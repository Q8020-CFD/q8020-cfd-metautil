"""Compare original vs re-harvested metadata at unlimited flatten depth."""
import json
import sys
from pathlib import Path

from q8020_cfd_metautil.compare import flatten

SRC = Path("/home/agallojr/proj/src/q8020-cfd/q8020-cfd-experiments/results/fvm_euler_1d_solver/2025-11-11")
DEST = Path("/tmp/final-test")
SKIP_KEYS = {"file_inventory", "experiment_id", "workflow_id", "timestamp",
             "name", "user", "hostname", "modified"}
SKIP_SECTIONS = {"experiment"}


def strip_volatile(obj, depth=0):
    """Recursively remove volatile keys/sections for comparison."""
    if isinstance(obj, dict):
        return {
            k: strip_volatile(v, depth + 1)
            for k, v in obj.items()
            if k not in SKIP_KEYS and not (depth == 0 and k in SKIP_SECTIONS)
        }
    if isinstance(obj, list):
        return [strip_volatile(item, depth + 1) for item in obj]
    return obj


def main():
    ok = diff = missing = 0
    diff_details = []

    for orig_meta in sorted(SRC.rglob("q8020_metadata_*.json")):
        case_dir = orig_meta.parent
        rel = case_dir.relative_to(SRC)
        new_dir = DEST / rel
        new_candidates = sorted(new_dir.glob("q8020_metadata_*.json"))
        if not new_candidates:
            missing += 1
            print(f"  MISSING: {rel}")
            continue

        new_path = new_candidates[0]

        orig = strip_volatile(json.loads(orig_meta.read_text()))
        new = strip_volatile(json.loads(new_path.read_text()))

        # Flatten both at unlimited depth for deep comparison
        orig_flat = flatten(orig, max_depth=0)
        new_flat = flatten(new, max_depth=0)

        if orig_flat == new_flat:
            ok += 1
        else:
            diff += 1
            # Collect details on first few diffs
            all_keys = sorted(set(orig_flat) | set(new_flat))
            case_diffs = []
            for k in all_keys:
                ov = orig_flat.get(k, "<ABSENT>")
                nv = new_flat.get(k, "<ABSENT>")
                if ov != nv:
                    case_diffs.append((k, ov, nv))
            diff_details.append((str(rel), case_diffs))
            print(f"  DIFF: {rel}  ({len(case_diffs)} keys differ)")

    print(f"\n=== Summary (unlimited depth, volatile fields excluded) ===")
    print(f"  OK:      {ok}")
    print(f"  DIFF:    {diff}")
    print(f"  MISSING: {missing}")
    print(f"  TOTAL:   {ok + diff + missing}")

    # Show details for first 3 diffs
    if diff_details:
        print(f"\n=== Diff details (first 3 cases, first 10 keys each) ===")
        for case_rel, diffs in diff_details[:3]:
            print(f"\n--- {case_rel} ---")
            for k, ov, nv in diffs[:10]:
                print(f"  {k}:")
                print(f"    orig: {repr(ov)[:120]}")
                print(f"    new:  {repr(nv)[:120]}")
            if len(diffs) > 10:
                print(f"  ... and {len(diffs) - 10} more")


if __name__ == "__main__":
    main()
