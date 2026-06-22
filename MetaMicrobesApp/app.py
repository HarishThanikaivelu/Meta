import sys
import os
import subprocess
import pandas as pd
import eurofins_pipeline
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTextEdit, QProgressBar, QFileDialog, QTabWidget,
                             QComboBox, QSpinBox, QMessageBox)
from PyQt6.QtCore import QThread, pyqtSignal, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView

class UnifiedAnalysisThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool)
    html_ready_signal = pyqtSignal(str)

    def __init__(self, sample_dir, classifier_file, trunc_f, trunc_r, work_dir, region, title, res_dir, num_es, es1_name, es1_count, es2_name="", es2_count=0):
        super().__init__()
        self.sample_dir = sample_dir
        self.classifier_file = classifier_file
        self.trunc_f = trunc_f
        self.trunc_r = trunc_r
        self.work_dir = work_dir
        self.region = region
        self.title = title
        self.res_dir = res_dir
        self.num_es = num_es
        self.es1_name = es1_name
        self.es1_count = es1_count
        self.es2_name = es2_name
        self.es2_count = es2_count

    def to_wsl_path(self, windows_path):
        if not windows_path: return ""
        wsl_path = windows_path.replace('\\', '/')
        if len(wsl_path) >= 3 and wsl_path[1] == ':' and wsl_path[2] == '/':
            drive = wsl_path[0].lower()
            return f"/mnt/{drive}/" + wsl_path[3:]
        return wsl_path

    def format_qiime_data(self, r_analyses_dir):
        """Merges exported feature-table and taxonomy into the required Complete_16S_data structure."""
        # 1. Load Feature Table
        table_path = os.path.join(r_analyses_dir, "feature-table.tsv")
        df_table = pd.read_csv(table_path, sep='\t', skiprows=1)
        df_table = df_table.rename(columns={'#OTU ID': 'mASV_ID'})
        
        # 2. Load Taxonomy
        tax_path = os.path.join(r_analyses_dir, "taxonomy.tsv")
        df_tax = pd.read_csv(tax_path, sep='\t')
        
        # 3. Merge on Feature ID
        df_merged = pd.merge(df_tax, df_table, left_on='Feature ID', right_on='mASV_ID', how='inner')
        
        # 4. Extract Taxonomic Levels 
        tax_levels = ['domain', 'phylum', 'class', 'order', 'family', 'genus', 'species']
        
        # Vectorized regex extraction based on standard QIIME string output
        df_merged['domain'] = df_merged['Taxon'].str.extract(r'[dk]__([^;]+)')
        df_merged['phylum'] = df_merged['Taxon'].str.extract(r'p__([^;]+)')
        df_merged['class']  = df_merged['Taxon'].str.extract(r'c__([^;]+)')
        df_merged['order']  = df_merged['Taxon'].str.extract(r'o__([^;]+)')
        df_merged['family'] = df_merged['Taxon'].str.extract(r'f__([^;]+)')
        df_merged['genus']  = df_merged['Taxon'].str.extract(r'g__([^;]+)')
        df_merged['species']= df_merged['Taxon'].str.extract(r's__([^;]+)')
        
        # 5. Clean up NAs and whitespace
        for lvl in tax_levels:
            df_merged[lvl] = df_merged[lvl].fillna('not_classified')
            df_merged[lvl] = df_merged[lvl].str.strip()
            
        # 6. Drop raw QIIME columns and reorder
        df_merged = df_merged.drop(columns=['Feature ID', 'Taxon', 'Confidence'], errors='ignore')
        
        sample_cols = [c for c in df_merged.columns if c not in ['mASV_ID'] + tax_levels]
        final_cols = ['mASV_ID', 'domain', 'phylum', 'class', 'order', 'family', 'genus', 'species'] + sample_cols
        df_merged = df_merged[final_cols]
        
        # 7. Export the final merged CSV
        out_path = os.path.join(r_analyses_dir, f"Complete_{self.region}_data.csv")
        df_merged.to_csv(out_path, index=False)

    def run(self):
        self.log_signal.emit(f"[System] PHASE 1: Starting QIIME 2 Execution...")
        self.progress_signal.emit(5)
        
        wsl_sample_dir = self.to_wsl_path(self.sample_dir)
        wsl_classifier = self.to_wsl_path(self.classifier_file)
        wsl_work_dir = self.to_wsl_path(self.work_dir)
        
        r_analyses_dir = os.path.join(self.work_dir, "R_analyses")
        os.makedirs(r_analyses_dir, exist_ok=True)
        wsl_r_analyses = self.to_wsl_path(r_analyses_dir)

        # IMPORTANT: Change this string to match your exact QIIME 2 Conda environment name!
        qiime_env = "qiime2-amplicon-2024.2" 

        qiime_commands = [
            # Import data
            f'wsl bash -ic "conda activate {qiime_env} && qiime tools import --type \'SampleData[PairedEndSequencesWithQuality]\' --input-path \'{wsl_sample_dir}\' --input-format CasavaOneEightSingleLanePerSampleDirFmt --output-path \'{wsl_work_dir}/demux.qza\'"',
            
            # Denoise via DADA2
            f'wsl bash -ic "conda activate {qiime_env} && qiime dada2 denoise-paired --i-demultiplexed-seqs \'{wsl_work_dir}/demux.qza\' --p-trunc-len-f {self.trunc_f} --p-trunc-len-r {self.trunc_r} --o-table \'{wsl_work_dir}/table.qza\' --o-representative-sequences \'{wsl_work_dir}/rep-seqs.qza\' --o-denoising-stats \'{wsl_work_dir}/stats.qza\'"',
            
            # Classify Taxonomy
            f'wsl bash -ic "conda activate {qiime_env} && qiime feature-classifier classify-sklearn --i-classifier \'{wsl_classifier}\' --i-reads \'{wsl_work_dir}/rep-seqs.qza\' --o-classification \'{wsl_work_dir}/taxonomy.qza\'"',
            
            # Export Feature Table
            f'wsl bash -ic "conda activate {qiime_env} && qiime tools export --input-path \'{wsl_work_dir}/table.qza\' --output-path \'{wsl_r_analyses}\'"',
            
            # Export Taxonomy
            f'wsl bash -ic "conda activate {qiime_env} && qiime tools export --input-path \'{wsl_work_dir}/taxonomy.qza\' --output-path \'{wsl_r_analyses}\'"',
            
            # Convert BIOM to TSV
            f'wsl bash -ic "conda activate {qiime_env} && biom convert -i \'{wsl_r_analyses}/feature-table.biom\' -o \'{wsl_r_analyses}/feature-table.tsv\' --to-tsv"'
        ]

        for idx, cmd in enumerate(qiime_commands):
            # Cleanly print which step is running
            step_name = cmd.split('&&')[1].split()[1:3]
            self.log_signal.emit(f"\n[Running Command {idx+1}/{len(qiime_commands)}] {' '.join(step_name)}...")
            try:
                process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                for line in process.stdout:
                    self.log_signal.emit(line.strip())
                
                process.wait()
                if process.returncode != 0:
                    self.log_signal.emit(f"\n[FATAL ERROR] QIIME 2 Process {idx+1} failed with exit code {process.returncode}")
                    self.finished_signal.emit(False)
                    return
            except Exception as e:
                self.log_signal.emit(f"\n[FATAL ERROR] Subprocess Failed: {str(e)}")
                self.finished_signal.emit(False)
                return

        self.log_signal.emit("\n[System] QIIME 2 Processing Complete. Formatting final taxonomy table...")
        try:
            self.format_qiime_data(r_analyses_dir)
        except Exception as e:
            self.log_signal.emit(f"\n[FATAL ERROR] Could not format final QIIME table: {str(e)}")
            self.finished_signal.emit(False)
            return

        self.progress_signal.emit(40)

        # ==========================================
        # PHASE 2: Python Data Visualization Pipeline
        # ==========================================
        self.log_signal.emit(f"\n[System] PHASE 2: Initiating Native Python Pipeline for {self.title}...")
        self.progress_signal.emit(60)

        try:
            report_path = eurofins_pipeline.run_pipeline(
                work_dir=self.work_dir,
                region=self.region,
                title=self.title,
                res_dir=self.res_dir,
                num_es=self.num_es,
                es1_name=self.es1_name,
                es1_count=self.es1_count,
                es2_name=self.es2_name,
                es2_count=self.es2_count,
                log_callback=self.log_signal.emit
            )
            
            self.log_signal.emit(f"\n[SUCCESS] Pipeline completed end-to-end. Report generated at:\n{report_path}")
            success = True
            self.html_ready_signal.emit(report_path)
            
        except Exception as e:
            self.log_signal.emit(f"\n[FATAL ERROR] Python Pipeline Failed: {str(e)}")
            success = False
            
        self.progress_signal.emit(100)
        self.finished_signal.emit(success)

class UnifiedLabTool(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Meta-Microbes End-to-End Pipeline")
        self.setMinimumSize(1200, 750)
        self.setStyleSheet("background-color: #1e1e2e; color: #cdd6f4; font-family: 'Segoe UI'; font-size: 13px;")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(25)

        left_panel = QVBoxLayout()
        self.settings_tabs = QTabWidget()
        self.settings_tabs.setFixedWidth(450)
        self.settings_tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #45475a; background: #181825; border-radius: 8px; }
            QTabBar::tab { background: #313244; padding: 10px 20px; margin-right: 2px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #a6e3a1; color: #11111b; font-weight: bold; }
        """)

        tab_q2 = QWidget()
        q2_layout = QVBoxLayout(tab_q2)
        q2_layout.setSpacing(15)
        self.sample_dir = self.add_path_input(q2_layout, "Raw Sample Directory:", "The folder containing your raw .fastq.gz files.")
        self.classifier_file = self.add_file_input(q2_layout, "Classifier File (.qza):", "Pre-trained QIIME 2 taxonomy classifier.")
        self.trunc_f = self.add_spin_input(q2_layout, "Trunc length (Forward):", 0, "Cutoff for forward reads.")
        self.trunc_r = self.add_spin_input(q2_layout, "Trunc length (Reverse):", 0, "Cutoff for reverse reads.")
        q2_layout.addStretch()
        self.settings_tabs.addTab(tab_q2, "QIIME 2 Denoising")

        tab_euro = QWidget()
        euro_layout = QVBoxLayout(tab_euro)
        euro_layout.setSpacing(15)

        self.work_dir = self.add_path_input(euro_layout, "Working Directory:", "Main project folder.")
        self.res_dir = self.add_path_input(euro_layout, "Resources Directory:", "The folder containing 'Food pathogens list.csv'.")
        self.title_input = self.add_text_input(euro_layout, "Report Title:", "Batch_01", "Naming prefix for reports.")
        
        reg_layout = QHBoxLayout()
        reg_lbl = QLabel("Target Region:")
        reg_lbl.setStyleSheet("font-weight: bold;")
        self.region_combo = QComboBox()
        self.region_combo.addItems(["16S", "ITS"])
        self.region_combo.setStyleSheet("background-color: #313244; padding: 6px; border-radius: 4px;")
        reg_layout.addWidget(reg_lbl)
        reg_layout.addStretch()
        reg_layout.addWidget(self.region_combo)
        euro_layout.addLayout(reg_layout)

        self.num_es_combo = QComboBox()
        self.num_es_combo.addItems(["0", "1", "2"])
        self.num_es_combo.currentTextChanged.connect(self.toggle_es2)
        euro_layout.addWidget(QLabel("Number of Enumeration Spikes (Set 0 for Proportions):"))
        euro_layout.addWidget(self.num_es_combo)

        self.es1_name = self.add_text_input(euro_layout, "ES 1 Name:", "Allobacillus_halotolerans")
        self.es1_count = self.add_spin_input(euro_layout, "ES 1 Cells:", 20000)
        self.es2_name = self.add_text_input(euro_layout, "ES 2 Name:", "Imtechella_halotolerans")
        self.es2_count = self.add_spin_input(euro_layout, "ES 2 Cells:", 20000)
        self.toggle_es2("0") 

        euro_layout.addStretch()
        self.settings_tabs.addTab(tab_euro, "Data Analysis")

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid #45475a; border-radius: 6px; background: #313244; text-align: center; } QProgressBar::chunk { background-color: #a6e3a1; border-radius: 5px; }")
        
        self.run_btn = QPushButton("▶ Execute Full Pipeline")
        self.run_btn.setStyleSheet("QPushButton { background-color: #a6e3a1; color: #11111b; font-weight: bold; font-size: 15px; padding: 15px; border-radius: 8px; } QPushButton:hover { background-color: #94e2d5; } QPushButton:disabled { background-color: #585b70; }")
        self.run_btn.clicked.connect(self.start_pipeline)

        left_panel.addWidget(self.settings_tabs)
        left_panel.addWidget(self.progress_bar)
        left_panel.addWidget(self.run_btn)

        self.main_tabs = QTabWidget()
        self.main_tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #45475a; background: #11111b; border-radius: 8px; }
            QTabBar::tab { background: #313244; padding: 12px 25px; margin-right: 2px; border-top-left-radius: 8px; border-top-right-radius: 8px; }
            QTabBar::tab:selected { background: #45475a; border-bottom: 2px solid #89b4fa; font-weight: bold; }
        """)

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("background-color: #11111b; color: #a6adc8; font-family: 'Consolas'; border: none; padding: 10px;")
        
        self.browser = QWebEngineView()
        self.browser.setHtml("<body style='background-color:#11111b; color:#585b70; display:flex; justify-content:center; align-items:center; height:90vh; font-family: sans-serif;'><h2>Run pipeline to generate visualizations...</h2></body>")

        self.main_tabs.addTab(self.log_console, "Terminal Log")
        self.main_tabs.addTab(self.browser, "Interactive Viewer")
        
        main_layout.addLayout(left_panel)
        main_layout.addWidget(self.main_tabs, stretch=1)

    def add_path_input(self, layout, label_text, help_text=""):
        row = QVBoxLayout(); row.setSpacing(5); lbl = QLabel(label_text); lbl.setStyleSheet("font-weight: bold; color: #bac2de;")
        edit_row = QHBoxLayout(); edit = QLineEdit(); edit.setStyleSheet("background-color: #313244; padding: 8px; border-radius: 4px; color: #cdd6f4;")
        btn = QPushButton("Browse"); btn.setStyleSheet("background-color: #45475a; padding: 8px 15px; border-radius: 4px; font-weight: bold;")
        btn.clicked.connect(lambda: edit.setText(QFileDialog.getExistingDirectory(self, "Select Directory")))
        edit_row.addWidget(edit); edit_row.addWidget(btn); row.addWidget(lbl); row.addLayout(edit_row); layout.addLayout(row)
        return edit

    def add_file_input(self, layout, label_text, help_text=""):
        row = QVBoxLayout(); row.setSpacing(5); lbl = QLabel(label_text); lbl.setStyleSheet("font-weight: bold; color: #bac2de;")
        edit_row = QHBoxLayout(); edit = QLineEdit(); edit.setStyleSheet("background-color: #313244; padding: 8px; border-radius: 4px; color: #cdd6f4;")
        btn = QPushButton("Browse"); btn.setStyleSheet("background-color: #45475a; padding: 8px 15px; border-radius: 4px; font-weight: bold;")
        btn.clicked.connect(lambda: edit.setText(QFileDialog.getOpenFileName(self, "Select File", "", "QIIME Artifact (*.qza);;All Files (*)")[0]))
        edit_row.addWidget(edit); edit_row.addWidget(btn); row.addWidget(lbl); row.addLayout(edit_row); layout.addLayout(row)
        return edit

    def add_text_input(self, layout, label_text, default_text="", help_text=""):
        row = QHBoxLayout(); lbl = QLabel(label_text); lbl.setStyleSheet("font-weight: bold; color: #bac2de;")
        edit = QLineEdit(default_text); edit.setStyleSheet("background-color: #313244; padding: 6px; border-radius: 4px; color: #cdd6f4;")
        row.addWidget(lbl); row.addStretch(); row.addWidget(edit); layout.addLayout(row)
        return edit

    def add_spin_input(self, layout, label_text, default_val, help_text=""):
        row = QHBoxLayout(); lbl = QLabel(label_text); lbl.setStyleSheet("font-weight: bold; color: #bac2de;")
        spin = QSpinBox(); spin.setMaximum(10000000); spin.setValue(default_val); spin.setStyleSheet("background-color: #313244; padding: 6px; border-radius: 4px; color: #cdd6f4;")
        row.addWidget(lbl); row.addStretch(); row.addWidget(spin); layout.addLayout(row)
        return spin

    def toggle_es2(self, value):
        es1_visible = (value in ["1", "2"])
        es2_visible = (value == "2")
        self.es1_name.parentWidget().setVisible(es1_visible)
        self.es1_count.parentWidget().setVisible(es1_visible)
        self.es2_name.parentWidget().setVisible(es2_visible)
        self.es2_count.parentWidget().setVisible(es2_visible)

    def load_html(self, file_path):
        local_url = QUrl.fromLocalFile(file_path)
        self.browser.load(local_url)
        self.main_tabs.setCurrentIndex(1)

    def start_pipeline(self):
        if not self.sample_dir.text() or not self.work_dir.text() or not self.res_dir.text() or not self.classifier_file.text():
            QMessageBox.warning(self, "Missing Paths", "Please provide Sample, Classifier, Working, and Resource directories.")
            return
        
        self.run_btn.setEnabled(False)
        self.log_console.clear()
        self.main_tabs.setCurrentIndex(0) 
        num_es = int(self.num_es_combo.currentText())
        
        self.thread = UnifiedAnalysisThread(
            sample_dir=self.sample_dir.text(), classifier_file=self.classifier_file.text(),
            trunc_f=self.trunc_f.value(), trunc_r=self.trunc_r.value(),
            work_dir=self.work_dir.text(), region=self.region_combo.currentText(), title=self.title_input.text(),
            res_dir=self.res_dir.text(), num_es=num_es, es1_name=self.es1_name.text(), es1_count=self.es1_count.value(),
            es2_name=self.es2_name.text() if num_es == 2 else "", es2_count=self.es2_count.value() if num_es == 2 else 0
        )
        
        self.thread.log_signal.connect(self.log_console.append)
        self.thread.progress_signal.connect(self.progress_bar.setValue)
        self.thread.finished_signal.connect(lambda: self.run_btn.setEnabled(True))
        self.thread.html_ready_signal.connect(self.load_html)
        self.thread.start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = UnifiedLabTool()
    window.show()
    sys.exit(app.exec())