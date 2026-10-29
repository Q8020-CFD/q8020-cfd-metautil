"""Record exactly which solver code a sweep ran.

``capture_code_provenance(code_dir, run_dir)`` is called once per sweep and
writes ``<run_dir>/q8020_code_provenance.json``.  For a git checkout it
records commit, branch, remote (credentials stripped) and dirty state; a
dirty tree also gets its tracked diff saved as a patch and its untracked
files archived.  Code that is not a git checkout is identified by a content
hash over its source files and archived once per sweep as
``<run_dir>/code/<hash>.tar.gz``.  Either way, the hashes of dependency
files (uv.lock, requirements*.txt, pyproject.toml, ...) are recorded.

Each case's sweep code fragment carries a compact reference to that record
(``case_code_provenance``), so per-case metadata stays small.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROVENANCE_FILE = "q8020_code_provenance.json"
ARCHIVE_DIR = "code"

# Never hashed or archived: environments, caches, VCS internals, outputs.
EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints", "node_modules",
    ".tox", "build", "dist", "output", "outputs",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".so", ".o"}
# Files above this size are listed (name, size) but not hashed or archived.
MAX_FILE_BYTES = 5 * 1024 * 1024
# Above this total, no archive is written (the content hash still is).
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
# Above this many files the dir is not treated as source: nothing is hashed.
# Also caps the untracked files of a dirty git checkout.
MAX_FILES = 5000
# A dirty checkout's diff above this is hashed but not saved.
MAX_PATCH_BYTES = 20 * 1024 * 1024
# Untracked file names kept in the record (the count is always kept).
UNTRACKED_SAMPLE = 50
DEPENDENCY_FILES = (
    "uv.lock", "pyproject.toml", "setup.py", "setup.cfg", "requirements*.txt",
    "environment.yml", "environment.yaml", "poetry.lock", "Pipfile.lock",
)
_GIT_TIMEOUT = 30


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(code_dir: Path, *args: str, text: bool = True) -> Any:
    """Run git in *code_dir*; return stdout, or None if git fails."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(code_dir), *args],
            capture_output=True, text=text, timeout=_GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def strip_url_credentials(url: str | None) -> str | None:
    """Drop ``user:token@`` from a remote URL so tokens never reach metadata."""
    if not url:
        return url
    return re.sub(r"^(\w[\w+.-]*://)[^/@]*@", r"\1", url.strip())


class _TooLarge(Exception):
    """The code dir is not a plausible source tree (too many files/bytes)."""


def _source_files(code_dir: Path, exclude: list[Path]) -> list[Path]:
    """All files under *code_dir* except excluded dirs/suffixes/paths, sorted.
    Excluded dirs are pruned, not walked.  Raises _TooLarge past MAX_FILES
    (e.g. a sweep whose code dir fell back to a data-laden working dir)."""
    excluded = [Path(p).resolve() for p in exclude]
    files: list[Path] = []
    for root, dirs, names in os.walk(code_dir):
        root_path = Path(root)
        dirs[:] = sorted(
            d for d in dirs
            if d not in EXCLUDED_DIRS
            and not any((root_path / d).resolve() == e for e in excluded)
        )
        for name in names:
            path = root_path / name
            if path.suffix in EXCLUDED_SUFFIXES or path.is_symlink():
                continue
            if not path.is_file():
                continue
            files.append(path)
            if len(files) > MAX_FILES:
                raise _TooLarge(f"more than {MAX_FILES} files under {code_dir}")
    return sorted(files)


def _content_hash(code_dir: Path, files: list[Path]) -> tuple[str, list[dict], int]:
    """Hash (relpath, file sha256) pairs; return hash, oversized files, bytes."""
    digest = hashlib.sha256()
    oversized: list[dict] = []
    total = 0
    for path in files:
        rel = path.relative_to(code_dir).as_posix()
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            oversized.append({"path": rel, "size_bytes": size})
            digest.update(f"{rel}\0<oversized:{size}>\n".encode())
            continue
        total += size
        digest.update(f"{rel}\0{_sha256_file(path)}\n".encode())
    return digest.hexdigest(), oversized, total


def _write_archive(code_dir: Path, files: list[Path], dest: Path) -> None:
    """Write a reproducible-order tar.gz of *files* (relative to *code_dir*)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        for path in files:
            if path.stat().st_size <= MAX_FILE_BYTES:
                tar.add(str(path), arcname=path.relative_to(code_dir).as_posix())
    tmp.replace(dest)


def _dependency_hashes(code_dir: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for pattern in DEPENDENCY_FILES:
        for path in sorted(code_dir.glob(pattern)):
            if path.is_file():
                found[path.name] = _sha256_file(path)
    return found


def _git_info(code_dir: Path) -> dict[str, Any] | None:
    """Commit/branch/remote/dirty for *code_dir*, or None if not a checkout."""
    toplevel = _git(code_dir, "rev-parse", "--show-toplevel")
    commit = _git(code_dir, "rev-parse", "HEAD")
    if not toplevel or not commit:
        return None
    branch = (_git(code_dir, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    remote = _git(code_dir, "config", "--get", "remote.origin.url")
    describe = _git(code_dir, "describe", "--tags", "--always", "--dirty")
    status = _git(code_dir, "status", "--porcelain") or ""
    tracked_changes = [l for l in status.splitlines() if not l.startswith("??")]
    untracked = (_git(code_dir, "ls-files", "--others", "--exclude-standard",
                      "-z") or "").split("\0")
    return {
        "toplevel": toplevel.strip(),
        "subdir": Path(code_dir).resolve().relative_to(
            Path(toplevel.strip()).resolve()).as_posix(),
        "commit": commit.strip(),
        "branch": None if branch == "HEAD" else branch,
        "describe": (describe or "").strip() or None,
        "remote_url": strip_url_credentials(remote),
        "dirty": bool(tracked_changes),
        "tracked_changes": len(tracked_changes),
        "untracked_files": [u for u in untracked if u],
    }


def capture_code_provenance(
    code_dir: str | Path,
    run_dir: str | Path,
    exclude: list[str | Path] | None = None,
) -> dict[str, Any]:
    """Record *code_dir*'s identity in ``<run_dir>/q8020_code_provenance.json``.

    *exclude* lists paths never hashed or archived (e.g. the sweep output
    dir when it sits inside the code dir).  Returns the record written.
    Never raises on a provenance problem: failures land in ``error``.
    """
    code_dir = Path(code_dir).expanduser().resolve()
    run_dir = Path(run_dir)
    record: dict[str, Any] = {
        "code_dir": str(code_dir),
        "captured_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"),
        "kind": None,
    }
    try:
        if not code_dir.is_dir():
            record["kind"] = "missing"
            record["error"] = f"code dir not found: {code_dir}"
            return _write_record(run_dir, record)
        record["dependency_files"] = _dependency_hashes(code_dir)
        git = _git_info(code_dir)
        # Only paths strictly inside the code dir can be excluded; an output
        # dir that contains the code (e.g. ".") must not hide the code.
        excludes = [
            p for p in (Path(e).expanduser().resolve()
                        for e in [*(exclude or []), run_dir])
            if p != code_dir and p.is_relative_to(code_dir)
        ]
        if git is not None:
            record["kind"] = "git"
            record["git"] = git
            if git["dirty"]:
                patch = _git(code_dir, "diff", "HEAD", "--binary", text=False)
                if patch:
                    patch_hash = hashlib.sha256(patch).hexdigest()
                    record["git"]["diff_sha256"] = patch_hash
                    record["git"]["diff_bytes"] = len(patch)
                    if len(patch) <= MAX_PATCH_BYTES:
                        rel = (f"{ARCHIVE_DIR}/{git['commit'][:12]}-"
                               f"{patch_hash[:16]}.patch")
                        (run_dir / ARCHIVE_DIR).mkdir(parents=True,
                                                      exist_ok=True)
                        (run_dir / rel).write_bytes(patch)
                        record["git"]["diff_patch"] = rel
                    else:
                        record["git"]["diff_patch"] = None
                        record["git"]["diff_patch_skipped"] = (
                            f"{len(patch)} patch bytes exceeds "
                            f"{MAX_PATCH_BYTES}")
            # The full untracked list can be huge; keep a count and a sample.
            all_untracked = git.pop("untracked_files")
            record["git"]["untracked_count"] = len(all_untracked)
            record["git"]["untracked_sample"] = all_untracked[:UNTRACKED_SAMPLE]
            untracked_paths = [
                p for p in ((Path(git["toplevel"]) / u).resolve()
                            for u in all_untracked)
                if p.is_file() and p.is_relative_to(code_dir)
                and not any(part in EXCLUDED_DIRS
                            for part in p.relative_to(code_dir).parts[:-1])
                and not any(p.is_relative_to(e) for e in excludes)
            ]
            if len(untracked_paths) > MAX_FILES:
                record["git"]["untracked_skipped"] = (
                    f"{len(untracked_paths)} untracked files exceeds "
                    f"{MAX_FILES}; not hashed or archived")
            elif untracked_paths:
                u_hash, _, u_bytes = _content_hash(code_dir, untracked_paths)
                record["git"]["untracked_sha256"] = u_hash
                if u_bytes > MAX_ARCHIVE_BYTES:
                    record["git"]["untracked_skipped"] = (
                        f"{u_bytes} untracked bytes exceeds "
                        f"{MAX_ARCHIVE_BYTES}; hashed, not archived")
                else:
                    rel = f"{ARCHIVE_DIR}/untracked-{u_hash[:16]}.tar.gz"
                    if not (run_dir / rel).exists():
                        _write_archive(code_dir, untracked_paths, run_dir / rel)
                    record["git"]["untracked_archive"] = rel
        else:
            record["kind"] = "directory"
            try:
                files = _source_files(code_dir, excludes)
            except _TooLarge as exc:
                record["error"] = (
                    f"{exc}; not hashed or archived (is _script's solver "
                    f"path resolvable, or is this a data directory?)")
                return _write_record(run_dir, record)
            content_hash, oversized, total = _content_hash(code_dir, files)
            record["content_sha256"] = content_hash
            record["file_count"] = len(files)
            record["source_bytes"] = total
            if oversized:
                record["oversized_files"] = oversized
            if total <= MAX_ARCHIVE_BYTES:
                rel = f"{ARCHIVE_DIR}/{content_hash[:16]}.tar.gz"
                if not (run_dir / rel).exists():
                    _write_archive(code_dir, files, run_dir / rel)
                record["archive"] = rel
            else:
                record["archive"] = None
                record["archive_skipped"] = (
                    f"{total} source bytes exceeds {MAX_ARCHIVE_BYTES}")
    except Exception as exc:  # provenance must never break a sweep
        record["error"] = f"{type(exc).__name__}: {exc}"
    return _write_record(run_dir, record)


def _write_record(run_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / PROVENANCE_FILE, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return record


def summarize(record: dict[str, Any]) -> dict[str, Any]:
    """The compact per-case reference to a sweep's provenance record."""
    keys = ("kind", "code_dir", "content_sha256", "archive", "error")
    summary = {k: record[k] for k in keys if record.get(k) is not None}
    git = record.get("git")
    if git:
        for k in ("commit", "branch", "describe", "remote_url", "dirty",
                  "diff_sha256", "diff_patch", "untracked_sha256",
                  "untracked_archive"):
            if git.get(k) is not None:
                summary[k] = git[k]
    if record.get("dependency_files"):
        summary["dependency_files"] = record["dependency_files"]
    return summary


def load_for_case(case_dir: str | Path, max_levels: int = 4) -> dict | None:
    """Find the sweep's provenance record from a case dir (it sits in the
    run dir, one or two levels up) and return its compact summary."""
    current = Path(case_dir).resolve()
    for _ in range(max_levels):
        candidate = current / PROVENANCE_FILE
        if candidate.is_file():
            try:
                with open(candidate, encoding="utf-8") as f:
                    record = json.load(f)
            except (OSError, ValueError):
                return None
            summary = summarize(record)
            summary["record"] = str(candidate)
            return summary
        if current.parent == current:
            break
        current = current.parent
    return None


_ACTIVATE_PATTERN = re.compile(r"source\s+(\S+?)/bin/activate")


def infer_env_from_command(command: list[str] | str | None) -> str | None:
    """Venv dir a solver command activates (``source X/bin/activate``), so
    the solver's own packages are snapshotted rather than the sweeper's."""
    if not command:
        return None
    text = command if isinstance(command, str) else " ".join(command)
    matches = _ACTIVATE_PATTERN.findall(text)
    if not matches:
        return None
    candidate = Path(matches[-1].strip("'\"")).expanduser()
    return str(candidate) if (candidate / "bin" / "activate").exists() else None
