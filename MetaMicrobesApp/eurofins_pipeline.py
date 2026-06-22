import os
import re
import pandas as pd
import numpy as np
import scipy.stats
import plotly.graph_objects as go
from plotly.subplots import make_subplots

def calculate_shannon(counts):
    """Calculate Shannon diversity index (base e)"""
    proportions = counts / counts.sum()
    return scipy.stats.entropy(proportions, base=np.e)

def calculate_simpson(counts):
    """Calculate Gini-Simpson diversity index (matches R's vegan package)"""
    proportions = counts / counts.sum()
    return 1 - np.sum(proportions ** 2)

def clean_taxa_data(df):
    """Replicates the tidyverse data cleaning logic"""
    taxa_cols = ['phylum', 'class', 'order', 'family', 'genus', 'species']
    for col in taxa_cols:
        df[col] = df[col].fillna("not_classified")
        df[col] = df[col].str.replace("_Incertae_sedis", "_uncertain")
    
    # Create mASV column: if species is not_classified, use mASV_ID
    df['mASV'] = np.where(df['species'] == 'not_classified', df['mASV_ID'], df['species'])
    return df

def generate_interactive_report(df_clean, sample_cols, title, report_dir):
    """Generates an advanced interactive Plotly HTML report combining metrics and stacked bars"""
    taxa_levels = ['phylum', 'class', 'order', 'family', 'genus', 'species']
    
    # Create a unified HTML file with tabs for different taxonomic levels
    html_content = f"""
    <html><head><title>{title} Microbiome Report</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {{ background-color: #11111b; color: #cdd6f4; font-family: 'Segoe UI', sans-serif; padding: 20px; }}
        h1, h2 {{ color: #a6e3a1; }}
        .plot-container {{ background: #181825; padding: 20px; border-radius: 8px; margin-bottom: 30px; }}
    </style>
    </head><body>
    <h1>{title} - Advanced Microbiome Analysis</h1>
    <p>Hover over plots for exact abundances. Use the legend to filter specific taxa.</p>
    """

    # 1. Diversity Metrics Plot
    diversity_data = {
        'Sample': sample_cols,
        'Richness (mASVs)': [(df_clean[col] > 0).sum() for col in sample_cols],
        'Shannon': [calculate_shannon(df_clean[col]) for col in sample_cols],
        'Simpson': [calculate_simpson(df_clean[col]) for col in sample_cols]
    }
    div_df = pd.DataFrame(diversity_data)
    
    fig_div = make_subplots(rows=1, cols=3, subplot_titles=("Richness (mASVs)", "Shannon Index", "Simpson Index"))
    fig_div.add_trace(go.Bar(x=div_df['Sample'], y=div_df['Richness (mASVs)'], name="Richness", marker_color='#89b4fa'), row=1, col=1)
    fig_div.add_trace(go.Bar(x=div_df['Sample'], y=div_df['Shannon'], name="Shannon", marker_color='#cba6f7'), row=1, col=2)
    fig_div.add_trace(go.Bar(x=div_df['Sample'], y=div_df['Simpson'], name="Simpson", marker_color='#f38ba8'), row=1, col=3)
    fig_div.update_layout(height=400, template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', showlegend=False)
    
    html_content += f"<div class='plot-container'><h2>Alpha Diversity Metrics</h2>{fig_div.to_html(full_html=False, include_plotlyjs=False)}</div>"

    # 2. Taxa Stacked Bar Charts
    for level in taxa_levels:
        # Group by taxa level and sum across samples
        level_df = df_clean.groupby(level)[sample_cols].sum().reset_index()
        
        # Calculate percentages to determine "< 1%" 
        total_per_sample = level_df[sample_cols].sum()
        percent_df = level_df[sample_cols].div(total_per_sample) * 100
        
        # Determine filling logic (Main taxa vs <1%)
        level_df['for_fill'] = np.where(percent_df.max(axis=1) < 1.0, "< 1%", level_df[level])
        plot_df = level_df.groupby('for_fill')[sample_cols].sum().reset_index()
        
        # Sort so <1% is at the bottom
        plot_df['sort_val'] = np.where(plot_df['for_fill'] == '< 1%', 0, plot_df[sample_cols].sum(axis=1))
        plot_df = plot_df.sort_values(by='sort_val', ascending=False).drop(columns=['sort_val'])

        # Build Plotly Stacked Bar
        fig_bar = go.Figure()
        for _, row in plot_df.iterrows():
            taxa_name = row['for_fill']
            y_vals = row[sample_cols].values
            fig_bar.add_trace(go.Bar(name=taxa_name, x=sample_cols, y=y_vals))
            
        fig_bar.update_layout(barmode='stack', title=f"Relative Abundance: {level.capitalize()}",
                              template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', height=600)
        
        html_content += f"<div class='plot-container'><h2>{level.capitalize()} Profiling</h2>{fig_bar.to_html(full_html=False, include_plotlyjs=False)}</div>"

    html_content += "</body></html>"
    
    report_path = os.path.join(report_dir, f"{title}_Interactive_Report.html")
    with open(report_path, "w", encoding='utf-8') as f:
        f.write(html_content)
        
    return report_path

def run_pipeline(work_dir, region, title, res_dir, num_es, es1_name, es1_count, es2_name, es2_count, log_callback):
    """Main execution function mapped 1:1 from the R logic"""
    
    # 1. Directory Setup
    report_dir = os.path.join(work_dir, "Report")
    os.makedirs(report_dir, exist_ok=True)
    
    data_path = os.path.join(work_dir, "R_analyses", f"Complete_{region}_data.csv")
    if not os.path.exists(data_path):
        raise FileNotFoundError(f"Missing data file: {data_path}")
        
    log_callback(f"[System] Loading taxonomy data from {data_path}...")
    df = pd.read_csv(data_path)
    
    # 2. Clean Taxa
    df = clean_taxa_data(df)
    meta_cols = ['mASV_ID', 'domain', 'phylum', 'class', 'order', 'family', 'genus', 'species', 'mASV']
    sample_cols = [col for col in df.columns if col not in meta_cols]
    
    log_callback(f"[System] Detected {len(sample_cols)} samples.")

    # 3. Enumeration Logic (if spikes are provided)
    if num_es > 0 and es1_count > 0:
        log_callback(f"[System] Processing Absolute Enumeration based on {num_es} spikes...")
        
        es1_data = df[df['species'] == es1_name]
        if es1_data.empty: raise ValueError(f"Spike {es1_name} not found in data.")
        es1_vector = es1_data[sample_cols].iloc[0].replace(0, 1) # avoid div by zero
        
        # Sweep equivalent: div by spike counts, mult by cells
        norm1 = df[sample_cols].div(es1_vector) * es1_count
        
        if num_es == 2:
            es2_data = df[df['species'] == es2_name]
            if es2_data.empty: raise ValueError(f"Spike {es2_name} not found in data.")
            es2_vector = es2_data[sample_cols].iloc[0].replace(0, 1)
            norm2 = df[sample_cols].div(es2_vector) * es2_count
            
            # Average the two estimates
            final_counts = (norm1 + norm2) / 2
        else:
            final_counts = norm1
            
        final_counts = final_counts.round(0)
        df_processed = pd.concat([df[meta_cols], final_counts], axis=1)
        
        # Remove spikes from final data
        df_processed = df_processed[df_processed['species'] != es1_name]
        if num_es == 2: df_processed = df_processed[df_processed['species'] != es2_name]

    else:
        log_callback("[System] No valid enumeration spikes provided. Calculating Relative Proportions...")
        # Remove spikes if requested (assuming yes for general workflow)
        df_processed = df[~df['species'].isin([es1_name, es2_name])]
        
        # Convert to percentages (decostand total * 100)
        totals = df_processed[sample_cols].sum(axis=0).replace(0, 1)
        df_processed[sample_cols] = df_processed[sample_cols].div(totals) * 100

    # 4. Pathogen Search
    log_callback("[System] Executing Pathogen Search...")
    pathogen_file = os.path.join(res_dir, "Food pathogens list.csv")
    if os.path.exists(pathogen_file):
        pathogens = pd.read_csv(pathogen_file, header=None)[0].tolist()
        df_processed['target'] = df_processed['genus'] + " " + df_processed['species']
        
        search_results = []
        for term in pathogens:
            matches = df_processed[df_processed['target'].str.contains(rf"^{term}", case=False, na=False, regex=True)]
            if matches.empty:
                empty_row = {'target': term}
                empty_row.update({s: 0 for s in sample_cols})
                search_results.append(empty_row)
            else:
                for _, row in matches.iterrows():
                    res = {'target': row['target']}
                    res.update({s: row[s] for s in sample_cols})
                    search_results.append(res)
                    
        res_df = pd.DataFrame(search_results).groupby('target')[sample_cols].sum().reset_index()
        res_df.to_csv(os.path.join(report_dir, f"{title}_search_results.csv"), index=False)
        df_processed.drop(columns=['target'], inplace=True)
    else:
        log_callback(f"[Warning] Pathogen list not found at {pathogen_file}")

    # 5. Generate Advanced HTML Report
    log_callback("[System] Generating Interactive Visualizations...")
    html_report_path = generate_interactive_report(df_processed, sample_cols, title, report_dir)
    
    return html_report_path