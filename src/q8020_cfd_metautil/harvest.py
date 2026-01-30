"""Harvest metadata fragments into a unified metadata.json.

Collects fragment files (q8020_experiment_0.json, q8020_case_0.json, etc.)
from an output directory and assembles them into a single metadata.json file.

Usage:
    q8020-harvest --outdir /path/to/experiment
    q8020-harvest --outdir /path/to/experiment --output metadata.json
    q8020-harvest --outdir /path/to/experiment --clean
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from q8020_cfd_metautil.meta_fragment import (
    FRAGMENT_PATTERN,
    MULTI_SECTIONS,
    SINGLETON_SECTIONS,
    VALID_SECTIONS,
    make_library_meta,
    read_fragments,
)


def harvest_metadata(
    outdir: Path,
) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    """
    Assemble metadata from fragment files.

    Args:
        outdir: Directory containing fragment files

    Returns:
        Tuple of (unified metadata dict, list of warnings, fragment counts by section)
    """
    fragments = read_fragments(outdir)
    warnings: list[str] = []
    metadata: dict[str, Any] = {}

    # Process singleton sections
    for section in SINGLETON_SECTIONS:
        section_fragments = fragments.get(section, [])
        if not section_fragments:
            metadata[section] = {}
        elif len(section_fragments) == 1:
            metadata[section] = section_fragments[0][1]
        else:
            warnings.append(
                f"Multiple fragments found for singleton section '{section}' "
                f"(indices: {[idx for idx, _ in section_fragments]}). Using index 0."
            )
            # Find index 0, or use first available
            idx_0_data = next(
                (data for idx, data in section_fragments if idx == 0),
                section_fragments[0][1],
            )
            metadata[section] = idx_0_data

    # Process multi-instance sections
    for section in MULTI_SECTIONS:
        section_fragments = fragments.get(section, [])
        if not section_fragments:
            metadata[section] = {}
        elif len(section_fragments) == 1:
            # Single fragment: use dict directly (unwrapped)
            metadata[section] = section_fragments[0][1]
        else:
            # Multiple fragments: assemble into list ordered by index
            metadata[section] = [data for _, data in section_fragments]

    # Inject library_versions into code section if not present
    if "library_versions" not in metadata.get("code", {}):
        if "code" not in metadata:
            metadata["code"] = {}
        metadata["code"]["library_versions"] = make_library_meta()

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Harvest metadata fragments into unified metadata.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  q8020-harvest --outdir /path/to/experiment
  q8020-harvest --outdir /path/to/experiment --output metadata.json
  q8020-harvest --outdir /path/to/experiment --clean
""",
    )
    parser.add_argument(
        "--outdir", "-d",
        type=str,
        required=True,
        help="Directory containing fragment files",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file path; defaults to <outdir>/metadata.json",
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

    output_path = Path(args.output) if args.output else outdir / "metadata.json"

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
