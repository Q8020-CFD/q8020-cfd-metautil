"""Shared app-identity derivation for metapatch grooming.

Pulls (entry_point, algorithm) from an already-assembled metadata dict the
same way the live ``sweep._resolve_app_identity`` does for new runs -- by
unwrapping the ``bash -c`` shell wrapper via the case section's ``_script``
and ``--method``.  Internal scratch helper, not shipped.
"""

import re
from pathlib import Path
from typing import Any

PY_RE = re.compile(r"(\S+\.py)\b")


def resolve(meta: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (entry_point, algorithm) derived from *meta*'s case section."""
    cases = meta.get("case", [])
    if isinstance(cases, dict):
        cases = [cases]
    script = method = None
    cmd_join = ""
    for c in cases:
        if not isinstance(c, dict):
            continue
        p = c.get("params") or {}
        script = script or p.get("_script")
        method = (
            method or c.get("method") or p.get("--method") or p.get("method")
        )
        cmd = c.get("command")
        if isinstance(cmd, list):
            cmd_join += " " + " ".join(str(x) for x in cmd)
    entry = None
    if isinstance(script, str):
        m = PY_RE.search(script)
        if m:
            entry = Path(m.group(1)).name
    if entry is None and cmd_join:
        m = PY_RE.search(cmd_join)
        if m:
            entry = Path(m.group(1)).name
    return entry, (method if isinstance(method, str) else None)


def cur_code(meta: dict[str, Any]) -> tuple[Any, Any]:
    """Return the current (entry_point, algorithm) in *meta*'s code section."""
    code = meta.get("code", [])
    if isinstance(code, dict):
        code = [code]
    if code and isinstance(code[0], dict):
        return code[0].get("entry_point"), code[0].get("algorithm")
    return None, None
