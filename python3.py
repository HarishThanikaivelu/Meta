import sys
import subprocess
import time
import os
import tempfile
import shutil
import zipfile
import webbrowser
import pandas as pd
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
    
    # First, normalize all backslashes to forward slashes
    wsl_path = windows_path.replace('\\', '/')
    
    # Check if the path starts with a drive letter (e.g., "C:/", "D:/")
    # This converts "C:/Users/..." to "/mnt/c/Users/..."
    if len(wsl_path) >= 3 and wsl_path[1] == ':' and wsl_path[2] == '/':
        drive_letter = wsl_path[0].lower()
        wsl_path = f"/mnt/{drive_letter}/" + wsl_path[3:]
        
    return wsl_path

# ---------------------------------------------------------
# BACKGROUND WORKER THREAD (OFFLINE PIPELINE)
# ---------------------------------------------------------
class PipelineThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal()

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
        
        self.log_signal.emit(f"[System] Mapped Input: {wsl_input_dir}")
        self.log_signal.emit(f"[System] Mapped Output: {wsl_output_dir}")
        self.log_signal.emit(f"[System] Run log will be saved to: {self.log_filepath}")
        
        os.makedirs(self.output_dir, exist_ok=True)

        # OFFLINE PIPELINE: Removed conda update to prevent internet checks
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
            
            "echo '[System] Step 3/4: Running DADA2 (This may take a while)...' && "
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
        
        try:
            with open(self.log_filepath, "a", encoding="utf-8") as log_file:
                log_file.write(f"--- QIIME2 Run Started: {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                log_file.write(f"Input Directory: {self.input_dir}\n")
                log_file.write(f"Output Directory: {self.output_dir}\n")
                log_file.write(f"Trunc F: {self.trunc_f} | Trunc R: {self.trunc_r}\n\n")
                
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
                    success_msg = "\n[SUCCESS] Pipeline finished. Check your output folder for the .qza/.qzv files."
                    self.log_signal.emit(success_msg)
                    log_file.write(success_msg + "\n")
                else:
                    error_msg = f"\n[ERROR] Pipeline failed with exit code {process.returncode}."
                    self.log_signal.emit(error_msg)
                    log_file.write(error_msg + "\n")
                
        except Exception as e:
            fatal_msg = f"\n[FATAL ERROR] Failed to communicate with WSL: {str(e)}"
            self.log_signal.emit(fatal_msg)
            try:
                with open(self.log_filepath, "a", encoding="utf-8") as log_file:
                    log_file.write(fatal_msg + "\n")
            except:
                pass
                
        self.finished_signal.emit()

# ---------------------------------------------------------
# MAIN WINDOW UI
# ---------------------------------------------------------
class Qiime2App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QIIME2 Offline Lab Tool")
        self.resize(1200, 750)
        self.setStyleSheet("background-color: #1e1e2e; color: #cdd6f4;")
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # --- LEFT PANEL (Controls) ---
        left_panel = QVBoxLayout()
        
        # 1. Input Data Group
        input_group = QGroupBox("Input Data")
        input_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #45475a; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        input_layout = QVBoxLayout()
        
        self.seq_folder_input = QLineEdit()
        self.seq_folder_input.setPlaceholderText("Sequence folder (Casava format)")
        self.seq_folder_input.setStyleSheet("background-color: #313244; padding: 5px; border: 1px solid #585b70;")
        browse_seq_btn = QPushButton("Browse")
        browse_seq_btn.setStyleSheet("background-color: #45475a; padding: 5px;")
        browse_seq_btn.clicked.connect(self.browse_input_folder)
        
        input_layout.addWidget(QLabel("Input Directory:"))
        input_layout.addWidget(self.seq_folder_input)
        input_layout.addWidget(browse_seq_btn)
        input_group.setLayout(input_layout)
        
        # 2. Output Data Group
        output_group = QGroupBox("Output Destination")
        output_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #45475a; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        output_layout = QVBoxLayout()
        
        self.out_folder_input = QLineEdit()
        self.out_folder_input.setPlaceholderText("Select where to save results...")
        self.out_folder_input.setStyleSheet("background-color: #313244; padding: 5px; border: 1px solid #585b70;")
        browse_out_btn = QPushButton("Browse")
        browse_out_btn.setStyleSheet("background-color: #45475a; padding: 5px;")
        browse_out_btn.clicked.connect(self.browse_output_folder)
        
        output_layout.addWidget(QLabel("Output Directory:"))
        output_layout.addWidget(self.out_folder_input)
        output_layout.addWidget(browse_out_btn)
        output_group.setLayout(output_layout)

        # 3. DADA2 Parameters Group
        dada_group = QGroupBox("DADA2 Parameters")
        dada_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #45475a; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        dada_layout = QVBoxLayout()
        
        self.trunc_f_input = QLineEdit("0")
        self.trunc_r_input = QLineEdit("0")
        self.trunc_f_input.setStyleSheet("background-color: #313244; padding: 5px; border: 1px solid #585b70;")
        self.trunc_r_input.setStyleSheet("background-color: #313244; padding: 5px; border: 1px solid #585b70;")
        
        dada_layout.addWidget(QLabel("Trunc length Forward (F):"))
        dada_layout.addWidget(self.trunc_f_input)
        dada_layout.addWidget(QLabel("Trunc length Reverse (R):"))
        dada_layout.addWidget(self.trunc_r_input)
        dada_group.setLayout(dada_layout)
        
        # Assembly of Left Panel
        left_panel.addWidget(input_group)
        left_panel.addWidget(output_group)
        left_panel.addWidget(dada_group)
        left_panel.addStretch()
        
        # Progress Bar & Run Button
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid #585b70; border-radius: 5px; text-align: center; } QProgressBar::chunk { background-color: #a6e3a1; }")
        
        self.run_btn = QPushButton("▶ Run Pipeline")
        self.run_btn.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 5px;")
        self.run_btn.clicked.connect(self.start_pipeline)
        
        left_panel.addWidget(self.progress_bar)
        left_panel.addWidget(self.run_btn)
        
        # --- RIGHT PANEL (Tabs for Log and Viewer) ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabBar::tab { background: #313244; color: #cdd6f4; padding: 8px 20px; border: 1px solid #45475a; }
            QTabBar::tab:selected { background: #45475a; font-weight: bold; }
            QTabWidget::pane { border: 1px solid #45475a; }
        """)

        # Tab 1: Terminal Log
        self.log_tab = QWidget()
        log_layout = QVBoxLayout(self.log_tab)
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("background-color: #11111b; color: #a6adc8; font-family: Consolas, monospace; font-size: 11pt; border: none;")
        log_layout.addWidget(self.log_console)
        self.tabs.addTab(self.log_tab, "Terminal Log")

        # Tab 2: Artifact Viewer
        self.viewer_tab = QWidget()
        viewer_layout = QVBoxLayout(self.viewer_tab)
        
        viewer_controls = QHBoxLayout()
        self.load_file_btn = QPushButton("📂 Load .qza / .qzv File")
        self.load_file_btn.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 8px; border-radius: 3px;")
        self.load_file_btn.clicked.connect(self.handle_file_load)
        
        self.current_file_label = QLabel("No file loaded.")
        self.current_file_label.setStyleSheet("color: #a6adc8; font-style: italic;")
        
        viewer_controls.addWidget(self.load_file_btn)
        viewer_controls.addWidget(self.current_file_label)
        viewer_controls.addStretch()
        
        self.data_table = QTableWidget()
        self.data_table.setStyleSheet("background-color: #181825; alternate-background-color: #1e1e2e; gridline-color: #45475a; border: none;")
        self.data_table.setAlternatingRowColors(True)
        
        viewer_layout.addLayout(viewer_controls)
        viewer_layout.addWidget(self.data_table)
        self.tabs.addTab(self.viewer_tab, "Data Viewer")
        
        # Set Layout Proportions
        main_layout.addLayout(left_panel, 1)
        main_layout.addWidget(self.tabs, 2)

    # --- UI INTERACTION METHODS ---
    def browse_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Sequence Input Folder")
        if folder:
            self.seq_folder_input.setText(folder.replace('/', '\\'))

    def browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Destination")
        if folder:
            self.out_folder_input.setText(folder.replace('/', '\\'))

    def start_pipeline(self):
        in_dir = self.seq_folder_input.text()
        out_dir = self.out_folder_input.text()
        trunc_f = self.trunc_f_input.text()
        trunc_r = self.trunc_r_input.text()
        
        if not in_dir or not out_dir:
            self.append_log("[WARNING] Please select both an Input and Output folder before running.")
            return

        self.run_btn.setEnabled(False)
        self.run_btn.setText("Pipeline Running...")
        self.run_btn.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 5px;")
        self.log_console.clear()
        self.progress_bar.setValue(0)
        self.tabs.setCurrentIndex(0)

        self.thread = PipelineThread(in_dir, out_dir, trunc_f, trunc_r)
        self.thread.log_signal.connect(self.append_log)
        self.thread.progress_signal.connect(self.progress_bar.setValue)
        self.thread.finished_signal.connect(self.pipeline_finished)
        self.thread.start()

    def append_log(self, text):
        self.log_console.append(text)
        scrollbar = self.log_console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def pipeline_finished(self):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("▶ Run Pipeline")
        self.run_btn.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 5px;")

    # --- OFFLINE VIEWER ROUTING ---
    def handle_file_load(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open QIIME 2 File", "", "QIIME 2 Files (*.qza *.qzv)")
        if not filepath:
            return

        if filepath.endswith('.qzv'):
            self.open_qzv_offline(filepath)
        elif filepath.endswith('.qza'):
            self.load_qza_table(filepath)

    # --- QZV VISUALIZER (OFFLINE BROWSER OPENER) ---
    def open_qzv_offline(self, filepath):
        self.current_file_label.setText(f"Unpacking visualization: {os.path.basename(filepath)}...")
        QApplication.processEvents()

        try:
            # Create a persistent temp directory in the user's OS temp space to hold HTML files
            extract_dir = tempfile.mkdtemp(prefix="qiime2_vis_")
            
            # Extract the zip contents natively in Windows
            with zipfile.ZipFile(filepath, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            # Find the index.html file inside the extracted UUID folder
            extracted_folders = [f for f in os.listdir(extract_dir) if os.path.isdir(os.path.join(extract_dir, f))]
            if not extracted_folders:
                raise FileNotFoundError("Could not find internal data folder in the .qzv file.")
            
            uuid_folder = extracted_folders[0]
            html_path = os.path.join(extract_dir, uuid_folder, "data", "index.html")

            if os.path.exists(html_path):
                # Open the local HTML file in the user's default Windows web browser
                webbrowser.open(f"file://{html_path}")
                self.current_file_label.setText(f"Visualization opened in browser: {os.path.basename(filepath)}")
            else:
                QMessageBox.warning(self, "No Visual Data", "This .qzv file does not contain a standard HTML visualization.")
                self.current_file_label.setText("No visual data found.")

        except Exception as e:
            QMessageBox.critical(self, "Extraction Error", f"Could not open the visualization.\n\nError: {str(e)}")
            self.current_file_label.setText("Error extracting .qzv.")

    # --- QZA DATA EXTRACTOR (THE WSL BRIDGE) ---
    def load_qza_table(self, filepath):
        self.current_file_label.setText(f"Extracting data via WSL: {os.path.basename(filepath)}...")
        QApplication.processEvents()

        temp_dir = tempfile.mkdtemp()
        wsl_filepath = to_wsl_path(filepath)
        wsl_temp_dir = to_wsl_path(temp_dir)

        try:
            # Step 1: Tell WSL to export the QZA
            export_cmd = (
                "source ~/miniconda3/etc/profile.d/conda.sh && "
                "conda activate qiime2-amplicon-2024.2 && "
                f"qiime tools export --input-path '{wsl_filepath}' --output-path '{wsl_temp_dir}'"
            )
            subprocess.run(["wsl", "-e", "bash", "-c", export_cmd], check=True, capture_output=True)

            exported_files = os.listdir(temp_dir)
            df = None

            # Step 2: Handle .biom files (Feature Tables)
            if any(f.endswith('.biom') for f in exported_files):
                biom_file = [f for f in exported_files if f.endswith('.biom')][0]
                wsl_biom_path = f"{wsl_temp_dir}/{biom_file}"
                wsl_tsv_path = f"{wsl_temp_dir}/converted_table.tsv"
                
                convert_cmd = (
                    "source ~/miniconda3/etc/profile.d/conda.sh && "
                    "conda activate qiime2-amplicon-2024.2 && "
                    f"biom convert -i '{wsl_biom_path}' -o '{wsl_tsv_path}' --to-tsv"
                )
                subprocess.run(["wsl", "-e", "bash", "-c", convert_cmd], check=True)
                
                # Biom TSVs have a comment line at the top, so we skip it
                df = pd.read_csv(os.path.join(temp_dir, "converted_table.tsv"), sep='\t', skiprows=1)

            # Step 3: Handle standard .tsv files (Metadata/Stats)
            elif any(f.endswith('.tsv') for f in exported_files):
                tsv_file = [f for f in exported_files if f.endswith('.tsv')][0]
                df = pd.read_csv(os.path.join(temp_dir, tsv_file), sep='\t')
            
            else:
                raise ValueError("The artifact did not contain a readable tabular format (.biom or .tsv).")

            self.populate_table(df)
            self.current_file_label.setText(f"Loaded Table: {os.path.basename(filepath)}")

        except subprocess.CalledProcessError as e:
            QMessageBox.critical(self, "WSL Export Error", f"Failed to export artifact via WSL.\n\nError: {e.stderr.decode()}")
            self.current_file_label.setText("Error extracting file.")
        except Exception as e:
            QMessageBox.critical(self, "Data Error", f"Could not parse the extracted data.\n\nDetails: {str(e)}")
            self.current_file_label.setText("Error parsing file.")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def populate_table(self, df):
        self.data_table.clear()
        self.data_table.setRowCount(df.shape[0])
        self.data_table.setColumnCount(df.shape[1])
        self.data_table.setHorizontalHeaderLabels(df.columns.astype(str))
        self.data_table.setVerticalHeaderLabels(df.index.astype(str))

        for i in range(df.shape[0]):
            for j in range(df.shape[1]):
                item = QTableWidgetItem(str(df.iat[i, j]))
                self.data_table.setItem(i, j, item)
                
        self.data_table.resizeColumnsToContents()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Qiime2App()
    window.show()
    sys.exit(app.exec())