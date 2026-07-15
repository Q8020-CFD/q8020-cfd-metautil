"""JIT composer: base rollup (+) active metapatches, deep-merge, later-wins.

Read-only reference implementation of the composition contract in
docs/metapatches.md.  Touches nothing on disk.  Internal, not shipped.

Usage: python -m tests.metapatches.compose <path/to/q8020_metadata_*.json>
"""

import json
import sys
from pathlib import Path
from typing import Any


def deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge *patch* into *base* (patch wins per key)."""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def active_runs(case_dir: Path) -> list[Path]:
    """Return patch-run subdirs still matching the active glob, date order."""
    mp = case_dir / "metapatches"
    if not mp.is_dir():
        return []
    runs = [d for d in mp.iterdir()
            if d.is_dir() and not d.name.endswith(".off")]
    return sorted(runs, key=lambda d: d.name)


def compose(md: Path) -> dict[str, Any]:
    """Compose *md* with its active metapatches; return the merged dict."""
    meta = json.load(open(md))
    case_dir = md.parent
    for run in active_runs(case_dir):
        for pf in sorted(run.glob("q8020_patch_*.json")):
            if pf.name.endswith(".off"):
                continue
            doc = json.load(open(pf))
            for section, patch in doc.get("patches", {}).items():
                sec = meta.get(section)
                if isinstance(sec, list) and sec and isinstance(sec[0], dict):
                    deep_merge(sec[0], patch)
                elif isinstance(sec, dict):
                    deep_merge(sec, patch)
                else:
                    meta[section] = [patch]
    return meta


def _code0(m: dict[str, Any]) -> dict[str, Any]:
    c = m.get("code", [])
    if isinstance(c, dict):
        c = [c]
    keys = ("interpreter", "entry_point", "algorithm")
    return {k: c[0].get(k) for k in keys} if c else {}


def main() -> None:
    md = Path(sys.argv[1])
    base = json.load(open(md))
    print("BASE     code:", _code0(base))
    print("COMPOSED code:", _code0(compose(md)))


if __name__ == "__main__":
    main()
