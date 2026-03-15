import sys
import subprocess
import time
import os
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
        
        # Generate a unique log filename based on the current time
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.log_filepath = os.path.join(self.output_dir, f"qiime2_run_log_{timestamp}.txt")

    def run(self):
        self.log_signal.emit("[System] Initializing Pipeline...")
        self.progress_signal.emit(5)
        
        wsl_input_dir = to_wsl_path(self.input_dir)
        wsl_output_dir = to_wsl_path(self.output_dir)
        
        self.log_signal.emit(f"[System] Mapped Input: {wsl_input_dir}")
        self.log_signal.emit(f"[System] Mapped Output: {wsl_output_dir}")
        self.log_signal.emit(f"[System] Run log will be saved to: {self.log_filepath}")
        
        # Ensure the output directory exists in Windows/WSL so we can write the log there
        os.makedirs(self.output_dir, exist_ok=True)

        qiime_pipeline = (
            "if [ -f ~/miniconda3/etc/profile.d/conda.sh ]; then source ~/miniconda3/etc/profile.d/conda.sh; "
            "elif [ -f ~/anaconda3/etc/profile.d/conda.sh ]; then source ~/anaconda3/etc/profile.d/conda.sh; "
            "elif [ -f ~/miniconda/etc/profile.d/conda.sh ]; then source ~/miniconda/etc/profile.d/conda.sh; "
            "else echo '[ERROR] Conda not found in standard directories.' && exit 1; fi && "
            
            # Step 0: Update Conda (-y automatically answers yes to prompts)
            "echo '[System] Step 0/4: Updating Conda base environment...' && "
            "conda update -n base -c defaults conda -y && "
            
            # Activate Environment
            "conda activate qiime2-amplicon-2024.2 && "
            
            # Step 1: Import Data
            "echo '[System] Step 1/4: Importing sequence data...' && "
            f"qiime tools import "
            f"--type 'SampleData[PairedEndSequencesWithQuality]' "
            f"--input-path '{wsl_input_dir}' "
            f"--input-format CasavaOneEightSingleLanePerSampleDirFmt "
            f"--output-path '{wsl_output_dir}/demux.qza' && "
            
            # Step 2: Denoise with DADA2
            "echo '[System] Step 2/4: Running DADA2 (This may take a while)...' && "
            f"qiime dada2 denoise-paired "
            f"--i-demultiplexed-seqs '{wsl_output_dir}/demux.qza' "
            f"--p-trunc-len-f {self.trunc_f} "
            f"--p-trunc-len-r {self.trunc_r} "
            f"--o-table '{wsl_output_dir}/table.qza' "
            f"--o-representative-sequences '{wsl_output_dir}/rep-seqs.qza' "
            f"--o-denoising-stats '{wsl_output_dir}/denoising-stats.qza' && "
            
            # Step 3: Generate Visual Summaries
            "echo '[System] Step 3/4: Generating visual summaries...' && "
            f"qiime feature-table summarize "
            f"--i-table '{wsl_output_dir}/table.qza' "
            f"--o-visualization '{wsl_output_dir}/table_summary.qzv' && "
            
            "echo '[System] Step 4/4: All steps completed successfully.'"
        )

        wsl_command = ["wsl", "-e", "bash", "-c", qiime_pipeline]
        
        try:
            # Open the text file in append mode. We use utf-8 to prevent Windows character errors.
            with open(self.log_filepath, "a", encoding="utf-8") as log_file:
                
                # Write initial setup info to the file
                log_file.write(f"--- QIIME2 Run Started: {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                log_file.write(f"Input Directory: {self.input_dir}\n")
                log_file.write(f"Output Directory: {self.output_dir}\n")
                log_file.write(f"Trunc F: {self.trunc_f} | Trunc R: {self.trunc_r}\n\n")
                
                process = subprocess.Popen(wsl_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                
                for line in process.stdout:
                    clean_line = line.strip()
                    if clean_line:
                        # Emit to GUI
                        self.log_signal.emit(clean_line)
                        
                        # Write to text file and flush to save immediately
                        log_file.write(clean_line + "\n")
                        log_file.flush() 
                        
                        # Update Progress Bar
                        if "Step 0/4" in clean_line: self.progress_signal.emit(10)
                        elif "Step 1/4" in clean_line: self.progress_signal.emit(30)
                        elif "Step 2/4" in clean_line: self.progress_signal.emit(50)
                        elif "Step 3/4" in clean_line: self.progress_signal.emit(85)
                        elif "Step 4/4" in clean_line: self.progress_signal.emit(100)
                    
                process.wait()
                
                if process.returncode == 0:
                    success_msg = "\n[SUCCESS] Pipeline finished. Check your output folder for the .qza/.qzv files and this log."
                    self.log_signal.emit(success_msg)
                    log_file.write(success_msg + "\n")
                else:
                    error_msg = f"\n[ERROR] Pipeline failed with exit code {process.returncode}."
                    self.log_signal.emit(error_msg)
                    log_file.write(error_msg + "\n")
                
        except Exception as e:
            fatal_msg = f"\n[FATAL ERROR] Failed to communicate with WSL: {str(e)}"
            self.log_signal.emit(fatal_msg)
            # If the file couldn't be opened, this will fail, but we catch it silently here
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
        
        # --- RIGHT PANEL (Log Console) ---
        right_panel = QVBoxLayout()
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("background-color: #11111b; color: #a6adc8; font-family: Consolas, monospace; font-size: 11pt; border: 1px solid #45475a; padding: 10px;")
        
        log_label = QLabel("Pipeline Terminal Log")
        log_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        right_panel.addWidget(log_label)
        right_panel.addWidget(self.log_console)
        
        # Set Layout Proportions
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

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Qiime2App()
    window.show()
    sys.exit(app.exec())