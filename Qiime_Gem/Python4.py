import sys
import subprocess
import time
import os
import tempfile
import shutil
import zipfile
import webbrowser
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import seaborn as sns
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTextEdit, QProgressBar, QGroupBox, QFileDialog,
                             QTabWidget, QTableWidget, QTableWidgetItem, QMessageBox)
from PyQt6.QtCore import QThread, pyqtSignal

# ---------------------------------------------------------
# UTILITY FUNCTIONS
# ---------------------------------------------------------
def to_wsl_path(windows_path):
    """Converts a standard Windows path to a WSL-friendly path."""
    if not windows_path:
        return ""
    wsl_path = windows_path.replace('\\', '/')
    if len(wsl_path) >= 3 and wsl_path[1] == ':' and wsl_path[2] == '/':
        drive_letter = wsl_path[0].lower()
        wsl_path = f"/mnt/{drive_letter}/" + wsl_path[3:]
    return wsl_path

# ---------------------------------------------------------
# BACKGROUND WORKER: QIIME 2 PIPELINE
# ---------------------------------------------------------
class PipelineThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool)

    def __init__(self, input_dir, output_dir, trunc_f, trunc_r):
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.trunc_f = trunc_f
        self.trunc_r = trunc_r
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.log_filepath = os.path.join(self.output_dir, f"qiime2_run_log_{timestamp}.txt")

    def run(self):
        self.log_signal.emit("[System] Initializing Offline Pipeline...")
        self.progress_signal.emit(5)
        
        wsl_input_dir = to_wsl_path(self.input_dir)
        wsl_output_dir = to_wsl_path(self.output_dir)
        os.makedirs(self.output_dir, exist_ok=True)

        qiime_pipeline = (
            "if [ -f ~/miniconda3/etc/profile.d/conda.sh ]; then source ~/miniconda3/etc/profile.d/conda.sh; "
            "elif [ -f ~/anaconda3/etc/profile.d/conda.sh ]; then source ~/anaconda3/etc/profile.d/conda.sh; "
            "elif [ -f ~/miniconda/etc/profile.d/conda.sh ]; then source ~/miniconda/etc/profile.d/conda.sh; "
            "else echo '[ERROR] Conda not found in standard directories.' && exit 1; fi && "
            
            "echo '[System] Step 1/4: Activating Environment...' && "
            "conda activate qiime2-amplicon-2024.2 && "
            
            "echo '[System] Step 2/4: Importing sequence data...' && "
            f"qiime tools import "
            f"--type 'SampleData[PairedEndSequencesWithQuality]' "
            f"--input-path '{wsl_input_dir}' "
            f"--input-format CasavaOneEightSingleLanePerSampleDirFmt "
            f"--output-path '{wsl_output_dir}/demux.qza' && "
            
            "echo '[System] Step 3/4: Running DADA2...' && "
            f"qiime dada2 denoise-paired "
            f"--i-demultiplexed-seqs '{wsl_output_dir}/demux.qza' "
            f"--p-trunc-len-f {self.trunc_f} "
            f"--p-trunc-len-r {self.trunc_r} "
            f"--o-table '{wsl_output_dir}/table.qza' "
            f"--o-representative-sequences '{wsl_output_dir}/rep-seqs.qza' "
            f"--o-denoising-stats '{wsl_output_dir}/denoising-stats.qza' && "
            
            "echo '[System] Step 4/4: Generating visual summaries...' && "
            f"qiime feature-table summarize "
            f"--i-table '{wsl_output_dir}/table.qza' "
            f"--o-visualization '{wsl_output_dir}/table_summary.qzv' && "
            
            "echo '[System] Pipeline completed successfully.'"
        )

        wsl_command = ["wsl", "-e", "bash", "-c", qiime_pipeline]
        success = False
        
        try:
            with open(self.log_filepath, "a", encoding="utf-8") as log_file:
                process = subprocess.Popen(wsl_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in process.stdout:
                    clean_line = line.strip()
                    if clean_line:
                        self.log_signal.emit(clean_line)
                        log_file.write(clean_line + "\n")
                        log_file.flush() 
                        
                        if "Step 1/4" in clean_line: self.progress_signal.emit(10)
                        elif "Step 2/4" in clean_line: self.progress_signal.emit(30)
                        elif "Step 3/4" in clean_line: self.progress_signal.emit(50)
                        elif "Step 4/4" in clean_line: self.progress_signal.emit(85)
                        elif "Pipeline completed" in clean_line: self.progress_signal.emit(100)
                process.wait()
                
                if process.returncode == 0:
                    self.log_signal.emit("\n[SUCCESS] Pipeline finished.")
                    success = True
                else:
                    self.log_signal.emit(f"\n[ERROR] Pipeline failed with exit code {process.returncode}.")
        except Exception as e:
            self.log_signal.emit(f"\n[FATAL ERROR] WSL failure: {str(e)}")
                
        self.finished_signal.emit(success)

# ---------------------------------------------------------
# BACKGROUND WORKER: ANALYTICS & PDF GENERATOR
# ---------------------------------------------------------
# ---------------------------------------------------------
# BACKGROUND WORKER: ANALYTICS & PDF GENERATOR
# ---------------------------------------------------------
class AnalyticsThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, output_dir):
        super().__init__()
        self.output_dir = output_dir

    def run(self):
        self.log_signal.emit("\n[Analytics] Starting automated data trend analysis...")
        temp_dir = tempfile.mkdtemp()
        wsl_temp_dir = to_wsl_path(temp_dir)
        wsl_output_dir = to_wsl_path(self.output_dir)

        try:
            # 1. Extract files via WSL
            self.log_signal.emit("[Analytics] Extracting DADA2 stats and Feature Table...")
            export_cmd = (
                "source ~/miniconda3/etc/profile.d/conda.sh && conda activate qiime2-amplicon-2024.2 && "
                f"qiime tools export --input-path '{wsl_output_dir}/denoising-stats.qza' --output-path '{wsl_temp_dir}/stats' && "
                f"qiime tools export --input-path '{wsl_output_dir}/table.qza' --output-path '{wsl_temp_dir}/table' && "
                f"biom convert -i '{wsl_temp_dir}/table/feature-table.biom' -o '{wsl_temp_dir}/table/feature-table.tsv' --to-tsv"
            )
            subprocess.run(["wsl", "-e", "bash", "-c", export_cmd], check=True, capture_output=True)

            # 2. Load into Pandas & CLEAN THE DATA
            stats_df = pd.read_csv(f"{temp_dir}/stats/stats.tsv", sep='\t')
            
            # --- THE FIX: Remove QIIME 2 types row and force numeric ---
            if not stats_df.empty and str(stats_df.iloc[0, 0]).startswith('#q2:'):
                stats_df = stats_df.drop(0) # Drop the directive row
                
            # Safely rename the first column (usually 'sample-id')
            stats_df.rename(columns={stats_df.columns[0]: 'Sample'}, inplace=True)
            stats_df.set_index('Sample', inplace=True)
            
            # Force the columns to be numbers, not strings
            for col in ['input', 'non-chimeric']:
                if col in stats_df.columns:
                    stats_df[col] = pd.to_numeric(stats_df[col], errors='coerce')

            # Process Feature Table
            table_df = pd.read_csv(f"{temp_dir}/table/feature-table.tsv", sep='\t', skiprows=1)
            table_df.rename(columns={table_df.columns[0]: 'Feature ID'}, inplace=True)
            table_df.set_index('Feature ID', inplace=True)
            
            # Force the feature table to be strictly numeric as a safety net
            table_df = table_df.apply(pd.to_numeric, errors='coerce')

            # 3. Compute Trends
            self.log_signal.emit("[Analytics] Crunching numbers and generating PDF report...")
            
            # Calculate feature richness per sample (number of non-zero features)
            richness = (table_df > 0).sum(axis=0)
            
            # 4. Generate PDF Report using Matplotlib
            pdf_path = os.path.join(self.output_dir, "QIIME2_Automated_Analytics_Report.pdf")
            sns.set_theme(style="whitegrid")
            
            with PdfPages(pdf_path) as pdf:
                # Plot 1: DADA2 Read Survival (Input vs Non-Chimeric)
                fig, ax = plt.subplots(figsize=(10, 6))
                stats_df[['input', 'non-chimeric']].plot(kind='bar', ax=ax, color=['#4C72B0', '#55A868'])
                ax.set_title("DADA2 Read Survival per Sample", fontsize=14, fontweight='bold')
                ax.set_ylabel("Number of Reads")
                ax.set_xlabel("Sample ID")
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                pdf.savefig(fig)
                plt.close()

                # Plot 2: Percentage of Reads Retained
                fig, ax = plt.subplots(figsize=(10, 6))
                retention = (stats_df['non-chimeric'] / stats_df['input']) * 100
                retention.plot(kind='bar', ax=ax, color='#C44E52')
                ax.set_title("Percentage of Reads Retained After DADA2 Filtering", fontsize=14, fontweight='bold')
                ax.set_ylabel("Retention (%)")
                ax.set_xlabel("Sample ID")
                ax.axhline(y=70, color='r', linestyle='--', label='70% Warning Threshold')
                ax.legend()
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                pdf.savefig(fig)
                plt.close()

                # Plot 3: ASV/Feature Richness
                fig, ax = plt.subplots(figsize=(10, 6))
                richness.plot(kind='bar', ax=ax, color='#8172B3')
                ax.set_title("Feature (ASV) Richness per Sample", fontsize=14, fontweight='bold')
                ax.set_ylabel("Number of Unique Features")
                ax.set_xlabel("Sample ID")
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                pdf.savefig(fig)
                plt.close()

            self.log_signal.emit(f"[Analytics] SUCCESS! PDF Report saved to:\n{pdf_path}")

        except Exception as e:
            self.log_signal.emit(f"[Analytics ERROR] Failed to generate analytics: {str(e)}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.finished_signal.emit()

# ---------------------------------------------------------
# MAIN WINDOW UI
# ---------------------------------------------------------
class Qiime2App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QIIME2 Offline Lab Tool (with Auto-Analytics)")
        self.resize(1200, 750)
        self.setStyleSheet("background-color: #1e1e2e; color: #cdd6f4;")
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        left_panel = QVBoxLayout()
        
        # UI Setup
        self.setup_input_group(left_panel)
        self.setup_output_group(left_panel)
        self.setup_dada_group(left_panel)
        left_panel.addStretch()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid #585b70; border-radius: 5px; text-align: center; } QProgressBar::chunk { background-color: #a6e3a1; }")
        
        # New Single-Click Button
        self.run_btn = QPushButton("▶ Run Pipeline & Generate Analytics PDF")
        self.run_btn.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 5px;")
        self.run_btn.clicked.connect(self.start_master_process)
        
        left_panel.addWidget(self.progress_bar)
        left_panel.addWidget(self.run_btn)
        
        # Tabs for Log and Viewer
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabBar::tab { background: #313244; color: #cdd6f4; padding: 8px 20px; border: 1px solid #45475a; } QTabBar::tab:selected { background: #45475a; font-weight: bold; } QTabWidget::pane { border: 1px solid #45475a; }")

        self.setup_log_tab()
        self.setup_viewer_tab()
        
        main_layout.addLayout(left_panel, 1)
        main_layout.addWidget(self.tabs, 2)

    def setup_input_group(self, layout):
        group = QGroupBox("1. Input Data")
        group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #45475a; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        vbox = QVBoxLayout()
        self.seq_folder_input = QLineEdit()
        self.seq_folder_input.setStyleSheet("background-color: #313244; padding: 5px; border: 1px solid #585b70;")
        btn = QPushButton("Browse")
        btn.setStyleSheet("background-color: #45475a; padding: 5px;")
        btn.clicked.connect(lambda: self.browse_folder(self.seq_folder_input))
        vbox.addWidget(QLabel("Sequence Directory:"))
        vbox.addWidget(self.seq_folder_input)
        vbox.addWidget(btn)
        group.setLayout(vbox)
        layout.addWidget(group)

    def setup_output_group(self, layout):
        group = QGroupBox("2. Output Destination")
        group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #45475a; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        vbox = QVBoxLayout()
        self.out_folder_input = QLineEdit()
        self.out_folder_input.setStyleSheet("background-color: #313244; padding: 5px; border: 1px solid #585b70;")
        btn = QPushButton("Browse")
        btn.setStyleSheet("background-color: #45475a; padding: 5px;")
        btn.clicked.connect(lambda: self.browse_folder(self.out_folder_input))
        vbox.addWidget(QLabel("Output Directory:"))
        vbox.addWidget(self.out_folder_input)
        vbox.addWidget(btn)
        group.setLayout(vbox)
        layout.addWidget(group)

    def setup_dada_group(self, layout):
        group = QGroupBox("3. DADA2 Parameters")
        group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #45475a; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        vbox = QVBoxLayout()
        self.trunc_f_input = QLineEdit("0")
        self.trunc_r_input = QLineEdit("0")
        self.trunc_f_input.setStyleSheet("background-color: #313244; padding: 5px; border: 1px solid #585b70;")
        self.trunc_r_input.setStyleSheet("background-color: #313244; padding: 5px; border: 1px solid #585b70;")
        vbox.addWidget(QLabel("Trunc length Forward (F):"))
        vbox.addWidget(self.trunc_f_input)
        vbox.addWidget(QLabel("Trunc length Reverse (R):"))
        vbox.addWidget(self.trunc_r_input)
        group.setLayout(vbox)
        layout.addWidget(group)

    def setup_log_tab(self):
        self.log_tab = QWidget()
        vbox = QVBoxLayout(self.log_tab)
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("background-color: #11111b; color: #a6adc8; font-family: Consolas, monospace; border: none;")
        vbox.addWidget(self.log_console)
        self.tabs.addTab(self.log_tab, "Terminal Log")

    def setup_viewer_tab(self):
        self.viewer_tab = QWidget()
        vbox = QVBoxLayout(self.viewer_tab)
        hbox = QHBoxLayout()
        btn = QPushButton("📂 Load .qza / .qzv File")
        btn.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 8px; border-radius: 3px;")
        btn.clicked.connect(self.handle_file_load)
        self.current_file_label = QLabel("No file loaded.")
        hbox.addWidget(btn)
        hbox.addWidget(self.current_file_label)
        hbox.addStretch()
        
        self.data_table = QTableWidget()
        self.data_table.setStyleSheet("background-color: #181825; alternate-background-color: #1e1e2e; gridline-color: #45475a; border: none;")
        self.data_table.setAlternatingRowColors(True)
        
        vbox.addLayout(hbox)
        vbox.addWidget(self.data_table)
        self.tabs.addTab(self.viewer_tab, "Data Viewer")

    def browse_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "Select Directory")
        if folder:
            line_edit.setText(folder)

    # --- PROCESS MANAGEMENT ---
    def start_master_process(self):
        if not self.seq_folder_input.text() or not self.out_folder_input.text():
            self.append_log("[WARNING] Please select both Input and Output folders.")
            return

        self.run_btn.setEnabled(False)
        self.run_btn.setText("Processing Pipeline & Analytics...")
        self.run_btn.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 5px;")
        self.log_console.clear()
        self.tabs.setCurrentIndex(0)

        # Start Pipeline Thread first
        self.pipe_thread = PipelineThread(self.seq_folder_input.text(), self.out_folder_input.text(), self.trunc_f_input.text(), self.trunc_r_input.text())
        self.pipe_thread.log_signal.connect(self.append_log)
        self.pipe_thread.progress_signal.connect(self.progress_bar.setValue)
        self.pipe_thread.finished_signal.connect(self.trigger_analytics)
        self.pipe_thread.start()

    def trigger_analytics(self, success):
        if success:
            # If pipeline succeeded, run the analytics thread
            self.analytics_thread = AnalyticsThread(self.out_folder_input.text())
            self.analytics_thread.log_signal.connect(self.append_log)
            self.analytics_thread.finished_signal.connect(self.reset_ui)
            self.analytics_thread.start()
        else:
            self.reset_ui()

    def reset_ui(self):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("▶ Run Pipeline & Generate Analytics PDF")
        self.run_btn.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 5px;")

    def append_log(self, text):
        self.log_console.append(text)
        scrollbar = self.log_console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # --- VIEWER LOGIC (Simplified from previous implementation) ---
    def handle_file_load(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open File", "", "QIIME 2 Files (*.qza *.qzv)")
        if not filepath: return
        if filepath.endswith('.qzv'):
            self.open_qzv_offline(filepath)
        elif filepath.endswith('.qza'):
            self.load_qza_table(filepath)

    def open_qzv_offline(self, filepath):
        extract_dir = tempfile.mkdtemp(prefix="q2_")
        try:
            with zipfile.ZipFile(filepath, 'r') as z: z.extractall(extract_dir)
            uuid_folder = [f for f in os.listdir(extract_dir) if os.path.isdir(os.path.join(extract_dir, f))][0]
            html_path = os.path.join(extract_dir, uuid_folder, "data", "index.html")
            if os.path.exists(html_path): webbrowser.open(f"file://{html_path}")
        except Exception as e:
            self.append_log(f"Error opening QZV: {e}")

    def load_qza_table(self, filepath):
        temp_dir = tempfile.mkdtemp()
        wsl_filepath = to_wsl_path(filepath)
        wsl_temp = to_wsl_path(temp_dir)
        try:
            subprocess.run(["wsl", "-e", "bash", "-c", f"source ~/miniconda3/etc/profile.d/conda.sh && conda activate qiime2-amplicon-2024.2 && qiime tools export --input-path '{wsl_filepath}' --output-path '{wsl_temp}'"], check=True)
            files = os.listdir(temp_dir)
            if any(f.endswith('.biom') for f in files):
                biom = [f for f in files if f.endswith('.biom')][0]
                subprocess.run(["wsl", "-e", "bash", "-c", f"source ~/miniconda3/etc/profile.d/conda.sh && conda activate qiime2-amplicon-2024.2 && biom convert -i '{wsl_temp}/{biom}' -o '{wsl_temp}/table.tsv' --to-tsv"], check=True)
                df = pd.read_csv(f"{temp_dir}/table.tsv", sep='\t', skiprows=1)
            else:
                tsv = [f for f in files if f.endswith('.tsv')][0]
                df = pd.read_csv(f"{temp_dir}/{tsv}", sep='\t')
            
            self.data_table.clear()
            self.data_table.setRowCount(df.shape[0])
            self.data_table.setColumnCount(df.shape[1])
            self.data_table.setHorizontalHeaderLabels(df.columns.astype(str))
            for i in range(df.shape[0]):
                for j in range(df.shape[1]):
                    self.data_table.setItem(i, j, QTableWidgetItem(str(df.iat[i, j])))
            self.current_file_label.setText(f"Loaded: {os.path.basename(filepath)}")
        except Exception as e:
            self.append_log(f"Error loading QZA: {e}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Qiime2App()
    window.show()
    sys.exit(app.exec())