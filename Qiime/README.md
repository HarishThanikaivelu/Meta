# QIIME2 Lab Tool — Setup Guide (Windows)

## Prerequisites
1. **Anaconda or Miniconda** installed → https://docs.conda.io/en/latest/miniconda.html
2. **QIIME2 installed in a conda environment**

## Step 1: Install QIIME2
Follow the official QIIME2 Windows install guide (WSL2 or native):
https://docs.qiime2.org/2024.10/install/

The default environment name expected by this tool is:
```
qiime2-amplicon-2024.10
```
(You can change this in the app's Settings tab.)

## Step 2: Install the GUI Tool
Open Anaconda Prompt and run:
```bash
pip install PyQt6
```

## Step 3: Run the App
Double-click `run.bat` OR run:
```bash
python qiime2_app.py
```

## Step 4: Use the Tool

### 16S rRNA Amplicon Workflow
1. Go to the **16S rRNA** tab
2. Select your **sequence folder** (must be in Casava 1.8 format — one folder with paired fastq.gz files per sample)
3. Select your **metadata .tsv file**
4. Select your **SILVA or GreenGenes classifier .qza file** (download from https://docs.qiime2.org/2024.10/data-resources/)
5. Adjust **DADA2 parameters** (trim/truncation lengths based on your quality plots)
6. Choose your **output folder**
7. Press **▶ Run Pipeline**

### Shotgun Metagenomics Workflow
1. Go to the **Shotgun** tab
2. Select your **sequence folder**
3. Select your **Kraken2 database folder**
4. (Optional) Select a **host reference .qza** for human/mouse read removal
5. Choose your **output folder**
6. Press **▶ Run Pipeline**

## Output Files
After the pipeline completes, the output folder will contain:

| File | Description |
|------|-------------|
| `demux.qzv` | Interactive demux summary |
| `table.qza` | Feature table |
| `rep-seqs.qza` | Representative sequences |
| `taxonomy.qza` | Taxonomic assignments |
| `taxa-barplot.qzv` | Interactive taxonomy bar plots |
| `core-metrics-results/` | Alpha & beta diversity results |

Open `.qzv` files at https://view.qiime2.org

## Settings
- **Conda environment name**: must match your QIIME2 conda env
- **Threads**: set to number of CPU cores available (check Task Manager)

## Troubleshooting
- **"conda not found"**: Make sure Anaconda is in your system PATH
- **Pipeline fails at DADA2**: Try adjusting truncation lengths
- **Slow performance**: Reduce threads or increase RAM allocation
