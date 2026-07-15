"""Write app-identity code-section metapatches for the burgers archive.

Additive only: writes <case>/metapatches/<date>/q8020_patch_code_0.json
derived from each case's own assembled metadata.  Never edits or deletes an
existing file.  Scoped to the subdir passed as argv[1].  Dry-run by default;
pass --apply to actually write.

Usage: python -m tests.metapatches.apply_code_patch <burgers_dir> [--apply]
"""

import json
import sys
from pathlib import Path

from tests.metapatches.app_identity import cur_code, resolve

PATCH_DATE = "20260715"


def main() -> None:
    root = Path(sys.argv[1])
    dry = "--apply" not in sys.argv
    written = skipped = 0
    for md in sorted(root.rglob("q8020_metadata_*.json")):
        if "metapatches" in md.parts:
            continue
        try:
            meta = json.load(open(md))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        cur_entry, cur_algo = cur_code(meta)
        entry, algo = resolve(meta)
        patch: dict = {}
        if entry and cur_entry in (None, "-c", "bash"):
            patch["entry_point"] = entry
            patch["interpreter"] = "python"
        if algo and not cur_algo:
            patch["algorithm"] = algo
        if not patch:
            skipped += 1
            continue
        run_dir = md.parent / "metapatches" / PATCH_DATE
        out = run_dir / "q8020_patch_code_0.json"
        doc = {
            "_source": "review",
            "_patch_date": "2026-07-15",
            "_note": ("app identity: sweep wrapper recorded the bash -c "
                      "shell, not the solver; derived from this case's own "
                      "case section"),
            "patches": {"code": patch},
        }
        if not dry:
            run_dir.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2)
        written += 1
    mode = "DRY-RUN would write" if dry else "wrote"
    print(f"{mode} {written} patch files; skipped {skipped} "
          f"(already groomed / not derivable)")


if __name__ == "__main__":
    main()
