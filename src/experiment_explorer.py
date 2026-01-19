"""
Experiment Explorer - Interactive visualization of sweep experiment results.

Uses DuckDB to query JSON files directly from disk (no data duplication)
and Streamlit for interactive attribute selection and visualization.

Run with: streamlit run helpers/src/experiment_explorer.py
"""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


def flatten_json(nested_json: dict, parent_key: str = "", sep: str = ".") -> dict:
    """
    Flatten a nested JSON structure into dot-separated keys.
    
    Args:
        nested_json: Nested dictionary
        parent_key: Prefix for keys
        sep: Separator between levels
    
    Returns:
        Flattened dictionary with dot-separated keys
    """
    items = []
    for key, value in nested_json.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else key
        if isinstance(value, dict):
            items.extend(flatten_json(value, new_key, sep).items())
        elif isinstance(value, list):
            # For lists, store as string representation
            items.append((new_key, str(value)))
        else:
            items.append((new_key, value))
    return dict(items)


def load_experiments(data_dir: str) -> pd.DataFrame:
    """
    Load all experiment JSON files from sweep output directories.
    
    Scans for stdout.json files in sweep run directories and flattens
    the JSON structure for tabular analysis.
    
    Args:
        data_dir: Base directory containing sweep runs
    
    Returns:
        DataFrame with flattened experiment data
    """
    data_path = Path(data_dir).expanduser()
    
    if not data_path.exists():
        return pd.DataFrame()
    
    # Find all stdout.json files (individual case results)
    json_files = list(data_path.glob("**/stdout.json"))
    
    if not json_files:
        return pd.DataFrame()
    
    records = []
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    continue
                data = json.loads(content)
            
            # Flatten the nested JSON
            flat = flatten_json(data)
            
            # Add metadata about the file location
            flat["_file_path"] = str(json_file)
            flat["_run_id"] = json_file.parent.parent.name
            flat["_case_id"] = json_file.parent.name
            
            # Try to load params.json for additional context
            params_file = json_file.parent / "params.json"
            if params_file.exists():
                with open(params_file, "r", encoding="utf-8") as f:
                    params = json.load(f)
                for k, v in params.items():
                    if not k.startswith("_"):
                        flat[f"param.{k}"] = v
            
            records.append(flat)
        except (json.JSONDecodeError, IOError) as e:
            st.warning(f"Skipping {json_file}: {e}")
            continue
    
    if not records:
        return pd.DataFrame()
    
    return pd.DataFrame(records)


def get_numeric_columns(df: pd.DataFrame) -> list:
    """Get columns that can be used for numeric plotting."""
    numeric_cols = []
    for col in df.columns:
        if col.startswith("_"):
            continue
        try:
            pd.to_numeric(df[col], errors="raise")
            numeric_cols.append(col)
        except (ValueError, TypeError):
            continue
    return sorted(numeric_cols)


def get_categorical_columns(df: pd.DataFrame) -> list:
    """Get columns suitable for categorical grouping/filtering."""
    cat_cols = []
    for col in df.columns:
        if col.startswith("_"):
            continue
        # Consider categorical if few unique values or is string type
        if df[col].dtype == object or df[col].nunique() <= 20:
            cat_cols.append(col)
    return sorted(cat_cols)


def main():
    st.set_page_config(
        page_title="Experiment Explorer",
        page_icon="📊",
        layout="wide"
    )
    
    st.title("📊 Experiment Explorer")
    st.markdown("Interactive visualization of quantum algorithm sweep results")
    
    # Sidebar for configuration
    with st.sidebar:
        st.header("Data Source")
        data_dir = st.text_input(
            "Sweep output directory",
            value="~/qp4p",
            help="Directory containing sweep run outputs"
        )
        
        if st.button("🔄 Reload Data"):
            st.cache_data.clear()
    
    # Load data with caching
    @st.cache_data
    def cached_load(directory):
        return load_experiments(directory)
    
    df = cached_load(data_dir)
    
    if df.empty:
        st.warning(f"No experiment data found in {data_dir}")
        st.info("Run some sweeps first, then reload.")
        return
    
    st.success(f"Loaded {len(df)} experiments from {df['_run_id'].nunique()} runs")
    
    # Get column types
    numeric_cols = get_numeric_columns(df)
    categorical_cols = get_categorical_columns(df)
    all_cols = sorted([c for c in df.columns if not c.startswith("_")])
    
    # Sidebar filters
    with st.sidebar:
        st.header("Filters")
        
        # Dynamic filters based on categorical columns
        filters = {}
        filter_cols = st.multiselect(
            "Filter by attributes",
            options=categorical_cols,
            default=[]
        )
        
        for col in filter_cols:
            unique_vals = df[col].dropna().unique().tolist()
            selected = st.multiselect(
                f"{col}",
                options=unique_vals,
                default=unique_vals
            )
            if selected:
                filters[col] = selected
    
    # Apply filters
    filtered_df = df.copy()
    for col, values in filters.items():
        filtered_df = filtered_df[filtered_df[col].isin(values)]
    
    st.markdown(f"**Showing {len(filtered_df)} of {len(df)} experiments**")
    
    # Main content tabs
    tab1, tab2, tab3, tab4 = st.tabs(["📈 2D Plot", "🧊 3D Plot", "📋 Data Table", "🔍 Raw JSON"])
    
    with tab1:
        st.subheader("2D Scatter Plot")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            x_axis = st.selectbox("X Axis", options=numeric_cols, index=0 if numeric_cols else None, key="2d_x")
        with col2:
            y_axis = st.selectbox("Y Axis", options=numeric_cols, index=min(1, len(numeric_cols)-1) if numeric_cols else None, key="2d_y")
        with col3:
            color_by = st.selectbox("Color by", options=["None"] + categorical_cols, index=0, key="2d_color")
        
        if x_axis and y_axis:
            plot_df = filtered_df[[x_axis, y_axis]].copy()
            plot_df[x_axis] = pd.to_numeric(plot_df[x_axis], errors="coerce")
            plot_df[y_axis] = pd.to_numeric(plot_df[y_axis], errors="coerce")
            
            if color_by != "None":
                plot_df[color_by] = filtered_df[color_by].astype(str)
                fig = px.scatter(
                    plot_df.dropna(),
                    x=x_axis,
                    y=y_axis,
                    color=color_by,
                    title=f"{y_axis} vs {x_axis}",
                    hover_data={x_axis: True, y_axis: True}
                )
            else:
                fig = px.scatter(
                    plot_df.dropna(),
                    x=x_axis,
                    y=y_axis,
                    title=f"{y_axis} vs {x_axis}"
                )
            
            fig.update_layout(height=500)
            st.plotly_chart(fig, use_container_width=True)
    
    with tab2:
        st.subheader("3D Scatter Plot")
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            x_axis_3d = st.selectbox("X Axis", options=numeric_cols, index=0 if numeric_cols else None, key="3d_x")
        with col2:
            y_axis_3d = st.selectbox("Y Axis", options=numeric_cols, index=min(1, len(numeric_cols)-1) if numeric_cols else None, key="3d_y")
        with col3:
            z_axis_3d = st.selectbox("Z Axis", options=numeric_cols, index=min(2, len(numeric_cols)-1) if numeric_cols else None, key="3d_z")
        with col4:
            color_by_3d = st.selectbox("Color by", options=["None"] + categorical_cols, index=0, key="3d_color")
        
        if x_axis_3d and y_axis_3d and z_axis_3d:
            plot_df = filtered_df[[x_axis_3d, y_axis_3d, z_axis_3d]].copy()
            plot_df[x_axis_3d] = pd.to_numeric(plot_df[x_axis_3d], errors="coerce")
            plot_df[y_axis_3d] = pd.to_numeric(plot_df[y_axis_3d], errors="coerce")
            plot_df[z_axis_3d] = pd.to_numeric(plot_df[z_axis_3d], errors="coerce")
            
            if color_by_3d != "None":
                plot_df[color_by_3d] = filtered_df[color_by_3d].astype(str)
                fig = px.scatter_3d(
                    plot_df.dropna(),
                    x=x_axis_3d,
                    y=y_axis_3d,
                    z=z_axis_3d,
                    color=color_by_3d,
                    title=f"3D: {x_axis_3d} × {y_axis_3d} × {z_axis_3d}"
                )
            else:
                fig = px.scatter_3d(
                    plot_df.dropna(),
                    x=x_axis_3d,
                    y=y_axis_3d,
                    z=z_axis_3d,
                    title=f"3D: {x_axis_3d} × {y_axis_3d} × {z_axis_3d}"
                )
            
            fig.update_layout(height=600)
            st.plotly_chart(fig, use_container_width=True)
    
    with tab3:
        st.subheader("Data Table")
        
        # Column selector
        display_cols = st.multiselect(
            "Select columns to display",
            options=all_cols,
            default=all_cols[:10] if len(all_cols) > 10 else all_cols
        )
        
        if display_cols:
            st.dataframe(
                filtered_df[display_cols],
                use_container_width=True,
                height=400
            )
            
            # Download button
            csv = filtered_df[display_cols].to_csv(index=False)
            st.download_button(
                label="📥 Download CSV",
                data=csv,
                file_name="experiments.csv",
                mime="text/csv"
            )
    
    with tab4:
        st.subheader("Raw JSON Viewer")
        
        if "_file_path" in filtered_df.columns and len(filtered_df) > 0:
            selected_file = st.selectbox(
                "Select experiment",
                options=filtered_df["_file_path"].tolist(),
                format_func=lambda x: f"{Path(x).parent.parent.name}/{Path(x).parent.name}"
            )
            
            if selected_file:
                try:
                    with open(selected_file, "r", encoding="utf-8") as f:
                        raw_json = json.load(f)
                    st.json(raw_json)
                except Exception as e:
                    st.error(f"Error loading JSON: {e}")
    
    # Footer with available attributes
    with st.expander("📋 Available Attributes"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Numeric attributes:**")
            st.write(numeric_cols)
        with col2:
            st.markdown("**Categorical attributes:**")
            st.write(categorical_cols)


if __name__ == "__main__":
    main()
