import sys
import subprocess
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTextEdit, QProgressBar, QGroupBox, QFileDialog)
from PyQt6.QtCore import QThread, pyqtSignal

# ---------------------------------------------------------
# UTILITY FUNCTIONS
# ---------------------------------------------------------
def to_wsl_path(windows_path):
    """Converts a standard Windows path to a WSL-friendly path."""
    if not windows_path:
        return ""
    # E.g., C:\Users\Name\Data becomes /mnt/c/Users/Name/Data
    wsl_path = windows_path.replace('C:\\', '/mnt/c/').replace('c:\\', '/mnt/c/')
    wsl_path = wsl_path.replace('\\', '/')
    return wsl_path

# ---------------------------------------------------------
# BACKGROUND WORKER THREAD
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

    def run(self):
        self.log_signal.emit("[System] Initializing Pipeline...")
        self.progress_signal.emit(5)
        
        # 1. Convert Windows paths to WSL paths
        wsl_input_dir = to_wsl_path(self.input_dir)
        wsl_output_dir = to_wsl_path(self.output_dir)
        
        self.log_signal.emit(f"[System] Mapped Input: {wsl_input_dir}")
        self.log_signal.emit(f"[System] Mapped Output: {wsl_output_dir}")
        
        # Ensure the output directory exists in WSL
        mkdir_command = f"mkdir -p '{wsl_output_dir}'"

        # 2. Formulate the QIIME 2 command string
        # Note: Replace 'qiime2-amplicon-2024.2' with the exact name of your WSL conda env
        # 2. Formulate the QIIME 2 command string
        # Note: Replace 'qiime2-amplicon-2024.2' with the exact name of your WSL conda env
        qiime_pipeline = (
            # Smart Conda Initialization: Check common installation paths
            "if [ -f ~/miniconda3/etc/profile.d/conda.sh ]; then source ~/miniconda3/etc/profile.d/conda.sh; "
            "elif [ -f ~/anaconda3/etc/profile.d/conda.sh ]; then source ~/anaconda3/etc/profile.d/conda.sh; "
            "elif [ -f ~/miniconda/etc/profile.d/conda.sh ]; then source ~/miniconda/etc/profile.d/conda.sh; "
            "elif [ -f /opt/miniconda3/etc/profile.d/conda.sh ]; then source /opt/miniconda3/etc/profile.d/conda.sh; "
            "else echo '[ERROR] Conda not found in standard directories. Is it installed in WSL?' && exit 1; fi && "
            
            # Activate the QIIME2 environment
            "conda activate qiime2-amplicon-2024.2 && "
            
            # Step 1: Import Data
            "echo '[System] Step 1/3: Importing sequence data...' && "
            f"qiime tools import "
            f"--type 'SampleData[PairedEndSequencesWithQuality]' "
            f"--input-path '{wsl_input_dir}' "
            f"--input-format CasavaOneEightSingleLanePerSampleDirFmt "
            f"--output-path '{wsl_output_dir}/demux.qza' && "
            
            # Step 2: Denoise with DADA2
            "echo '[System] Step 2/3: Running DADA2 (This may take a while)...' && "
            f"qiime dada2 denoise-paired "
            f"--i-demultiplexed-seqs '{wsl_output_dir}/demux.qza' "
            f"--p-trunc-len-f {self.trunc_f} "
            f"--p-trunc-len-r {self.trunc_r} "
            f"--o-table '{wsl_output_dir}/table.qza' "
            f"--o-representative-sequences '{wsl_output_dir}/rep-seqs.qza' "
            f"--o-denoising-stats '{wsl_output_dir}/denoising-stats.qza' && "
            
            # Step 3: Generate Visual Summaries
            "echo '[System] Step 3/3: Generating visual summaries...' && "
            f"qiime feature-table summarize "
            f"--i-table '{wsl_output_dir}/table.qza' "
            f"--o-visualization '{wsl_output_dir}/table_summary.qzv' && "
            
            "echo '[System] All steps completed successfully.'"
        )

        # Combine into the final WSL execution call
        full_bash_command = f"{mkdir_command} && {qiime_pipeline}"
        wsl_command = ["wsl", "-e", "bash", "-c", full_bash_command]
        
        try:
            # 3. Execute command and capture output in real-time
            process = subprocess.Popen(wsl_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            
            for line in process.stdout:
                # Clean up the output line and emit to the GUI
                clean_line = line.strip()
                if clean_line:
                    self.log_signal.emit(clean_line)
                    
                    # Optional: Increment progress bar based on text cues
                    if "Step 1/3" in clean_line: self.progress_signal.emit(15)
                    elif "Step 2/3" in clean_line: self.progress_signal.emit(40)
                    elif "Step 3/3" in clean_line: self.progress_signal.emit(85)
                
            process.wait()
            
            if process.returncode == 0:
                self.progress_signal.emit(100)
                self.log_signal.emit("\n[SUCCESS] Pipeline finished. Check your output folder.")
            else:
                self.log_signal.emit(f"\n[ERROR] Pipeline failed with exit code {process.returncode}.")
            
        except Exception as e:
            self.log_signal.emit(f"\n[FATAL ERROR] Failed to communicate with WSL: {str(e)}")
            
        self.finished_signal.emit()

# ---------------------------------------------------------
# MAIN WINDOW UI
# ---------------------------------------------------------
class Qiime2App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QIIME2 Lab Tool")
        self.resize(1050, 750)
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
        
        self.trunc_f_input = QLineEdit("250")
        self.trunc_r_input = QLineEdit("220")
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
        
        # --- RIGHT PANEL (Log Console) ---
        right_panel = QVBoxLayout()
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("background-color: #11111b; color: #a6adc8; font-family: Consolas, monospace; font-size: 11pt; border: 1px solid #45475a; padding: 10px;")
        
        log_label = QLabel("Pipeline Terminal Log")
        log_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        right_panel.addWidget(log_label)
        right_panel.addWidget(self.log_console)
        
        # Set Layout Proportions (Left is narrower, Right is wider)
        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 2)

    def browse_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Sequence Input Folder")
        if folder:
            self.seq_folder_input.setText(folder.replace('/', '\\'))

    def browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Destination")
        if folder:
            self.out_folder_input.setText(folder.replace('/', '\\'))

    def start_pipeline(self):
        # Retrieve GUI inputs
        in_dir = self.seq_folder_input.text()
        out_dir = self.out_folder_input.text()
        trunc_f = self.trunc_f_input.text()
        trunc_r = self.trunc_r_input.text()
        
        # Validation
        if not in_dir or not out_dir:
            self.append_log("[WARNING] Please select both an Input and Output folder before running.")
            return

        # Lock UI
        self.run_btn.setEnabled(False)
        self.run_btn.setText("Pipeline Running...")
        self.run_btn.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 5px;")
        self.log_console.clear()
        self.progress_bar.setValue(0)

        # Initialize and start the background thread
        self.thread = PipelineThread(in_dir, out_dir, trunc_f, trunc_r)
        self.thread.log_signal.connect(self.append_log)
        self.thread.progress_signal.connect(self.progress_bar.setValue)
        self.thread.finished_signal.connect(self.pipeline_finished)
        self.thread.start()

    def append_log(self, text):
        self.log_console.append(text)
        # Auto-scroll to the bottom of the text edit
        scrollbar = self.log_console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def pipeline_finished(self):
        # Unlock the UI
        self.run_btn.setEnabled(True)
        self.run_btn.setText("▶ Run Pipeline")
        self.run_btn.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 5px;")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Qiime2App()
    window.show()
    sys.exit(app.exec())