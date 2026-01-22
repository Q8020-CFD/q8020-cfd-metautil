"""
Experiment Explorer - Interactive visualization of sweep experiment results.

Uses DuckDB to query JSON files directly from disk (no data duplication)
and Streamlit for interactive attribute selection and visualization.

Features:
- Dynamic field discovery from JSON files
- 2D and 3D scatter plots with configurable axes
- Filtering by field values (e.g., "where algorithm=hhl, plot fidelity vs shots")
- Default view: case × algorithm × backend colored by fidelity
- Live file watching with watchdog for automatic updates
- URL query parameters for pre-configured views

Run with: 
    streamlit run experiment_explorer.py [-- --data-dir ~/q8020]

URL Query Parameters (for pre-configured views):
    ?x=config.ansatz_reps&y=metrics.fidelity&color=_case_id&preset=reps
    
    Supported parameters:
    - data_dir: Data directory path
    - x, y, z: Axis field names
    - color: Color-by field name
    - filter: Filter expression (field:value or field:~contains)
    - preset: Named preset (reps, peclet, maxiter, matrix, overview)
    - tab: Initial tab (2d, 3d, table, json, sql)
"""

import json
import threading
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent


# Default axis mappings for common field names
DEFAULT_AXES = {
    "x": ["_case_id", "problem.dimension", "config.shots", "param.size"],
    "y": ["metrics.fidelity", "metrics.l2_error", "circuit_info.depth", "circuit_info.num_qubits"],
    "z": ["algorithm", "script_name", "config.backend", "backend_info.backend_name"],
    "color": ["algorithm", "config.backend", "_run_id", "status"]
}

# Named presets for common visualizations
PRESETS = {
    "reps": {
        "x": "config.ansatz_reps",
        "y": "metrics.fidelity",
        "color": "_case_id",
        "filter": "_case_id:~reps_",
        "tab": "2d",
        "title": "Ansatz Depth Study"
    },
    "peclet": {
        "x": "config.peclet",
        "y": "metrics.fidelity",
        "color": "_case_id",
        "filter": "_case_id:~peclet_",
        "tab": "2d",
        "title": "Peclet Number Study"
    },
    "maxiter": {
        "x": "config.maxiter",
        "y": "metrics.optimization_cost",
        "color": "_case_id",
        "filter": "_case_id:~maxiter_",
        "tab": "2d",
        "title": "Optimizer Iterations Study"
    },
    "matrix": {
        "x": "_case_id",
        "y": "metrics.fidelity",
        "color": "_case_id",
        "filter": "_case_id:~matrix_",
        "tab": "2d",
        "title": "Matrix Type Comparison"
    },
    "overview": {
        "x": "_case_id",
        "y": "metrics.fidelity",
        "color": "algorithm",
        "tab": "2d",
        "title": "Overview - All Cases"
    }
}


class ExperimentFileHandler(FileSystemEventHandler):
    """Watchdog handler that detects new/modified stdout.json files."""
    
    def __init__(self):
        self.change_detected = threading.Event()
        self._last_change_time = 0
    
    def on_created(self, event):
        if isinstance(event, FileCreatedEvent) and event.src_path.endswith("stdout.json"):
            self.change_detected.set()
    
    def on_modified(self, event):
        if isinstance(event, FileModifiedEvent) and event.src_path.endswith("stdout.json"):
            self.change_detected.set()


# Global observer and handler (persists across Streamlit reruns)
_file_observer: Optional[Observer] = None
_file_handler: Optional[ExperimentFileHandler] = None
_watched_path: Optional[str] = None


def start_file_watcher(data_dir: str) -> ExperimentFileHandler:
    """Start or restart the file watcher for the given directory."""
    global _file_observer, _file_handler, _watched_path
    
    data_path = str(Path(data_dir).expanduser().resolve())
    
    # If already watching the same path, return existing handler
    if _file_observer is not None and _watched_path == data_path:
        return _file_handler
    
    # Stop existing observer if watching different path
    if _file_observer is not None:
        _file_observer.stop()
        _file_observer.join(timeout=1)
    
    # Create new observer
    _file_handler = ExperimentFileHandler()
    _file_observer = Observer()
    _file_observer.schedule(_file_handler, data_path, recursive=True)
    _file_observer.start()
    _watched_path = data_path
    
    return _file_handler


def stop_file_watcher():
    """Stop the file watcher."""
    global _file_observer, _file_handler, _watched_path
    if _file_observer is not None:
        _file_observer.stop()
        _file_observer.join(timeout=1)
        _file_observer = None
        _file_handler = None
        _watched_path = None


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


def load_experiments_duckdb(data_dir: str) -> tuple[pd.DataFrame, dict]:
    """
    Load all experiment JSON files using DuckDB for efficient querying.
    
    DuckDB can query JSON files directly, allowing on-the-fly schema discovery
    without pre-processing. Falls back to Python loading if DuckDB fails.
    
    Args:
        data_dir: Base directory containing sweep runs
    
    Returns:
        Tuple of (DataFrame with flattened data, field_info dict with value samples)
    """
    data_path = Path(data_dir).expanduser()
    
    if not data_path.exists():
        return pd.DataFrame(), {}
    
    # Find all stdout.json files
    json_files = list(data_path.glob("**/stdout.json"))
    
    if not json_files:
        return pd.DataFrame(), {}
    
    records = []
    field_values = {}  # Track unique values per field for picker
    
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
            
            # Track field values for picker
            for k, v in flat.items():
                if k not in field_values:
                    field_values[k] = set()
                if v is not None and not isinstance(v, (list, dict)):
                    # Only track scalar values, limit to 50 unique
                    if len(field_values[k]) < 50:
                        field_values[k].add(v)
            
            records.append(flat)
        except (json.JSONDecodeError, IOError):
            continue
    
    if not records:
        return pd.DataFrame(), {}
    
    df = pd.DataFrame(records)
    
    # Convert field_values sets to sorted lists
    field_info = {}
    for k, v in field_values.items():
        try:
            field_info[k] = sorted(list(v), key=lambda x: (x is None, str(x)))
        except TypeError:
            field_info[k] = list(v)
    
    return df, field_info


def get_field_type(df: pd.DataFrame, col: str) -> str:
    """Determine if a field is numeric, categorical, or mixed."""
    if col not in df.columns:
        return "unknown"
    
    try:
        pd.to_numeric(df[col].dropna(), errors="raise")
        return "numeric"
    except (ValueError, TypeError):
        pass
    
    # Check if categorical (few unique values)
    if df[col].nunique() <= 20:
        return "categorical"
    
    return "text"


def find_default_field(df: pd.DataFrame, candidates: list, field_type: Optional[str] = None) -> Optional[str]:
    """Find the first available field from a list of candidates."""
    for candidate in candidates:
        if candidate in df.columns:
            if field_type is None:
                return candidate
            if get_field_type(df, candidate) == field_type:
                return candidate
    return None


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


def get_url_config() -> dict:
    """
    Get configuration from URL query parameters.
    
    Supports:
    - preset: Named preset from PRESETS dict
    - x, y, z: Axis field names
    - color: Color-by field
    - filter: Filter expression (field:value or field:~contains)
    - tab: Initial tab (2d, 3d, table, json, sql)
    - data_dir: Data directory override
    
    Returns:
        Dict with configuration values (empty dict if no params)
    """
    params = st.query_params
    config = {}
    
    # Check for preset first
    preset_name = params.get("preset")
    if preset_name and preset_name in PRESETS:
        config.update(PRESETS[preset_name])
        config["_preset_name"] = preset_name
    
    # Override with explicit params
    for key in ["x", "y", "z", "color", "filter", "tab", "data_dir"]:
        if key in params:
            config[key] = params[key]
    
    return config


def apply_filter_expression(df: pd.DataFrame, filter_expr: str) -> pd.DataFrame:
    """
    Apply a filter expression to the dataframe.
    
    Formats:
    - field:value - exact match
    - field:~pattern - contains pattern
    
    Args:
        df: Input dataframe
        filter_expr: Filter expression string
    
    Returns:
        Filtered dataframe
    """
    if ":" not in filter_expr:
        return df
    
    field, value = filter_expr.split(":", 1)
    
    if field not in df.columns:
        return df
    
    if value.startswith("~"):
        # Contains filter
        pattern = value[1:]
        return df[df[field].astype(str).str.contains(pattern, case=False, na=False)]
    else:
        # Exact match
        return df[df[field].astype(str) == value]


def main():
    st.set_page_config(
        page_title="Experiment Explorer",
        page_icon="📊",
        layout="wide"
    )
    
    # Get URL configuration
    url_config = get_url_config()
    
    st.title("📊 Experiment Explorer")
    
    # Show preset info if loaded from URL
    if "_preset_name" in url_config:
        st.markdown(f"**Preset: {url_config.get('title', url_config['_preset_name'])}**")
    else:
        st.markdown("Interactive visualization of quantum algorithm sweep results")
    
    # Sidebar for configuration
    with st.sidebar:
        st.header("Data Source")
        
        # Use URL param or default
        default_data_dir = url_config.get("data_dir", "~/q8020")
        data_dir = st.text_input(
            "Sweep output directory",
            value=default_data_dir,
            help="Directory containing sweep run outputs"
        )
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Reload"):
                st.cache_data.clear()
                st.rerun()
        with col2:
            live_watch = st.checkbox("👁️ Live", value=True, help="Watch for new experiments")
        
        # Start/manage file watcher
        if live_watch:
            handler = start_file_watcher(data_dir)
            st.caption("🟢 Watching for changes...")
        else:
            stop_file_watcher()
            st.caption("⏸️ Live watching paused")
    
    # Fragment that checks for file changes periodically without blocking UI
    @st.fragment(run_every=3)
    def check_for_changes():
        if live_watch and _file_handler is not None:
            if _file_handler.change_detected.is_set():
                _file_handler.change_detected.clear()
                st.cache_data.clear()
                st.rerun()
    
    check_for_changes()
    
    # Load data with caching
    @st.cache_data
    def cached_load(directory):
        return load_experiments_duckdb(directory)
    
    df, field_info = cached_load(data_dir)
    
    if df.empty:
        st.warning(f"No experiment data found in {data_dir}")
        st.info("Run some sweeps first, then reload.")
        return
    
    st.success(f"Loaded {len(df)} experiments from {df['_run_id'].nunique()} runs")
    
    # Get column types
    numeric_cols = get_numeric_columns(df)
    categorical_cols = get_categorical_columns(df)
    # Include _case_id and _run_id in categorical since they're useful for grouping
    meta_cols = ["_case_id", "_run_id"]
    for mc in meta_cols:
        if mc in df.columns and mc not in categorical_cols:
            categorical_cols.insert(0, mc)
    all_cols = sorted([c for c in df.columns if not c.startswith("_") or c in meta_cols])
    
    # Sidebar filters - enhanced with value previews
    with st.sidebar:
        st.header("🔍 Filters")
        st.caption("Filter experiments before plotting")
        
        # Dynamic filters based on categorical columns
        filters = {}
        filter_cols = st.multiselect(
            "Filter by attributes",
            options=categorical_cols,
            default=[],
            help="Select fields to filter on"
        )
        
        for col in filter_cols:
            unique_vals = field_info.get(col, df[col].dropna().unique().tolist())
            if isinstance(unique_vals, set):
                unique_vals = list(unique_vals)
            selected = st.multiselect(
                f"**{col}**",
                options=unique_vals,
                default=unique_vals,
                help=f"{len(unique_vals)} unique values"
            )
            if selected:
                filters[col] = selected
    
    # Apply filters
    filtered_df = df.copy()
    
    # Apply URL filter expression first
    url_filter = url_config.get("filter")
    if url_filter:
        filtered_df = apply_filter_expression(filtered_df, url_filter)
        st.sidebar.caption(f"🔗 URL filter: `{url_filter}`")
    
    # Apply interactive filters
    for col, values in filters.items():
        filtered_df = filtered_df[filtered_df[col].isin(values)]
    
    st.markdown(f"**Showing {len(filtered_df)} of {len(df)} experiments**")
    
    # Main content tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["🧊 3D Plot", "📈 2D Plot", "📋 Data Table", "🔍 Raw JSON", "🔧 SQL Query"])
    
    # 3D Plot tab (default view: case × algorithm × backend, colored by fidelity)
    with tab1:
        st.subheader("3D Scatter Plot")
        st.caption("Default: case × algorithm × metric (colored by algorithm)")
        
        # Smart defaults for 3D: case_id, metric, algorithm
        all_plottable = numeric_cols + categorical_cols
        
        # Find best defaults
        default_x = find_default_field(df, ["_case_id", "problem.dimension", "config.shots"]) or (all_plottable[0] if all_plottable else None)
        default_y = find_default_field(df, ["metrics.fidelity", "metrics.l2_error", "circuit_info.depth"]) or (all_plottable[1] if len(all_plottable) > 1 else None)
        default_z = find_default_field(df, ["circuit_info.num_qubits", "circuit_info.depth", "config.shots"]) or (all_plottable[2] if len(all_plottable) > 2 else None)
        default_color = find_default_field(df, ["algorithm", "config.backend", "status"]) or (categorical_cols[0] if categorical_cols else None)
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            x_idx = all_plottable.index(default_x) if default_x in all_plottable else 0
            x_axis_3d = st.selectbox("X Axis", options=all_plottable, index=x_idx, key="3d_x")
        with col2:
            y_idx = all_plottable.index(default_y) if default_y in all_plottable else min(1, len(all_plottable)-1)
            y_axis_3d = st.selectbox("Y Axis", options=all_plottable, index=y_idx, key="3d_y")
        with col3:
            z_idx = all_plottable.index(default_z) if default_z in all_plottable else min(2, len(all_plottable)-1)
            z_axis_3d = st.selectbox("Z Axis", options=all_plottable, index=z_idx, key="3d_z")
        with col4:
            color_opts = ["None"] + categorical_cols
            color_idx = color_opts.index(default_color) if default_color in color_opts else 0
            color_by_3d = st.selectbox("Color by", options=color_opts, index=color_idx, key="3d_color")
        
        if x_axis_3d and y_axis_3d and z_axis_3d:
            plot_df = filtered_df[[x_axis_3d, y_axis_3d, z_axis_3d]].copy()
            
            # Convert to numeric where possible
            for axis in [x_axis_3d, y_axis_3d, z_axis_3d]:
                if get_field_type(df, axis) == "numeric":
                    plot_df[axis] = pd.to_numeric(plot_df[axis], errors="coerce")
                else:
                    # For categorical, encode as integers for 3D positioning
                    plot_df[axis] = pd.Categorical(filtered_df[axis]).codes
            
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
            
            fig.update_layout(height=650)
            st.plotly_chart(fig, use_container_width=True)
    
    # 2D Plot tab
    with tab2:
        st.subheader("2D Scatter Plot")
        st.caption("Compare any two fields with optional color grouping")
        
        # Use URL config for defaults if provided, otherwise use smart defaults
        # Include categorical cols for x-axis (useful for case_id plots)
        all_2d_cols = numeric_cols + [c for c in categorical_cols if c not in numeric_cols]
        
        # Get defaults from URL config or fall back to smart defaults
        url_x = url_config.get("x")
        url_y = url_config.get("y")
        url_color = url_config.get("color")
        
        if url_x and url_x in all_2d_cols:
            default_2d_x = url_x
        else:
            default_2d_x = find_default_field(df, ["config.shots", "problem.dimension", "param.size"]) or (all_2d_cols[0] if all_2d_cols else None)
        
        if url_y and url_y in all_2d_cols:
            default_2d_y = url_y
        else:
            default_2d_y = find_default_field(df, ["metrics.fidelity", "metrics.l2_error"]) or (all_2d_cols[1] if len(all_2d_cols) > 1 else None)
        
        if url_color and url_color in categorical_cols:
            default_2d_color = url_color
        else:
            default_2d_color = find_default_field(df, ["algorithm", "config.backend"]) or None
        
        col1, col2, col3 = st.columns(3)
        with col1:
            x_idx = all_2d_cols.index(default_2d_x) if default_2d_x in all_2d_cols else 0
            x_axis = st.selectbox("X Axis", options=all_2d_cols, index=x_idx if all_2d_cols else None, key="2d_x")
        with col2:
            y_idx = all_2d_cols.index(default_2d_y) if default_2d_y in all_2d_cols else min(1, len(all_2d_cols)-1)
            y_axis = st.selectbox("Y Axis", options=all_2d_cols, index=y_idx if all_2d_cols else None, key="2d_y")
        with col3:
            color_opts = ["None"] + categorical_cols
            color_idx = color_opts.index(default_2d_color) if default_2d_color in color_opts else 0
            color_by = st.selectbox("Color by", options=color_opts, index=color_idx, key="2d_color")
        
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
    
    # SQL Query tab for advanced users
    with tab5:
        st.subheader("SQL Query (DuckDB)")
        st.caption("Query the experiment data using SQL. Table name is `experiments`.")
        
        default_query = """SELECT 
    algorithm, 
    COUNT(*) as count,
    AVG("metrics.fidelity") as avg_fidelity,
    AVG("circuit_info.depth") as avg_depth
FROM experiments 
GROUP BY algorithm
ORDER BY avg_fidelity DESC"""
        
        query = st.text_area("SQL Query", value=default_query, height=150)
        
        if st.button("▶️ Run Query"):
            try:
                # Register the filtered dataframe as a DuckDB table
                con = duckdb.connect()
                con.register("experiments", filtered_df)
                result = con.execute(query).fetchdf()
                st.dataframe(result, use_container_width=True)
                
                # Option to plot results
                if len(result.columns) >= 2:
                    st.markdown("---")
                    st.markdown("**Quick Plot from Results**")
                    qcol1, qcol2 = st.columns(2)
                    with qcol1:
                        qx = st.selectbox("X", options=result.columns.tolist(), key="sql_x")
                    with qcol2:
                        qy = st.selectbox("Y", options=result.columns.tolist(), index=min(1, len(result.columns)-1), key="sql_y")
                    
                    if qx and qy:
                        fig = px.bar(result, x=qx, y=qy, title=f"{qy} by {qx}")
                        st.plotly_chart(fig, use_container_width=True)
                
                con.close()
            except Exception as e:
                st.error(f"Query error: {e}")
    
    # Footer with available attributes and field dictionary
    with st.expander("📋 Field Dictionary", expanded=False):
        st.markdown("All discovered fields with sample values")
        
        # Build field dictionary table
        field_rows = []
        for col in sorted(all_cols):
            ftype = get_field_type(df, col)
            samples = field_info.get(col, [])
            if len(samples) > 5:
                sample_str = ", ".join(str(s) for s in samples[:5]) + f" ... ({len(samples)} total)"
            else:
                sample_str = ", ".join(str(s) for s in samples) if samples else "(no values)"
            field_rows.append({
                "Field": col,
                "Type": ftype,
                "Unique": df[col].nunique() if col in df.columns else 0,
                "Sample Values": sample_str
            })
        
        field_df = pd.DataFrame(field_rows)
        st.dataframe(field_df, use_container_width=True, height=300)
        
        # Quick stats
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Fields", len(all_cols))
        with col2:
            st.metric("Numeric Fields", len(numeric_cols))
        with col3:
            st.metric("Categorical Fields", len(categorical_cols))


if __name__ == "__main__":
    main()
