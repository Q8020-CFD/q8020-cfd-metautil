"""Report the union of all flattened metadata keys with per-key coverage.

Scans case directories, reads assembled metadata JSONs, and prints a table
showing which keys exist and how many cases contain each one.  Read-only --
does not harvest, modify, or write any files.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from q8020_cfd_metautil.compare import (
    _find_metadata,
    _should_skip,
    flatten,
    load_metadata,
)
from q8020_cfd_metautil.meta_fragment import FRAGMENT_PATTERN


# -- recursive directory discovery -------------------------------------------

def _has_fragment(d: Path) -> bool:
    """Return True if *d* contains at least one q8020 fragment file."""
    for p in d.iterdir():
        if p.is_file() and FRAGMENT_PATTERN.match(p.name):
            return True
    return False


def _walk_case_dirs(root: Path) -> tuple[list[Path], list[Path]]:
    """Recursively find case dirs under *root*.

    Returns (with_metadata, no_metadata) where:
      with_metadata = dirs containing q8020_metadata_*.json
      no_metadata   = dirs containing fragment files but no assembled metadata

    A directory that contains metadata is a leaf -- its children are not
    searched.  A directory that contains fragments but no metadata is counted
    as a no-metadata case (also a leaf).  Otherwise the directory's
    subdirectories are searched recursively.
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        return [], []

    # Is root itself a case dir?
    if _find_metadata(root):
        return [root], []

    try:
        has_frag = _has_fragment(root)
    except PermissionError:
        return [], []

    if has_frag:
        # Fragments but no assembled metadata.
        return [], [root]

    # Neither metadata nor fragments -- recurse into children.
    with_meta: list[Path] = []
    no_meta: list[Path] = []
    try:
        children = sorted(root.iterdir())
    except PermissionError:
        return [], []
    for child in children:
        if not child.is_dir():
            continue
        wm, nm = _walk_case_dirs(child)
        with_meta.extend(wm)
        no_meta.extend(nm)
    return with_meta, no_meta


# -- key collection ----------------------------------------------------------

def collect_key_counts(
    case_dirs: list[Path],
    skip_patterns: list[str] | None = None,
    section: str | None = None,
) -> tuple[dict[str, int], int]:
    """Load metadata from *case_dirs* and count per-key occurrences.

    Returns (key_counts, n_cases_with_metadata).
    """
    key_counts: dict[str, int] = {}
    loaded = 0
    for cd in case_dirs:
        try:
            meta = load_metadata(cd)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"Warning: skipping {cd}: {exc}", file=sys.stderr)
            continue
        loaded += 1
        flat = flatten(meta)
        for key in flat:
            if skip_patterns and _should_skip(key, skip_patterns):
                continue
            if section and not key.startswith(section + "."):
                continue
            key_counts[key] = key_counts.get(key, 0) + 1
    return key_counts, loaded


# -- output ------------------------------------------------------------------

def render_table(
    key_counts: dict[str, int],
    total_cases: int,
    no_metadata: int,
) -> None:
    """Print a plain-text coverage table to stdout."""
    key_width = 40
    if key_counts:
        key_width = max(key_width, max(len(k) for k in key_counts) + 2)

    hdr = f"{'key':<{key_width}} {'count':>6}   {'pct':>6}"
    print(hdr)
    print("-" * len(hdr))

    # "(no metadata)" row first, if any.
    if no_metadata > 0:
        pct = no_metadata / total_cases * 100 if total_cases else 0
        print(f"{'(no metadata)':<{key_width}} {no_metadata:>6}   {pct:>5.1f}%")

    for key in sorted(key_counts):
        count = key_counts[key]
        pct = count / total_cases * 100 if total_cases else 0
        print(f"{key:<{key_width}} {count:>6}   {pct:>5.1f}%")

    n_keys = len(key_counts)
    print()
    print(f"{total_cases} cases, {n_keys} unique keys")


def render_json(
    key_counts: dict[str, int],
    total_cases: int,
    no_metadata: int,
) -> None:
    """Print JSON output to stdout."""
    keys_obj: dict[str, dict[str, Any]] = {}
    for key in sorted(key_counts):
        count = key_counts[key]
        pct = round(count / total_cases * 100, 1) if total_cases else 0
        keys_obj[key] = {"count": count, "pct": pct}

    out = {
        "total_cases": total_cases,
        "no_metadata": no_metadata,
        "keys": keys_obj,
    }
    print(json.dumps(out, indent=2))


def _short_name(key: str) -> str:
    """Return the last token of a dotted key name."""
    return key.rsplit(".", 1)[-1]


def render_short_names(
    key_counts: dict[str, int],
    combine: bool = False,
) -> None:
    """Print just the key names, one per line.

    With *combine* the last dot-separated token is printed (deduplicated).
    Without *combine* the full dotted key name is printed.
    """
    if combine:
        seen: set[str] = set()
        for key in sorted(key_counts):
            short = _short_name(key)
            if short not in seen:
                seen.add(short)
                print(short)
    else:
        for key in sorted(key_counts):
            print(key)


def render_combine_json(
    key_counts: dict[str, int],
    total_cases: int,
    no_metadata: int,
) -> None:
    """Print JSON keyed by last token, with full key names listed."""
    groups: dict[str, list[str]] = {}
    for key in sorted(key_counts):
        short = _short_name(key)
        groups.setdefault(short, []).append(key)

    keys_obj: dict[str, dict[str, Any]] = {}
    for short in sorted(groups):
        full_keys = groups[short]
        counts = [key_counts[k] for k in full_keys]
        pcts = [round(c / total_cases * 100, 1) if total_cases else 0
                for c in counts]
        keys_obj[short] = {
            "full_keys": full_keys,
            "count": counts[0] if len(counts) == 1 else counts,
            "pct": pcts[0] if len(pcts) == 1 else pcts,
        }

    out = {
        "total_cases": total_cases,
        "no_metadata": no_metadata,
        "keys": keys_obj,
    }
    print(json.dumps(out, indent=2))


# -- CLI entry point ---------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Report flattened metadata key coverage across experiment cases.\n"
            "By default outputs JSON with every key, its count, and percentage.\n"
            "All keys are shown (no skip patterns) unless --skip is used."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "dirs",
        nargs="+",
        type=Path,
        metavar="DIR",
        help="One or more case or sweep directories to scan (recursive)",
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="PAT",
        help=(
            "Glob pattern to suppress matching keys from output. "
            "Repeatable, e.g. --skip 'artifacts*' --skip '*._source'"
        ),
    )
    parser.add_argument(
        "--section",
        type=str,
        default=None,
        metavar="SEC",
        help="Limit output to keys under a top-level section (e.g. --section results)",
    )
    parser.add_argument(
        "--common",
        action="store_true",
        help=(
            "Only show keys present in every case (100%% coverage). "
            "Percentages are relative to cases with metadata."
        ),
    )
    parser.add_argument(
        "--short",
        action="store_true",
        help=(
            "Print just the key names, one per line (no counts or JSON). "
            "Combine with --combine to print only the last token of each key."
        ),
    )
    parser.add_argument(
        "--combine",
        action="store_true",
        help=(
            "Group keys by their last dot-separated token. "
            "JSON output lists full key names under each short name. "
            "With --short, prints deduplicated short names only."
        ),
    )
    parser.add_argument(
        "--table",
        action="store_true",
        help="Output as plain-text table instead of JSON (default is JSON)",
    )
    args = parser.parse_args()

    # Recursively discover case dirs.
    case_dirs: list[Path] = []
    no_meta_dirs: list[Path] = []
    for d in args.dirs:
        wm, nm = _walk_case_dirs(d)
        case_dirs.extend(wm)
        no_meta_dirs.extend(nm)
    no_metadata = len(no_meta_dirs)

    if not case_dirs and not no_meta_dirs:
        print("Error: no case directories found.", file=sys.stderr)
        sys.exit(1)

    total_cases = len(case_dirs) + no_metadata

    skip = args.skip or None

    key_counts, n_loaded = collect_key_counts(
        case_dirs,
        skip_patterns=skip,
        section=args.section,
    )

    if args.common and n_loaded > 0:
        key_counts = {k: v for k, v in key_counts.items() if v == n_loaded}
        # In --common mode, percentages are relative to cases with metadata.
        total_cases = n_loaded
        no_metadata = 0

    if args.short:
        render_short_names(key_counts, combine=args.combine)
    elif args.table:
        render_table(key_counts, total_cases, no_metadata)
    elif args.combine:
        render_combine_json(key_counts, total_cases, no_metadata)
    else:
        render_json(key_counts, total_cases, no_metadata)


if __name__ == "__main__":
    main()
