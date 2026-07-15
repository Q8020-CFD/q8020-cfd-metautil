#!/usr/bin/env python3
"""Ingest q8020 metadata into a human-readable JSON index.

Walk a directory tree of the form:

    <root>/<date>/<workflow_id>/<case_id>/q8020_metadata_*.json

For each metadata file, extract a flat record and append it to the
index.  If a record with the same (workflow_id, case_id) already
exists, refuse unless --force is given.

Usage:
    q8020-corpus-ingest <dir> [<dir> ...] [-o index.json] [--force]

The index is a JSON file with structure:
    {
        "_meta": { "created": "...", "updated": "...", "count": N },
        "records": [
            { "workflow_id": "...", "case_id": "...", ... },
            ...
        ]
    }
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys


def _safe_get(meta: dict, section: str, key: str, default=None):
    """Extract a scalar value from the first entry in a metadata section."""
    entries = meta.get(section, [])
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict) and key in e:
                return e[key]
    return default


def _safe_get_all(meta: dict, section: str, key: str) -> list:
    """Extract a value from ALL entries in a metadata section that have it."""
    out = []
    entries = meta.get(section, [])
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict) and key in e:
                out.append(e[key])
    return out


def _extract_problem_size(meta: dict) -> dict:
    """Extract problem sizing fields from wherever they live."""
    size = {}

    # FVM Euler: nelem from case params
    params = _safe_get(meta, "case", "params")
    if isinstance(params, dict):
        for key in ("nelem", "-nelem"):
            if key in params:
                size["nelem"] = int(params[key])
                break

    # Direct nelem on case entry
    nelem = _safe_get(meta, "case", "nelem")
    if nelem is not None:
        size["nelem"] = int(nelem)

    # Burgers: q -> N = 2^q
    q = _safe_get(meta, "case", "q")
    if q is not None:
        size["q"] = int(q)
        size["grid_points"] = 2 ** int(q)

    # Linear system sizes
    for key in ("matrix_size_original", "matrix_size_hermitian"):
        v = _safe_get(meta, "case", key)
        if v is not None:
            size[key] = int(v)

    # nx, ny for 2D cases
    for key in ("nx", "ny"):
        v = _safe_get(meta, "case", key)
        if v is not None:
            size[key] = int(v)

    return size


def _extract_quality(meta: dict) -> dict:
    """Extract fidelity / error metrics from analysis section."""
    quality = {}
    fields = [
        "fidelity", "sv_fidelity", "fidelity_shots",
        "l2_error_abs", "l2_error_rel", "l2_error_normalized",
        "l2_error_phase_corrected",
        "final_error_epsilon", "max_error_epsilon",
        "residual", "converged", "iterations_completed",
    ]
    for f in fields:
        v = _safe_get(meta, "analysis", f)
        if v is not None:
            quality[f] = v
    return quality


def _extract_record(meta: dict, meta_path: str) -> dict:
    """Flatten one metadata file into an index record."""
    rec: dict = {}

    # Identity
    rec["workflow_id"] = _safe_get(meta, "experiment", "workflow_id", "unknown")
    rec["experiment_id"] = _safe_get(meta, "experiment", "experiment_id", "unknown")
    rec["case_id"] = _safe_get(meta, "case", "case_id") or _safe_get(meta, "experiment", "name", "unknown")
    rec["case_name"] = _safe_get(meta, "case", "name", "")
    rec["timestamp"] = _safe_get(meta, "experiment", "timestamp", "")

    # Code / algorithm
    rec["algorithm"] = _safe_get(meta, "code", "algorithm", "")
    rec["entry_point"] = _safe_get(meta, "code", "entry_point", "")

    # Backend
    rec["backend_name"] = _safe_get(meta, "backend", "name", "")
    rec["backend_type"] = _safe_get(meta, "backend", "type", "")
    rec["backend_noise"] = _safe_get(meta, "backend", "noise")
    rec["backend_nshots"] = (
        _safe_get(meta, "backend", "nshots")
        or _safe_get(meta, "analysis", "shots")
    )
    rec["backend_num_qubits"] = _safe_get(meta, "backend", "num_qubits")

    # Problem size
    rec["size"] = _extract_problem_size(meta)

    # Quality metrics
    rec["quality"] = _extract_quality(meta)

    # Condition number (lives in different places)
    for key in ("condition_number", "condition_number_original",
                "condition_number_hermitian", "max_condition_number"):
        v = _safe_get(meta, "case", key)
        if v is not None:
            rec.setdefault("condition_numbers", {})[key] = v

    # Timing
    rec["wall_time_s"] = _safe_get(meta, "exec_stats", "duration_seconds")
    rec["success"] = _safe_get(meta, "exec_stats", "success")

    # Case params (raw, for search)
    params = _safe_get(meta, "case", "params")
    if isinstance(params, dict):
        rec["params"] = {k: v for k, v in params.items() if not k.startswith("_")}

    # Provenance
    rec["_meta_path"] = os.path.abspath(meta_path)

    return rec


def _record_key(rec: dict) -> str:
    return f"{rec['workflow_id']}::{rec['case_id']}"


def load_index(path: str) -> dict:
    """Load an existing index or create an empty one."""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "_meta": {
            "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated": None,
            "count": 0,
        },
        "records": [],
    }


def save_index(index: dict, path: str) -> None:
    """Write the index to disk."""
    index["_meta"]["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    index["_meta"]["count"] = len(index["records"])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, default=str)
    print(f"Index written: {path}  ({index['_meta']['count']} records)")


def ingest_dir(root: str) -> list[dict]:
    """Find all q8020_metadata_*.json under root, return extracted records."""
    pattern = os.path.join(root, "**", "q8020_metadata_*.json")
    files = glob.glob(pattern, recursive=True)
    records = []
    errors = 0
    for fpath in sorted(files):
        try:
            with open(fpath, encoding="utf-8") as f:
                meta = json.load(f)
            rec = _extract_record(meta, fpath)
            records.append(rec)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            print(f"  WARN: {fpath}: {e}", file=sys.stderr)
            errors += 1
    print(f"  Scanned {root}: {len(records)} records, {errors} errors")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "dirs", nargs="+",
        help="Directory trees to scan for q8020_metadata_*.json",
    )
    parser.add_argument(
        "-o", "--output", default="corpus_index.json",
        help="Output index file (default: corpus_index.json)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing records with same (workflow_id, case_id)",
    )
    args = parser.parse_args()

    index = load_index(args.output)
    existing_keys = {_record_key(r) for r in index["records"]}

    new_records = []
    duplicates = 0
    for d in args.dirs:
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            print(f"  SKIP: not a directory: {d}", file=sys.stderr)
            continue
        for rec in ingest_dir(d):
            key = _record_key(rec)
            if key in existing_keys:
                if args.force:
                    index["records"] = [
                        r for r in index["records"]
                        if _record_key(r) != key
                    ]
                    existing_keys.discard(key)
                else:
                    duplicates += 1
                    continue
            new_records.append(rec)
            existing_keys.add(key)

    if duplicates:
        print(f"  Skipped {duplicates} duplicates (use --force to overwrite)")

    index["records"].extend(new_records)
    print(f"  Added {len(new_records)} new records")

    save_index(index, args.output)


if __name__ == "__main__":
    main()
