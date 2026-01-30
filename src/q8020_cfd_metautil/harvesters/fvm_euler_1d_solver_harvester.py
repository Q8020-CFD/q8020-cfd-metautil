"""Generate metadata JSON from FVM Euler 1D solver output directory.

Parses the output files from nozzle_1d_solver.py runs and produces a
structured metadata.json conforming to the make_meta schema.

Usage:
    fvm-euler-1d-meta --outdir /tmp/fvm
    fvm-euler-1d-meta --outdir /tmp/fvm --output metadata.json
"""

import argparse
import csv
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from q8020_cfd_metautil.harvest import harvest_metadata
from q8020_cfd_metautil.meta_fragment import (
    generate_experiment_id,
    generate_workflow_id,
    make_library_meta,
    make_user_meta,
    write_analysis,
    write_artifacts,
    write_backend,
    write_case,
    write_code,
    write_exec_stats,
    write_experiment,
    write_results,
)


def _parse_csv_to_dicts(csv_path: Path) -> list[dict[str, Any]]:
    """Parse a CSV file with header row into list of dicts."""
    if not csv_path.exists():
        return []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        return [row for row in reader]


def _parse_csv_to_floats(csv_path: Path) -> list[dict[str, float]]:
    """Parse CSV and convert all values to floats where possible."""
    rows = _parse_csv_to_dicts(csv_path)
    result = []
    for row in rows:
        converted = {}
        for k, v in row.items():
            k = k.strip()
            try:
                converted[k] = float(v)
            except (ValueError, TypeError):
                converted[k] = v
        result.append(converted)
    return result


def _load_pickle(pkl_path: Path) -> dict[str, Any] | None:
    """Load a pickle file, returning None if not found."""
    if not pkl_path.exists():
        return None
    with open(pkl_path, "rb") as f:
        return pickle.load(f)


def _inventory_files(outdir: Path) -> dict[str, list[dict[str, Any]]]:
    """Categorize files in output directory by type."""
    inventory: dict[str, list[dict[str, Any]]] = {
        "qpy_generated": [],
        "qpy_transpiled": [],
        "csv": [],
        "pkl": [],
        "png": [],
        "other": [],
    }

    for file_path in outdir.iterdir():
        if not file_path.is_file():
            continue

        stat = file_path.stat()
        info = {
            "name": file_path.name,
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z"),
        }

        if file_path.suffix == ".qpy":
            if "generated" in file_path.name:
                inventory["qpy_generated"].append(info)
            elif "transpile" in file_path.name:
                inventory["qpy_transpiled"].append(info)
            else:
                inventory["other"].append(info)
        elif file_path.suffix == ".csv":
            inventory["csv"].append(info)
        elif file_path.suffix == ".pkl":
            inventory["pkl"].append(info)
        elif file_path.suffix == ".png":
            inventory["png"].append(info)
        else:
            inventory["other"].append(info)

    return inventory


def _extract_run_params(outdir: Path) -> dict[str, Any]:
    """Extract run parameters from metadata pickle file."""
    # Find metadata pickle
    pkl_files = list(outdir.glob("metadata_*.pkl"))
    if not pkl_files:
        return {}

    metadata = _load_pickle(pkl_files[0])
    if metadata is None:
        return {}

    # Convert numpy types to Python types for JSON serialization
    result = {}
    for k, v in metadata.items():
        if hasattr(v, "item"):  # numpy scalar
            result[k] = v.item()
        elif isinstance(v, dict):
            result[k] = {
                kk: vv.item() if hasattr(vv, "item") else vv
                for kk, vv in v.items()
            }
        else:
            result[k] = v
    return result


def _extract_hhl_metrics(outdir: Path) -> list[dict[str, float]]:
    """Extract HHL metrics from CSV."""
    csv_files = list(outdir.glob("hhl_metrics_*.csv"))
    if not csv_files:
        return []
    return _parse_csv_to_floats(csv_files[0])


def _extract_qc_metadata(outdir: Path) -> list[dict[str, Any]]:
    """Extract quantum circuit metadata from CSV."""
    csv_files = list(outdir.glob("qc_metadata_*.csv"))
    if not csv_files:
        return []
    return _parse_csv_to_floats(csv_files[0])


def _extract_final_results(outdir: Path) -> list[dict[str, float]]:
    """Extract final solution results from CSV."""
    csv_files = list(outdir.glob("final_results_*.csv"))
    if not csv_files:
        return []
    return _parse_csv_to_floats(csv_files[0])


def _extract_residuals(outdir: Path) -> list[dict[str, float]]:
    """Extract residual history from CSV."""
    csv_files = list(outdir.glob("residual_*.csv"))
    if not csv_files:
        return []
    return _parse_csv_to_floats(csv_files[0])


def generate_metadata(outdir: Path) -> dict[str, Any]:
    """
    Generate structured metadata from FVM Euler 1D solver output.

    Args:
        outdir: Path to the solver output directory

    Returns:
        Metadata dict conforming to make_meta schema
    """
    run_params = _extract_run_params(outdir)
    hhl_metrics = _extract_hhl_metrics(outdir)
    qc_metadata = _extract_qc_metadata(outdir)
    final_results = _extract_final_results(outdir)
    residuals = _extract_residuals(outdir)
    file_inventory = _inventory_files(outdir)

    # Build experiment section
    experiment_id = generate_experiment_id()
    workflow_id = generate_workflow_id(experiment_id)
    timestamp = run_params.get(
        "timestamp",
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )

    experiment = {
        "name": "fvm_euler_1d",
        "experiment_id": experiment_id,
        "workflow_id": workflow_id,
        "timestamp": timestamp,
        "user": make_user_meta(),
    }

    # Build case section (problem definition)
    case = {
        "name": "nozzle_1d",
        "nelem": run_params.get("nelem"),
        "time_scheme": run_params.get("time_scheme"),
        "cfl": run_params.get("cfl"),
        "max_iters": run_params.get("max_iters"),
        "max_inner_iters": run_params.get("max_inner_iters"),
        "conv_tol": run_params.get("conv_tol"),
        "res_tol": run_params.get("res_tol"),
        "localdt": run_params.get("localdt"),
        "nondim": run_params.get("nondim"),
        "area_equation": run_params.get("area_equation"),
        "reference_values": {
            "rho_ref": run_params.get("rho_ref"),
            "u_ref": run_params.get("u_ref"),
            "p_ref": run_params.get("p_ref"),
        },
    }

    # Build code section
    code = {
        "algorithm": run_params.get("linear_solver", "HHL"),
        "entry_point": "nozzle_1d_solver.py",
        "library_versions": make_library_meta(),
    }

    # Build backend section
    backend = {
        "type": run_params.get("backend_type", "ideal"),
        "method": run_params.get("backend_method", "statevector"),
        "nshots": run_params.get("nshots", 0),
    }
    qc_config = run_params.get("quantum_circuit", {})
    if qc_config:
        backend["use_gpu"] = qc_config.get("use_gpu", False)

    # Build artifacts section
    artifacts = {
        "directory": str(outdir.resolve()),
        "circuits": {
            "generated": file_inventory["qpy_generated"],
            "transpiled": file_inventory["qpy_transpiled"],
        },
        "circuit_info": qc_metadata,
        "data_files": file_inventory["csv"],
        "pickles": file_inventory["pkl"],
        "plots": file_inventory["png"],
    }

    # Build exec_stats section
    exec_stats = {
        "elapsed_time": run_params.get("elapsed_time"),
        "final_iters": run_params.get("final_iters"),
        "final_residual": run_params.get("final_residual"),
    }

    # Build results section
    results = {
        "final_solution": final_results,
        "residual_history": residuals,
    }

    # Build analysis section with per-iteration metrics
    analysis = {
        "hhl_metrics": hhl_metrics,
    }

    # Write fragments
    write_experiment(outdir, experiment)
    write_case(outdir, case)
    write_code(outdir, code)
    write_backend(outdir, backend)
    write_artifacts(outdir, artifacts)
    write_exec_stats(outdir, exec_stats)
    write_results(outdir, results)
    write_analysis(outdir, analysis)

    # Harvest all fragments into unified metadata
    unified_metadata, warnings, _ = harvest_metadata(outdir)
    return unified_metadata, warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate metadata JSON from FVM Euler 1D solver output",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  fvm-euler-1d-meta --outdir /tmp/fvm
  fvm-euler-1d-meta --outdir /tmp/fvm --output metadata.json
  fvm-euler-1d-meta /path/to/_case_postproc.json  (sweep postproc mode)
""",
    )
    parser.add_argument(
        "--outdir", "-d",
        type=str,
        default=None,
        help="Path to solver output directory",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output JSON file path; defaults to <outdir>/metadata.json",
    )
    parser.add_argument(
        "postproc_json",
        nargs="?",
        default=None,
        help="Postproc JSON file from sweep (contains case_dir)",
    )

    args = parser.parse_args()

    # Determine outdir from either --outdir or postproc JSON
    if args.postproc_json:
        with open(args.postproc_json, "r", encoding="utf-8") as f:
            postproc_data = json.load(f)
        outdir = Path(postproc_data["case_dir"]).expanduser().resolve()
    elif args.outdir:
        outdir = Path(args.outdir).expanduser().resolve()
    else:
        print("Error: Must specify --outdir or provide postproc JSON", file=sys.stderr)
        sys.exit(1)

    if not outdir.exists():
        print(f"Error: Output directory does not exist: {outdir}", file=sys.stderr)
        sys.exit(1)

    metadata, warnings = generate_metadata(outdir)

    for warning in warnings:
        print(f"⚠️  {warning}", file=sys.stderr)

    output_path = Path(args.output) if args.output else outdir / "metadata.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"✅ Metadata written to: {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
