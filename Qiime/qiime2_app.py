"""
QIIME2 Lab Desktop Tool
A GUI wrapper for QIIME2 workflows — 16S rRNA & Shotgun Metagenomics
Requires: PyQt6, qiime2 (installed in conda/mamba env), conda OR mamba
"""

import sys
import os
import subprocess
import json
import threading
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QComboBox, QTabWidget,
    QTextEdit, QProgressBar, QGroupBox, QLineEdit, QSpinBox,
    QDoubleSpinBox, QCheckBox, QScrollArea, QFrame, QSizePolicy,
    QMessageBox, QStatusBar, QSplitter
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon, QTextCursor


# ─── Worker Thread ────────────────────────────────────────────────────────────

class PipelineWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, pipeline_type, params, conda_env, output_dir, pkg_manager="mamba"):
        super().__init__()
        self.pipeline_type = pipeline_type
        self.params = params
        self.conda_env = conda_env
        self.output_dir = output_dir
        self.pkg_manager = pkg_manager   # "mamba", "conda", or "micromamba"
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self.pipeline_type == "16S rRNA Amplicon":
                self._run_16s_pipeline()
            elif self.pipeline_type == "Shotgun Metagenomics":
                self._run_shotgun_pipeline()
        except Exception as e:
            self.finished_signal.emit(False, str(e))

    def _run_cmd(self, cmd, step_label, progress_val):
        self.log_signal.emit(f"\n▶ {step_label}")
        self.log_signal.emit(f"  CMD: {' '.join(cmd)}\n")

        full_cmd = f"{self.pkg_manager} run -n {self.conda_env} " + " ".join(cmd)
        process = subprocess.Popen(
            full_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        for line in process.stdout:
            if self._cancelled:
                process.terminate()
                self.finished_signal.emit(False, "Pipeline cancelled by user.")
                return False
            self.log_signal.emit(line.rstrip())

        process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"Step '{step_label}' failed with code {process.returncode}")

        self.progress_signal.emit(progress_val)
        self.log_signal.emit(f"✔ {step_label} complete.\n")
        return True

    def _run_16s_pipeline(self):
        p = self.params
        out = self.output_dir
        os.makedirs(out, exist_ok=True)

        self.log_signal.emit("═" * 60)
        self.log_signal.emit("  QIIME 2 — 16S rRNA Amplicon Pipeline")
        self.log_signal.emit(f"  Package manager: {self.pkg_manager}")
        self.log_signal.emit(f"  Environment:     {self.conda_env}")
        self.log_signal.emit(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log_signal.emit("═" * 60)

        # Step 1: Import
        self._run_cmd([
            "qiime", "tools", "import",
            "--type", "SampleData[PairedEndSequencesWithQuality]",
            "--input-path", p["input_path"],
            "--input-format", "CasavaOneEightSingleLanePerSampleDirFmt",
            "--output-path", f"{out}/demux.qza"
        ], "Importing sequences", 15)

        # Step 2: Demux summary
        self._run_cmd([
            "qiime", "demux", "summarize",
            "--i-data", f"{out}/demux.qza",
            "--o-visualization", f"{out}/demux.qzv"
        ], "Summarizing demultiplexed data", 25)

        # Step 3: DADA2 denoise
        self._run_cmd([
            "qiime", "dada2", "denoise-paired",
            "--i-demultiplexed-seqs", f"{out}/demux.qza",
            "--p-trim-left-f", str(p.get("trim_left_f", 0)),
            "--p-trim-left-r", str(p.get("trim_left_r", 0)),
            "--p-trunc-len-f", str(p.get("trunc_len_f", 250)),
            "--p-trunc-len-r", str(p.get("trunc_len_r", 200)),
            "--p-n-threads", str(p.get("threads", 4)),
            "--o-table", f"{out}/table.qza",
            "--o-representative-sequences", f"{out}/rep-seqs.qza",
            "--o-denoising-stats", f"{out}/denoising-stats.qza"
        ], "DADA2 denoising", 50)

        # Step 4: Feature table summary
        self._run_cmd([
            "qiime", "feature-table", "summarize",
            "--i-table", f"{out}/table.qza",
            "--o-visualization", f"{out}/table.qzv",
            "--m-sample-metadata-file", p.get("metadata_path", "")
        ], "Feature table summary", 60) if p.get("metadata_path") else None

        # Step 5: Taxonomic classification
        self._run_cmd([
            "qiime", "feature-classifier", "classify-sklearn",
            "--i-classifier", p["classifier_path"],
            "--i-reads", f"{out}/rep-seqs.qza",
            "--p-n-jobs", str(p.get("threads", 4)),
            "--o-classification", f"{out}/taxonomy.qza"
        ], "Taxonomic classification", 75)

        # Step 6: Taxa bar plots
        self._run_cmd([
            "qiime", "taxa", "barplot",
            "--i-table", f"{out}/table.qza",
            "--i-taxonomy", f"{out}/taxonomy.qza",
            "--m-metadata-file", p.get("metadata_path", ""),
            "--o-visualization", f"{out}/taxa-barplot.qzv"
        ], "Generating taxa bar plots", 85) if p.get("metadata_path") else None

        # Step 7: Diversity analysis
        if p.get("metadata_path") and p.get("sampling_depth"):
            self._run_cmd([
                "qiime", "diversity", "core-metrics-phylogenetic",
                "--i-phylogeny", f"{out}/rooted-tree.qza",
                "--i-table", f"{out}/table.qza",
                "--p-sampling-depth", str(p["sampling_depth"]),
                "--m-metadata-file", p["metadata_path"],
                "--output-dir", f"{out}/core-metrics-results"
            ], "Core diversity metrics", 95)

        self.progress_signal.emit(100)
        self.log_signal.emit("\n" + "═" * 60)
        self.log_signal.emit("  ✅ Pipeline complete!")
        self.log_signal.emit(f"  Output saved to: {out}")
        self.log_signal.emit("═" * 60)
        self.finished_signal.emit(True, out)

    def _run_shotgun_pipeline(self):
        p = self.params
        out = self.output_dir
        os.makedirs(out, exist_ok=True)

        self.log_signal.emit("═" * 60)
        self.log_signal.emit("  QIIME 2 — Shotgun Metagenomics Pipeline")
        self.log_signal.emit(f"  Package manager: {self.pkg_manager}")
        self.log_signal.emit(f"  Environment:     {self.conda_env}")
        self.log_signal.emit(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log_signal.emit("═" * 60)

        # Step 1: Import
        self._run_cmd([
            "qiime", "tools", "import",
            "--type", "SampleData[PairedEndSequencesWithQuality]",
            "--input-path", p["input_path"],
            "--input-format", "CasavaOneEightSingleLanePerSampleDirFmt",
            "--output-path", f"{out}/reads.qza"
        ], "Importing reads", 10)

        # Step 2: Host removal (if reference provided)
        if p.get("host_ref"):
            self._run_cmd([
                "qiime", "quality-control", "filter-reads",
                "--i-demultiplexed-sequences", f"{out}/reads.qza",
                "--i-reference-sequences", p["host_ref"],
                "--o-filtered-sequences", f"{out}/reads-filtered.qza"
            ], "Host read removal", 25)
            reads_path = f"{out}/reads-filtered.qza"
        else:
            reads_path = f"{out}/reads.qza"

        # Step 3: Taxonomic profiling with Kraken2/Bracken via QIIME plugin
        self._run_cmd([
            "qiime", "moshpit", "classify-kraken2",
            "--i-seqs", reads_path,
            "--i-kraken2-db", p["kraken_db"],
            "--p-threads", str(p.get("threads", 4)),
            "--o-reports", f"{out}/kraken2-reports.qza",
            "--o-hits", f"{out}/kraken2-hits.qza"
        ], "Taxonomic profiling (Kraken2)", 50)

        # Step 4: Build feature table
        self._run_cmd([
            "qiime", "moshpit", "kraken2-to-features",
            "--i-reports", f"{out}/kraken2-reports.qza",
            "--o-table", f"{out}/table.qza",
            "--o-taxonomy", f"{out}/taxonomy.qza"
        ], "Building feature table from Kraken2 reports", 65)

        # Step 5: Diversity
        if p.get("metadata_path") and p.get("sampling_depth"):
            self._run_cmd([
                "qiime", "diversity", "core-metrics",
                "--i-table", f"{out}/table.qza",
                "--p-sampling-depth", str(p["sampling_depth"]),
                "--m-metadata-file", p["metadata_path"],
                "--output-dir", f"{out}/core-metrics-results"
            ], "Core diversity metrics", 85)

        # Step 6: Functional profiling (HUMAnN3 if available)
        self._run_cmd([
            "qiime", "moshpit", "run-humann",
            "--i-reads", reads_path,
            "--p-threads", str(p.get("threads", 4)),
            "--o-per-sample-outputs", f"{out}/humann-outputs.qza"
        ], "Functional profiling (HUMAnN3)", 95)

        self.progress_signal.emit(100)
        self.log_signal.emit("\n" + "═" * 60)
        self.log_signal.emit("  ✅ Pipeline complete!")
        self.log_signal.emit(f"  Output saved to: {out}")
        self.log_signal.emit("═" * 60)
        self.finished_signal.emit(True, out)


# ─── Main Window ──────────────────────────────────────────────────────────────

class QIIME2App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.setWindowTitle("QIIME2 Lab Tool")
        self.setMinimumSize(1100, 750)
        self._apply_theme()
        self._build_ui()

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #0f1117;
                color: #e8eaf0;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
            }
            QTabWidget::pane {
                border: 1px solid #2a2d3e;
                border-radius: 8px;
                background: #161922;
            }
            QTabBar::tab {
                background: #1e2130;
                color: #8b90a8;
                padding: 10px 24px;
                border-radius: 6px 6px 0 0;
                margin-right: 2px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background: #2563eb;
                color: #ffffff;
            }
            QGroupBox {
                border: 1px solid #2a2d3e;
                border-radius: 8px;
                margin-top: 12px;
                padding: 12px;
                font-weight: 600;
                color: #a0a8c8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background: #1e2130;
                border: 1px solid #2a2d3e;
                border-radius: 6px;
                padding: 7px 10px;
                color: #e8eaf0;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #2563eb;
            }
            QPushButton {
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-weight: 600;
            }
            QPushButton:hover { background: #1d4ed8; }
            QPushButton:disabled { background: #374151; color: #6b7280; }
            QPushButton#danger {
                background: #dc2626;
            }
            QPushButton#danger:hover { background: #b91c1c; }
            QPushButton#secondary {
                background: #1e2130;
                border: 1px solid #2a2d3e;
                color: #a0a8c8;
            }
            QPushButton#secondary:hover { background: #252a3d; }
            QTextEdit {
                background: #0a0c12;
                border: 1px solid #1e2130;
                border-radius: 6px;
                color: #a8ffb0;
                font-family: 'Cascadia Code', 'Consolas', monospace;
                font-size: 12px;
                padding: 8px;
            }
            QProgressBar {
                background: #1e2130;
                border-radius: 6px;
                height: 10px;
                text-align: center;
                color: white;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2563eb, stop:1 #7c3aed);
                border-radius: 6px;
            }
            QScrollBar:vertical {
                background: #1e2130;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #3a3f5c;
                border-radius: 4px;
            }
            QLabel#header {
                font-size: 22px;
                font-weight: 700;
                color: #ffffff;
            }
            QLabel#subtitle {
                font-size: 13px;
                color: #6b7280;
            }
            QLabel#badge {
                background: #1d3a6b;
                color: #60a5fa;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 600;
            }
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        header_bar = QWidget()
        header_bar.setFixedHeight(70)
        header_bar.setStyleSheet("background: #161922; border-bottom: 1px solid #2a2d3e;")
        hbl = QHBoxLayout(header_bar)
        hbl.setContentsMargins(24, 0, 24, 0)

        title = QLabel("🧬  QIIME2 Lab Tool")
        title.setObjectName("header")
        subtitle = QLabel("Microbiome Analysis Platform")
        subtitle.setObjectName("subtitle")

        badge = QLabel("v1.0 · Windows")
        badge.setObjectName("badge")

        hbl.addWidget(title)
        hbl.addSpacing(12)
        hbl.addWidget(subtitle)
        hbl.addStretch()
        hbl.addWidget(badge)
        root.addWidget(header_bar)

        # Main content
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(16)

        # Left panel — config
        left = QWidget()
        left.setMaximumWidth(420)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_16s_tab(), "16S rRNA")
        self.tabs.addTab(self._build_shotgun_tab(), "Shotgun")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        left_layout.addWidget(self.tabs)

        # Run controls
        run_box = QGroupBox("Run Pipeline")
        run_layout = QVBoxLayout(run_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        run_layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("▶  Run Pipeline")
        self.run_btn.setFixedHeight(40)
        self.run_btn.clicked.connect(self._run_pipeline)

        self.cancel_btn = QPushButton("■  Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setFixedHeight(40)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_pipeline)

        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.cancel_btn)
        run_layout.addLayout(btn_row)
        left_layout.addWidget(run_box)

        content_layout.addWidget(left)

        # Right panel — logs + output
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        log_header = QHBoxLayout()
        log_label = QLabel("Pipeline Log")
        log_label.setStyleSheet("font-weight: 700; font-size: 14px; color: #e8eaf0;")
        self.clear_log_btn = QPushButton("Clear")
        self.clear_log_btn.setObjectName("secondary")
        self.clear_log_btn.setFixedWidth(70)
        self.clear_log_btn.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(log_label)
        log_header.addStretch()
        log_header.addWidget(self.clear_log_btn)
        right_layout.addLayout(log_header)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Pipeline output will appear here...")
        right_layout.addWidget(self.log_view, stretch=1)

        # Output actions
        self.open_output_btn = QPushButton("📂  Open Output Folder")
        self.open_output_btn.setObjectName("secondary")
        self.open_output_btn.setEnabled(False)
        self.open_output_btn.clicked.connect(self._open_output_folder)
        right_layout.addWidget(self.open_output_btn)

        content_layout.addWidget(right, stretch=1)
        root.addWidget(content, stretch=1)

        # Status bar
        self.status = QStatusBar()
        self.status.setStyleSheet("background: #161922; color: #6b7280; border-top: 1px solid #2a2d3e;")
        self.setStatusBar(self.status)
        self.status.showMessage("Ready — configure your pipeline and press Run.")

    def _path_row(self, label, attr, is_dir=False):
        row = QHBoxLayout()
        field = QLineEdit()
        field.setPlaceholderText(f"Select {label}...")
        setattr(self, attr, field)
        btn = QPushButton("Browse")
        btn.setObjectName("secondary")
        btn.setFixedWidth(70)
        if is_dir:
            btn.clicked.connect(lambda: self._browse_dir(field))
        else:
            btn.clicked.connect(lambda: self._browse_file(field))
        row.addWidget(field)
        row.addWidget(btn)
        return row

    def _browse_dir(self, field):
        path = QFileDialog.getExistingDirectory(self, "Select Folder")
        if path:
            field.setText(path)

    def _browse_file(self, field, filters="All Files (*)"):
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "", filters)
        if path:
            field.setText(path)

    def _build_16s_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Input
        g1 = QGroupBox("Input Data")
        l1 = QVBoxLayout(g1)
        l1.addWidget(QLabel("Sequence folder (Casava format):"))
        l1.addLayout(self._path_row("input folder", "s16_input", is_dir=True))
        l1.addWidget(QLabel("Metadata file (.tsv):"))
        l1.addLayout(self._path_row("metadata", "s16_metadata"))
        layout.addWidget(g1)

        # Classifier
        g2 = QGroupBox("Classifier")
        l2 = QVBoxLayout(g2)
        l2.addWidget(QLabel("SILVA/GreenGenes classifier (.qza):"))
        l2.addLayout(self._path_row("classifier", "s16_classifier"))
        layout.addWidget(g2)

        # Params
        g3 = QGroupBox("DADA2 Parameters")
        l3 = QVBoxLayout(g3)

        def spin_row(label, attr, default, min_val=0, max_val=500):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            s = QSpinBox()
            s.setRange(min_val, max_val)
            s.setValue(default)
            setattr(self, attr, s)
            row.addWidget(s)
            return row

        l3.addLayout(spin_row("Trim left F:", "s16_trim_f", 0))
        l3.addLayout(spin_row("Trim left R:", "s16_trim_r", 0))
        l3.addLayout(spin_row("Trunc length F:", "s16_trunc_f", 250))
        l3.addLayout(spin_row("Trunc length R:", "s16_trunc_r", 200))
        layout.addWidget(g3)

        # Output
        g4 = QGroupBox("Output")
        l4 = QVBoxLayout(g4)
        l4.addWidget(QLabel("Output folder:"))
        l4.addLayout(self._path_row("output folder", "s16_output", is_dir=True))
        self.s16_sampling = QSpinBox()
        self.s16_sampling.setRange(0, 100000)
        self.s16_sampling.setValue(10000)
        sr = QHBoxLayout()
        sr.addWidget(QLabel("Sampling depth:"))
        sr.addWidget(self.s16_sampling)
        l4.addLayout(sr)
        layout.addWidget(g4)

        layout.addStretch()
        return w

    def _build_shotgun_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        g1 = QGroupBox("Input Data")
        l1 = QVBoxLayout(g1)
        l1.addWidget(QLabel("Sequence folder:"))
        l1.addLayout(self._path_row("input folder", "sg_input", is_dir=True))
        l1.addWidget(QLabel("Metadata file (.tsv):"))
        l1.addLayout(self._path_row("metadata", "sg_metadata"))
        layout.addWidget(g1)

        g2 = QGroupBox("Databases")
        l2 = QVBoxLayout(g2)
        l2.addWidget(QLabel("Kraken2 database folder:"))
        l2.addLayout(self._path_row("Kraken2 DB", "sg_kraken", is_dir=True))
        l2.addWidget(QLabel("Host reference (optional, for removal):"))
        l2.addLayout(self._path_row("host ref (.qza)", "sg_host"))
        layout.addWidget(g2)

        g3 = QGroupBox("Output")
        l3 = QVBoxLayout(g3)
        l3.addWidget(QLabel("Output folder:"))
        l3.addLayout(self._path_row("output folder", "sg_output", is_dir=True))
        self.sg_sampling = QSpinBox()
        self.sg_sampling.setRange(0, 1000000)
        self.sg_sampling.setValue(50000)
        sr = QHBoxLayout()
        sr.addWidget(QLabel("Sampling depth:"))
        sr.addWidget(self.sg_sampling)
        l3.addLayout(sr)
        layout.addWidget(g3)

        layout.addStretch()
        return w

    def _build_settings_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        g1 = QGroupBox("QIIME2 Environment")
        l1 = QVBoxLayout(g1)
        l1.addWidget(QLabel("Conda environment name:"))
        self.conda_env_field = QLineEdit("qiime2-amplicon-2024.10")
        l1.addWidget(self.conda_env_field)

        l1.addWidget(QLabel("Package manager:"))
        self.pkg_manager_combo = QComboBox()
        self.pkg_manager_combo.addItems(["mamba", "conda", "micromamba"])
        self.pkg_manager_combo.setCurrentText("mamba")
        self.pkg_manager_combo.setToolTip(
            "mamba   — use if 'conda' is not recognised (most common fix)\n"
            "conda   — use if conda is on your PATH\n"
            "micromamba — lightweight alternative"
        )
        l1.addWidget(self.pkg_manager_combo)

        tip = QLabel("💡 Getting 'conda not recognised'? Switch to mamba.")
        tip.setStyleSheet("color: #f59e0b; font-size: 11px;")
        tip.setWordWrap(True)
        l1.addWidget(tip)
        layout.addWidget(g1)

        g2 = QGroupBox("Compute")
        l2 = QVBoxLayout(g2)
        l2.addWidget(QLabel("Threads / CPUs:"))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 64)
        self.threads_spin.setValue(4)
        l2.addWidget(self.threads_spin)
        layout.addWidget(g2)

        g3 = QGroupBox("About")
        l3 = QVBoxLayout(g3)
        l3.addWidget(QLabel(
            "QIIME2 Lab Tool v1.0\n"
            "Built for Windows · 16S + Shotgun workflows\n\n"
            "Requires QIIME2 installed via mamba or conda."
        ))
        layout.addWidget(g3)

        layout.addStretch()
        return w

    def _run_pipeline(self):
        tab = self.tabs.currentIndex()
        conda_env  = self.conda_env_field.text().strip()
        pkg_manager = self.pkg_manager_combo.currentText().strip()

        if tab == 0:
            pipeline = "16S rRNA Amplicon"
            input_path = self.s16_input.text().strip()
            output_dir = self.s16_output.text().strip()

            if not input_path or not output_dir:
                QMessageBox.warning(self, "Missing Fields", "Please provide input and output paths.")
                return

            params = {
                "input_path": input_path,
                "metadata_path": self.s16_metadata.text().strip() or None,
                "classifier_path": self.s16_classifier.text().strip(),
                "trim_left_f": self.s16_trim_f.value(),
                "trim_left_r": self.s16_trim_r.value(),
                "trunc_len_f": self.s16_trunc_f.value(),
                "trunc_len_r": self.s16_trunc_r.value(),
                "sampling_depth": self.s16_sampling.value(),
                "threads": self.threads_spin.value(),
            }

        elif tab == 1:
            pipeline = "Shotgun Metagenomics"
            input_path = self.sg_input.text().strip()
            output_dir = self.sg_output.text().strip()

            if not input_path or not output_dir:
                QMessageBox.warning(self, "Missing Fields", "Please provide input and output paths.")
                return

            params = {
                "input_path": input_path,
                "metadata_path": self.sg_metadata.text().strip() or None,
                "kraken_db": self.sg_kraken.text().strip(),
                "host_ref": self.sg_host.text().strip() or None,
                "sampling_depth": self.sg_sampling.value(),
                "threads": self.threads_spin.value(),
            }
        else:
            return

        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.open_output_btn.setEnabled(False)
        self.status.showMessage(f"Running {pipeline} via {pkg_manager}...")
        self._last_output = output_dir

        self.worker = PipelineWorker(pipeline, params, conda_env, output_dir, pkg_manager)
        self.worker.log_signal.connect(self._append_log)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()

    def _cancel_pipeline(self):
        if self.worker:
            self.worker.cancel()
        self.cancel_btn.setEnabled(False)
        self.status.showMessage("Cancelling...")

    def _append_log(self, text):
        self.log_view.append(text)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _on_finished(self, success, msg):
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if success:
            self.open_output_btn.setEnabled(True)
            self.status.showMessage(f"✅ Pipeline complete. Output: {msg}")
            QMessageBox.information(self, "Done!", f"Pipeline finished successfully!\n\nOutput saved to:\n{msg}")
        else:
            self.status.showMessage(f"❌ Pipeline failed: {msg}")
            QMessageBox.critical(self, "Pipeline Error", f"An error occurred:\n\n{msg}")

    def _open_output_folder(self):
        if hasattr(self, "_last_output") and os.path.exists(self._last_output):
            os.startfile(self._last_output)


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = QIIME2App()
    window.show()
    sys.exit(app.exec())
