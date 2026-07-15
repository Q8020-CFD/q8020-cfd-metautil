"""Report how many burgers cases need code-section grooming.

Read-only.  Usage: python -m tests.metapatches.survey <burgers_dir>
"""

import json
import sys
from pathlib import Path

from tests.metapatches.app_identity import cur_code, resolve


def main() -> None:
    root = Path(sys.argv[1])
    n = have_entry = have_algo = bad_code = 0
    algos: dict[str, int] = {}
    entries: dict[str, int] = {}
    for md in root.rglob("q8020_metadata_*.json"):
        if "metapatches" in md.parts:
            continue
        try:
            meta = json.load(open(md))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        n += 1
        cur_entry, cur_algo = cur_code(meta)
        if cur_entry in (None, "-c") or cur_algo is None:
            bad_code += 1
        entry, algo = resolve(meta)
        if entry:
            have_entry += 1
            entries[entry] = entries.get(entry, 0) + 1
        if algo:
            have_algo += 1
            algos[algo] = algos.get(algo, 0) + 1

    print(f"metadata files: {n}")
    print(f"code section needs grooming (entry None/-c OR algo missing): "
          f"{bad_code}")
    print(f"can derive entry_point: {have_entry}   "
          f"can derive algorithm: {have_algo}")
    print("derived entry_points:", entries)
    print("derived algorithms:", algos)


if __name__ == "__main__":
    main()
