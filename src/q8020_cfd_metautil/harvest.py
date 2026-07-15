"""Harvest metadata fragments into a unified metadata.json.

Collects fragment files (q8020_experiment_0.json, q8020_case_0.json, etc.)
from a source directory and assembles them into a single metadata.json file.

Can also invoke solver-specific harvesters to extract metadata from solver
artifacts (CSVs, pickles, etc.) before rolling up into unified metadata.

The --source directory can be a single case dir or a parent directory
containing nested case dirs at any depth.  Case dirs are discovered
recursively (a case dir contains q8020 fragment files).

Usage:
    # Roll up a single case dir:
    q8020-harvest --source /path/to/case_dir
    
    # Roll up all case dirs under a parent (recursive):
    q8020-harvest --source /path/to/2025-11-11 --dest /tmp/harvest-out
    
    # Run solver harvester first, then roll up:
    q8020-harvest --source /path/to/2025-11-11 \
        --harvester /path/to/fvm_euler_1d_solver_harvester.py \
        --dest /tmp/harvest-out
"""

import argparse
import importlib.util
import json
import shutil
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

from q8020_cfd_metautil.metakeys import _walk_case_dirs


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
    read_only: bool = False,
) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    """
    Assemble metadata from fragment files.

    Also checks q8020_stdout.txt for JSON content and writes a results fragment
    if found (without clobbering existing results fragments).  When *read_only*
    is True the stdout fragment write is skipped so the source dir is not
    modified.

    Args:
        outdir: Directory containing fragment files
        read_only: If True, do not write any files back into *outdir*.

    Returns:
        Tuple of (unified metadata dict, list of warnings, fragment counts by section)
    """
    # Check stdout for JSON and write results fragment if found
    stdout_json = _extract_stdout_json(outdir)
    if stdout_json and not read_only:
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


def _walk_leaf_dirs(root: Path) -> list[Path]:
    """Return all leaf directories under *root* (dirs with no subdirectories).

    Used when ``--harvester`` is specified on a fresh directory tree that has
    no existing metadata or fragment files.  Leaf dirs are assumed to be
    individual case directories containing solver artifacts.
    """
    root = root.expanduser().resolve()
    leaves: list[Path] = []

    def _recurse(d: Path) -> None:
        try:
            children = sorted(d.iterdir())
        except PermissionError:
            return
        subdirs = [c for c in children if c.is_dir()]
        if not subdirs:
            # Leaf directory
            leaves.append(d)
        else:
            for sd in subdirs:
                _recurse(sd)

    _recurse(root)
    return leaves


def _copy_fragments(src: Path, dest: Path) -> None:
    """Copy existing q8020 fragment files from *src* to *dest*."""
    for p in src.iterdir():
        if p.is_file() and FRAGMENT_PATTERN.match(p.name):
            shutil.copy2(p, dest / p.name)


def _load_harvester_module(harvester_path: Path):
    """Load a harvester module from a file path."""
    harvester_path = harvester_path.expanduser().resolve()
    if not harvester_path.is_file():
        raise FileNotFoundError(f"Harvester not found: {harvester_path}")
    spec = importlib.util.spec_from_file_location(
        harvester_path.stem, harvester_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load harvester: {harvester_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "generate_metadata"):
        raise AttributeError(
            f"Harvester '{harvester_path.name}' lacks generate_metadata function"
        )
    return mod


def run_solver_harvester(
    outdir: Path,
    harvester_path: Path,
    experiment_id: str | None = None,
    write_dir: Path | None = None,
) -> str:
    """
    Run a solver-specific harvester to extract metadata from solver artifacts.

    This is used for standalone solver runs (outside of sweeper). It:
    1. Generates experiment_id if not provided
    2. Writes a generic experiment fragment with user/timestamp info
    3. Loads the harvester from *harvester_path* and runs it

    Args:
        outdir: Directory containing solver output artifacts
        harvester_path: Path to a Python file that exposes generate_metadata()
        experiment_id: Optional experiment ID; generated if not provided
        write_dir: Directory to write fragments to.  Defaults to *outdir*.

    Returns:
        The experiment_id used

    Raises:
        FileNotFoundError: If harvester file not found
        ImportError: If harvester cannot be loaded
        AttributeError: If harvester lacks generate_metadata function
    """
    if write_dir is None:
        write_dir = outdir

    harvester_module = _load_harvester_module(harvester_path)

    # Generate IDs for standalone run
    if experiment_id is None:
        experiment_id = generate_experiment_id()
    workflow_id = generate_workflow_id(experiment_id)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Write generic experiment fragment
    experiment_data = {
        "_source": "harvest",
        "name": harvester_path.stem,
        "experiment_id": experiment_id,
        "workflow_id": workflow_id,
        "timestamp": timestamp,
        "user": make_user_meta(),
    }
    write_experiment(write_dir, experiment_data, experiment_id=experiment_id)

    harvester_module.generate_metadata(
        outdir, experiment_id=experiment_id, write_dir=write_dir,
    )

    return experiment_id


def _harvest_one(
    source_dir: Path,
    dest_dir: Path | None = None,
    harvester_path: Path | None = None,
) -> Path:
    """Harvest a single case directory.

    Returns the path to the written metadata JSON.
    """
    # Where to write fragments and rollup.
    if dest_dir is not None:
        write_dir = dest_dir
        write_dir.mkdir(parents=True, exist_ok=True)
        # Only copy existing fragments when there's no harvester —
        # the harvester regenerates all fragments from scratch.
        if harvester_path is None:
            _copy_fragments(source_dir, write_dir)
    else:
        write_dir = source_dir

    # Run solver-specific harvester if specified.
    experiment_id = None
    if harvester_path:
        experiment_id = run_solver_harvester(
            source_dir, harvester_path, write_dir=dest_dir,
        )

    # Determine output path.
    if dest_dir is not None:
        output_path = dest_dir / f"q8020_metadata_{source_dir.name}.json"
    elif experiment_id:
        output_path = source_dir / f"q8020_metadata_{experiment_id}.json"
    else:
        output_path = source_dir / "metadata.json"

    # Roll up fragments.
    metadata, warnings, _ = harvest_metadata(
        write_dir, read_only=False,
    )

    for warning in warnings:
        print(f"⚠️  {warning}", file=sys.stderr)

    # Write output.
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return output_path


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Harvest metadata fragments into unified metadata.json.\n"
            "--source can be a single case dir or a parent directory;\n"
            "case dirs are discovered recursively."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Roll up a single case dir:
  q8020-harvest --source /path/to/case_dir

  # Recursively harvest all cases under a parent dir:
  q8020-harvest --source /path/to/2025-11-11 --dest /tmp/harvest-out

  # Run solver harvester on all cases, write to dest:
  q8020-harvest --source /path/to/2025-11-11 \\
      --harvester /path/to/fvm_euler_1d_solver_harvester.py \\
      --dest /tmp/out
""",
    )
    parser.add_argument(
        "--source", "-s",
        type=str,
        required=True,
        help=(
            "Source directory to harvest.  Can be a single case dir or a "
            "parent directory containing nested case dirs (recursive)."
        ),
    )
    parser.add_argument(
        "--harvester", "-H",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Path to a solver-specific harvester Python file that exposes "
            "generate_metadata(outdir, experiment_id, write_dir)."
        ),
    )
    parser.add_argument(
        "--dest", "-d",
        type=str,
        default=None,
        metavar="DIR",
        help=(
            "Write assembled metadata to a separate directory instead of "
            "the --source dir.  The source directory is never modified. "
            "Fragments and rollup are written to --dest."
        ),
    )

    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        print(f"Error: Directory does not exist: {source}", file=sys.stderr)
        sys.exit(1)

    dest_root: Path | None = None
    if args.dest:
        dest_root = Path(args.dest).expanduser().resolve()
        dest_root.mkdir(parents=True, exist_ok=True)

    # Discover case dirs recursively.
    case_dirs, no_meta_dirs = _walk_case_dirs(source)

    # If nothing found and a harvester is specified, fall back to leaf-dir
    # discovery (first-time harvest on a tree with no existing fragments).
    if not case_dirs and not no_meta_dirs and args.harvester:
        case_dirs = _walk_leaf_dirs(source)
        if case_dirs:
            print(
                f"ℹ️  No metadata/fragments found; discovered {len(case_dirs)} "
                f"leaf dirs for harvesting.",
                file=sys.stderr,
            )

    # If still nothing, treat source as a single case dir.
    if not case_dirs and not no_meta_dirs:
        case_dirs = [source]

    n_harvested = 0
    n_skipped = 0
    for case_dir in case_dirs:
        # Build per-case dest dir preserving relative structure.
        if dest_root is not None:
            rel = case_dir.relative_to(source) if case_dir != source else Path(case_dir.name)
            case_dest = dest_root / rel
        else:
            case_dest = None

        try:
            out = _harvest_one(case_dir, dest_dir=case_dest, harvester_path=args.harvester)
            n_harvested += 1
            print(f"✅ {case_dir.name} → {out}", file=sys.stderr)
        except Exception as e:
            n_skipped += 1
            print(f"❌ {case_dir.name}: {e}", file=sys.stderr)

    if no_meta_dirs:
        print(f"\n⚠️  {len(no_meta_dirs)} dirs with fragments but no assembled metadata", file=sys.stderr)

    print(f"\n📊 {n_harvested} harvested, {n_skipped} skipped", file=sys.stderr)


if __name__ == "__main__":
    main()
