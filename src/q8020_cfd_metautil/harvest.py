"""Harvest metadata fragments into a unified metadata.json.

Collects fragment files (q8020_experiment_0.json, q8020_case_0.json, etc.)
from an output directory and assembles them into a single metadata.json file.

Can also invoke solver-specific harvesters to extract metadata from solver
artifacts (CSVs, pickles, etc.) before rolling up into unified metadata.

Usage:
    # Roll up existing fragments only:
    q8020-harvest --outdir /path/to/experiment
    
    # Run solver-specific harvester first, then roll up:
    q8020-harvest --outdir /path/to/solver_output --harvester fvm_euler_1d
    
    q8020-harvest --outdir /path/to/experiment --output metadata.json
    q8020-harvest --outdir /path/to/experiment --clean
"""

import argparse
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from q8020_cfd_metautil.meta_fragment import (
    FRAGMENT_PATTERN,
    SWEEP_FRAGMENT_PATTERN,
    VALID_SECTIONS,
    generate_experiment_id,
    generate_workflow_id,
    make_user_meta,
    read_fragments,
    write_experiment,
    write_results,
)

# Re-export patterns for use by other modules
__all__ = [
    "harvest_metadata",
    "get_fragment_files",
    "run_solver_harvester",
    "FRAGMENT_PATTERN",
    "SWEEP_FRAGMENT_PATTERN",
]


def _extract_stdout_json(outdir: Path) -> dict[str, Any] | None:
    """
    Check stdout file for JSON content.
    
    Looks for q8020_stdout.txt or q8020_stdout_<exp_id>.txt.
    Returns parsed JSON dict if stdout looks like JSON, else None.
    """
    outdir = Path(outdir)
    
    # Find stdout file - could be q8020_stdout.txt or q8020_stdout_<exp_id>.txt
    stdout_file = None
    for filepath in outdir.iterdir():
        if filepath.is_file() and filepath.name.startswith("q8020_stdout"):
            stdout_file = filepath
            break
    
    if stdout_file is None:
        return None
    
    try:
        content = stdout_file.read_text(encoding="utf-8").strip()
        if not content:
            return None
        # Try to parse as JSON
        data = json.loads(content)
        if isinstance(data, dict):
            return data
        return None
    except (json.JSONDecodeError, OSError):
        return None


def _next_results_index(outdir: Path) -> int:
    """Find the next available index for results fragments."""
    max_index = -1
    for filepath in outdir.iterdir():
        if not filepath.is_file():
            continue
        # Check both patterns for results fragments
        for pattern in [SWEEP_FRAGMENT_PATTERN, FRAGMENT_PATTERN]:
            match = pattern.match(filepath.name)
            if match and match.group(1) == "results":
                idx = int(match.group(3))
                max_index = max(max_index, idx)
                break
    return max_index + 1


def harvest_metadata(
    outdir: Path,
) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    """
    Assemble metadata from fragment files.

    Also checks q8020_stdout.txt for JSON content and writes a results fragment
    if found (without clobbering existing results fragments).

    Args:
        outdir: Directory containing fragment files

    Returns:
        Tuple of (unified metadata dict, list of warnings, fragment counts by section)
    """
    # Check stdout for JSON and write results fragment if found
    stdout_json = _extract_stdout_json(outdir)
    if stdout_json:
        next_idx = _next_results_index(outdir)
        write_results(outdir, {"_source": "stdout", **stdout_json}, index=next_idx)

    fragments = read_fragments(outdir)
    warnings: list[str] = []
    metadata: dict[str, Any] = {}

    # All sections are multi - collect all fragments into lists
    for section in VALID_SECTIONS:
        section_fragments = fragments.get(section, [])
        if not section_fragments:
            metadata[section] = []
        else:
            # Collect all fragments ordered by index
            metadata[section] = [data for _, data in section_fragments]

    # Build fragment counts
    fragment_counts = {section: len(frags) for section, frags in fragments.items()}

    return metadata, warnings, fragment_counts


def get_fragment_files(outdir: Path) -> list[Path]:
    """Get list of fragment files in directory."""
    outdir = Path(outdir)
    if not outdir.exists():
        return []

    fragment_files = []
    for filepath in outdir.iterdir():
        if filepath.is_file() and FRAGMENT_PATTERN.match(filepath.name):
            fragment_files.append(filepath)
    return fragment_files


def run_solver_harvester(
    outdir: Path,
    harvester_name: str,
    experiment_id: str | None = None,
) -> str:
    """
    Run a solver-specific harvester to extract metadata from solver artifacts.

    This is used for standalone solver runs (outside of sweeper). It:
    1. Generates experiment_id if not provided
    2. Writes a generic experiment fragment with user/timestamp info
    3. Dynamically loads and runs the solver-specific harvester

    Args:
        outdir: Directory containing solver output artifacts
        harvester_name: Name of harvester (e.g., "fvm_euler_1d" loads
                        q8020_cfd_metautil.harvesters.fvm_euler_1d_harvester)
        experiment_id: Optional experiment ID; generated if not provided

    Returns:
        The experiment_id used

    Raises:
        ImportError: If harvester module not found
        AttributeError: If harvester lacks generate_metadata function
    """
    # Generate IDs for standalone run
    if experiment_id is None:
        experiment_id = generate_experiment_id()
    workflow_id = generate_workflow_id(experiment_id)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Write generic experiment fragment (sweeper would do this, but we're standalone)
    experiment_data = {
        "_source": "harvest",
        "name": harvester_name,
        "experiment_id": experiment_id,
        "workflow_id": workflow_id,
        "timestamp": timestamp,
        "user": make_user_meta(),
    }
    write_experiment(outdir, experiment_data, experiment_id=experiment_id)

    # Dynamically load solver-specific harvester
    module_name = f"q8020_cfd_metautil.harvesters.{harvester_name}_harvester"
    try:
        harvester_module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(
            f"Could not load harvester module '{module_name}': {e}"
        ) from e

    # Call the harvester's generate_metadata function
    if not hasattr(harvester_module, "generate_metadata"):
        raise AttributeError(
            f"Harvester module '{module_name}' lacks generate_metadata function"
        )

    harvester_module.generate_metadata(outdir, experiment_id=experiment_id)

    return experiment_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Harvest metadata fragments into unified metadata.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Roll up existing fragments:
  q8020-harvest --outdir /path/to/experiment
  
  # Run solver harvester first, then roll up:
  q8020-harvest --outdir /path/to/solver_output --harvester fvm_euler_1d
  
  q8020-harvest --outdir /path/to/experiment --output metadata.json
  q8020-harvest --outdir /path/to/experiment --clean
""",
    )
    parser.add_argument(
        "--outdir", "-d",
        type=str,
        required=True,
        help="Directory containing solver output or fragment files",
    )
    parser.add_argument(
        "--harvester", "-H",
        type=str,
        default=None,
        help="Solver-specific harvester name (e.g., 'fvm_euler_1d')",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file path; defaults to <outdir>/q8020_metadata_<exp_id>.json",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Overwrite existing metadata.json",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove fragment files after successful assembly",
    )

    args = parser.parse_args()

    outdir = Path(args.outdir).expanduser().resolve()
    if not outdir.exists():
        print(f"Error: Directory does not exist: {outdir}", file=sys.stderr)
        sys.exit(1)

    # Run solver-specific harvester if specified
    experiment_id = None
    if args.harvester:
        try:
            experiment_id = run_solver_harvester(outdir, args.harvester)
            print(f"🔧 Ran {args.harvester} harvester (exp_id: {experiment_id})", file=sys.stderr)
        except (ImportError, AttributeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    elif experiment_id:
        output_path = outdir / f"q8020_metadata_{experiment_id}.json"
    else:
        output_path = outdir / "metadata.json"

    if output_path.exists() and not args.force:
        print(
            f"Error: {output_path} already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Harvest fragments
    metadata, warnings, fragment_counts = harvest_metadata(outdir)

    # Print warnings
    for warning in warnings:
        print(f"⚠️  {warning}", file=sys.stderr)

    # Print fragment counts
    print("📊 Fragments found:", file=sys.stderr)
    for section in VALID_SECTIONS:
        count = fragment_counts.get(section, 0)
        if count > 0:
            print(f"   {section}: {count}", file=sys.stderr)

    # Write output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"✅ Metadata written to: {output_path}", file=sys.stderr)

    # Clean up fragments if requested
    if args.clean:
        fragment_files = get_fragment_files(outdir)
        for filepath in fragment_files:
            filepath.unlink()
        print(f"🧹 Removed {len(fragment_files)} fragment files", file=sys.stderr)


if __name__ == "__main__":
    main()
