"""Compare metadata across multiple q8020 experiment cases.

Finds q8020_metadata_*.json files, flattens nested JSON into dot-separated
keys, and displays a table showing which parameters differ between cases.
"""

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any

# Keys skipped by default (too verbose or not useful for comparison).
DEFAULT_SKIP_PATTERNS = [
    "artifacts*",
    "*library_versions*",
    "*.command*",
    "*.cwd",
    "*.directory",
    "*.path",
    "*.files*",
    "*._source",
    "*.coupling_map*",
    "*.readout_error*",
    "*.gate_error*",
    "*.t1.*",
    "*.t2.*",
    "results.*.per_iteration*",
    "results.*.residual_history*",
    "results.*.final_solution*",
    "results.*.*_solution*",
    "results.*.*_vector*",
    "results.*.dq*",
    "analysis.*.hhl_metrics*",
    "case.*.params.*",
    "case.*.matrix*",
    "case.*.rhs*",
    "case.*.eigenvalues*",
]

# Keys whose values are filesystem paths -- skip them.
_PATH_RE = re.compile(r"^(/|~|[A-Z]:\\)")

# Depth at which nested containers are summarised instead of expanded.
_MAX_DEPTH = 3


# -- directory resolution ---------------------------------------------------

def _find_metadata(case_dir: Path) -> Path | None:
    """Return the first q8020_metadata_*.json in *case_dir*, or None."""
    for p in sorted(case_dir.glob("q8020_metadata_*.json")):
        return p
    return None


def resolve_dirs(paths: list[Path]) -> list[Path]:
    """Expand paths into case directories containing metadata files.

    Each path is either a case dir (contains q8020_metadata_*.json) or a
    sweep dir whose immediate subdirectories are case dirs.
    """
    case_dirs: list[Path] = []
    for p in paths:
        p = p.expanduser().resolve()
        if not p.is_dir():
            print(f"Warning: not a directory, skipping: {p}", file=sys.stderr)
            continue
        if _find_metadata(p):
            case_dirs.append(p)
        else:
            # Try subdirs (sweep directory).
            subs = sorted(
                s for s in p.iterdir()
                if s.is_dir() and _find_metadata(s)
            )
            if subs:
                case_dirs.extend(subs)
            else:
                print(
                    f"Warning: no metadata found in {p}",
                    file=sys.stderr,
                )
    return case_dirs


# -- metadata loading -------------------------------------------------------

def load_metadata(case_dir: Path) -> dict[str, Any]:
    """Load the unified metadata JSON from a case directory."""
    meta_path = _find_metadata(case_dir)
    if meta_path is None:
        raise FileNotFoundError(
            f"No q8020_metadata_*.json in {case_dir}"
        )
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


# -- flattening --------------------------------------------------------------

def _summarise(obj: Any) -> str:
    """Return a short summary string for a non-scalar value."""
    if isinstance(obj, list):
        return f"[{len(obj)} items]"
    if isinstance(obj, dict):
        return f"{{{len(obj)} keys}}"
    return str(obj)


def flatten(
    obj: Any,
    prefix: str = "",
    depth: int = 0,
) -> dict[str, Any]:
    """Recursively flatten *obj* into dot-separated scalar keys.

    Lists are indexed (e.g. ``backend.0.name``).  Containers deeper than
    ``_MAX_DEPTH`` are summarised rather than expanded.
    """
    out: dict[str, Any] = {}
    if depth >= _MAX_DEPTH:
        out[prefix] = _summarise(obj)
        return out

    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)) and v:
                out.update(flatten(v, key, depth + 1))
            else:
                out[key] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}.{i}" if prefix else str(i)
            if isinstance(v, (dict, list)) and v:
                out.update(flatten(v, key, depth + 1))
            else:
                out[key] = v
    else:
        out[prefix] = obj

    return out


# -- filtering ---------------------------------------------------------------

def _should_skip(key: str, skip_patterns: list[str]) -> bool:
    """Return True if *key* matches any skip pattern or looks like a path."""
    for pat in skip_patterns:
        if fnmatch.fnmatch(key, pat):
            return True
    return False


def _looks_like_path(value: Any) -> bool:
    """Return True if *value* looks like a filesystem path."""
    if not isinstance(value, str):
        return False
    return bool(_PATH_RE.match(value))


# -- comparison --------------------------------------------------------------

def compare(
    records: list[dict[str, Any]],
    skip_patterns: list[str] | None = None,
    field_filter: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """Partition flattened keys into same-across-all vs differing.

    Returns (same, diff) where:
      same  = {key: value} for keys identical in every record
      diff  = {key: [val_per_record]} for keys that differ
    """
    if skip_patterns is None:
        skip_patterns = DEFAULT_SKIP_PATTERNS

    all_keys: set[str] = set()
    for rec in records:
        all_keys.update(rec.keys())

    # Apply field filter if given.
    if field_filter is not None:
        allowed = set(field_filter)
        all_keys = all_keys & allowed

    # Filter out skipped keys and path-like values.
    filtered_keys: list[str] = []
    for key in sorted(all_keys):
        if _should_skip(key, skip_patterns):
            continue
        # Skip if every non-missing value looks like a path.
        vals = [rec.get(key) for rec in records]
        real_vals = [v for v in vals if v is not None]
        if real_vals and all(_looks_like_path(v) for v in real_vals):
            continue
        filtered_keys.append(key)

    same: dict[str, Any] = {}
    diff: dict[str, list[Any]] = {}

    for key in filtered_keys:
        vals = [rec.get(key, None) for rec in records]
        # Normalise for comparison (JSON types can vary).
        strs = [json.dumps(v, sort_keys=True) if v is not None else None
                for v in vals]
        unique = set(strs)
        if len(unique) == 1:
            same[key] = vals[0]
        else:
            diff[key] = vals

    return same, diff


# -- table rendering ---------------------------------------------------------

def _fmt(value: Any, width: int) -> str:
    """Format a value for display, truncating to *width*."""
    if value is None:
        s = "-"
    elif isinstance(value, float):
        s = f"{value:.6g}"
    elif isinstance(value, bool):
        s = str(value)
    elif isinstance(value, (dict, list)):
        s = _summarise(value)
    else:
        s = str(value)
    if len(s) > width:
        s = s[: width - 1] + "\u2026"
    return s


def _case_label(record: dict[str, Any]) -> str:
    """Derive a short label for a case from its metadata."""
    # Prefer case_id, then experiment_id, then fall back to index.
    for key in ("case.0.case_id", "experiment.0.experiment_id"):
        if key in record:
            return str(record[key])
    return "?"


def render_table(
    same: dict[str, Any],
    diff: dict[str, list[Any]],
    labels: list[str],
    show_all: bool = False,
) -> None:
    """Print the comparison table to stdout."""
    n = len(labels)
    col_width = max(20, max((len(lb) for lb in labels), default=20))
    key_width = 40

    # Header.
    hdr = f"{'key':<{key_width}}"
    for lb in labels:
        hdr += f"  {lb:>{col_width}}"
    print(hdr)
    print("-" * len(hdr))

    # Differing rows.
    for key in sorted(diff):
        row = f"{key:<{key_width}}"
        for val in diff[key]:
            row += f"  {_fmt(val, col_width):>{col_width}}"
        print(row)

    # Same rows (collapsed or expanded).
    if same:
        if show_all:
            print()
            print(f"--- identical across all {n} cases ---")
            for key in sorted(same):
                row = f"{key:<{key_width}}  {_fmt(same[key], col_width)}"
                print(row)
        else:
            print()
            print(f"({len(same)} parameters identical across all cases)")


# -- --keys mode -------------------------------------------------------------

def print_keys(
    records: list[dict[str, Any]],
    skip_patterns: list[str] | None = None,
) -> None:
    """Print sorted list of flattened keys after default filtering."""
    if skip_patterns is None:
        skip_patterns = DEFAULT_SKIP_PATTERNS

    all_keys: set[str] = set()
    for rec in records:
        all_keys.update(rec.keys())

    for key in sorted(all_keys):
        if _should_skip(key, skip_patterns):
            continue
        vals = [rec.get(key) for rec in records]
        real_vals = [v for v in vals if v is not None]
        if real_vals and all(_looks_like_path(v) for v in real_vals):
            continue
        print(key)


# -- CLI entry point ---------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare q8020 metadata across experiment cases.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "dirs",
        nargs="+",
        type=Path,
        metavar="DIR",
        help="Case or sweep directories to compare",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="show_all",
        help="Show all rows, not just differences",
    )
    parser.add_argument(
        "--skip",
        type=str,
        default=None,
        metavar="PATTERN",
        help="Additional glob pattern for keys to skip",
    )
    parser.add_argument(
        "--fields",
        type=str,
        default=None,
        metavar="KEY,KEY",
        help="Comma-separated list of fields to compare (only these)",
    )
    parser.add_argument(
        "--keys",
        action="store_true",
        help="List available flattened field names and exit",
    )
    args = parser.parse_args()

    case_dirs = resolve_dirs(args.dirs)
    if not case_dirs:
        print("Error: no case directories found.", file=sys.stderr)
        sys.exit(1)

    # Load and flatten.
    records: list[dict[str, Any]] = []
    for cd in case_dirs:
        try:
            meta = load_metadata(cd)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"Error loading {cd}: {exc}", file=sys.stderr)
            sys.exit(1)
        records.append(flatten(meta))

    # Build skip patterns.
    skip = list(DEFAULT_SKIP_PATTERNS)
    if args.skip:
        skip.append(args.skip)

    # --keys mode: print field names and exit.
    if args.keys:
        print_keys(records, skip_patterns=skip)
        sys.exit(0)

    if len(records) < 2:
        print(
            "Error: need at least 2 cases to compare.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build field filter.
    field_filter = None
    if args.fields:
        field_filter = [f.strip() for f in args.fields.split(",")]

    labels = [_case_label(rec) for rec in records]
    same, diff = compare(records, skip_patterns=skip, field_filter=field_filter)

    if not diff and not same:
        print("No comparable fields found.", file=sys.stderr)
        sys.exit(0)

    render_table(same, diff, labels, show_all=args.show_all)


if __name__ == "__main__":
    main()
