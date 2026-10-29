"""Tests for code_provenance: git, dirty git, non-git, and venv inference."""
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from q8020_cfd_metautil import code_provenance as cp


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True,
        capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "solver"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    _git(r, "remote", "add", "origin",
         "https://someone:ghp_SECRET123@github.com/org/solver.git")
    (r / "solver.py").write_text("print('v1')\n")
    (r / "uv.lock").write_text("lock\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    return r


def test_clean_git_records_commit_and_strips_token(repo, tmp_path):
    run_dir = tmp_path / "run"
    rec = cp.capture_code_provenance(repo, run_dir)
    assert rec["kind"] == "git"
    assert rec["git"]["commit"] == _git(repo, "rev-parse", "HEAD")
    assert rec["git"]["dirty"] is False
    assert rec["git"]["remote_url"] == "https://github.com/org/solver.git"
    assert "uv.lock" in rec["dependency_files"]
    assert not (run_dir / cp.ARCHIVE_DIR).exists()  # commit alone suffices
    text = (run_dir / cp.PROVENANCE_FILE).read_text()
    assert "SECRET" not in text


def test_dirty_git_saves_patch_and_untracked(repo, tmp_path):
    (repo / "solver.py").write_text("print('v2')\n")
    (repo / "new_module.py").write_text("x = 1\n")
    run_dir = tmp_path / "run"
    rec = cp.capture_code_provenance(repo, run_dir)
    git = rec["git"]
    assert git["dirty"] is True
    patch = (run_dir / git["diff_patch"]).read_text()
    assert "+print('v2')" in patch
    with tarfile.open(run_dir / git["untracked_archive"]) as tar:
        assert tar.getnames() == ["new_module.py"]


def test_non_git_dir_hashes_and_archives_once(tmp_path):
    code = tmp_path / "code"
    (code / "pkg").mkdir(parents=True)
    (code / "pkg" / "a.py").write_text("a = 1\n")
    (code / ".venv" / "lib").mkdir(parents=True)
    (code / ".venv" / "lib" / "big.py").write_text("ignored\n")
    (code / "__pycache__").mkdir()
    (code / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\0")
    run_dir = tmp_path / "run"
    rec = cp.capture_code_provenance(code, run_dir)
    assert rec["kind"] == "directory"
    assert rec["file_count"] == 1
    with tarfile.open(run_dir / rec["archive"]) as tar:
        assert tar.getnames() == ["pkg/a.py"]
    # Same code -> same hash; a change -> a different hash
    again = cp.capture_code_provenance(code, tmp_path / "run2")
    assert again["content_sha256"] == rec["content_sha256"]
    (code / "pkg" / "a.py").write_text("a = 2\n")
    changed = cp.capture_code_provenance(code, tmp_path / "run3")
    assert changed["content_sha256"] != rec["content_sha256"]


def test_output_dir_containing_code_does_not_hide_code(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    (code / "s.py").write_text("s = 1\n")
    rec = cp.capture_code_provenance(code, tmp_path / "run", exclude=[tmp_path])
    assert rec["file_count"] == 1


def test_run_dir_inside_code_dir_is_excluded(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    (code / "s.py").write_text("s = 1\n")
    run_dir = code / "results" / "_wf"
    run_dir.mkdir(parents=True)
    (run_dir / "old_output.csv").write_text("1,2\n")
    rec = cp.capture_code_provenance(code, run_dir)
    assert rec["file_count"] == 1


def test_load_for_case_finds_record_from_trial_dir(repo, tmp_path):
    run_dir = tmp_path / "run"
    cp.capture_code_provenance(repo, run_dir)
    trial = run_dir / "group_0" / "trial_0_abcd1234"
    trial.mkdir(parents=True)
    summary = cp.load_for_case(trial)
    assert summary["kind"] == "git"
    assert summary["commit"] == _git(repo, "rev-parse", "HEAD")
    assert summary["record"].endswith(cp.PROVENANCE_FILE)


def test_missing_code_dir_reports_error(tmp_path):
    rec = cp.capture_code_provenance(tmp_path / "nope", tmp_path / "run")
    assert rec["kind"] == "missing"
    stored = json.loads((tmp_path / "run" / cp.PROVENANCE_FILE).read_text())
    assert "error" in stored


def test_infer_env_from_command(tmp_path):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "activate").write_text("")
    cmd = ["bash", "-c", f"source {venv}/bin/activate && python3 solver.py -n 5"]
    assert cp.infer_env_from_command(cmd) == str(venv)
    assert cp.infer_env_from_command(["python", "solver.py"]) is None
    missing = ["bash", "-c", "source /no/such/venv/bin/activate && python3 s.py"]
    assert cp.infer_env_from_command(missing) is None


def test_huge_non_git_dir_is_not_hashed(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "MAX_FILES", 3)
    code = tmp_path / "data"
    code.mkdir()
    for i in range(5):
        (code / f"f{i}.csv").write_text("1\n")
    rec = cp.capture_code_provenance(code, tmp_path / "run")
    assert "content_sha256" not in rec
    assert "more than 3 files" in rec["error"]
    assert not (tmp_path / "run" / cp.ARCHIVE_DIR).exists()


def test_dirty_git_caps_keep_commit(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "MAX_FILES", 3)
    monkeypatch.setattr(cp, "MAX_PATCH_BYTES", 10)
    monkeypatch.setattr(cp, "UNTRACKED_SAMPLE", 2)
    (repo / "solver.py").write_text("print('a much longer v2 line')\n")
    for i in range(5):
        (repo / f"stray_{i}.csv").write_text("1\n")
    run_dir = tmp_path / "run"
    rec = cp.capture_code_provenance(repo, run_dir)
    git = rec["git"]
    assert git["commit"] == _git(repo, "rev-parse", "HEAD")
    assert git["dirty"] is True
    assert git["diff_sha256"] and git["diff_patch"] is None
    assert "exceeds 10" in git["diff_patch_skipped"]
    assert git["untracked_count"] == 5 and len(git["untracked_sample"]) == 2
    assert "untracked_sha256" not in git
    assert "exceeds 3" in git["untracked_skipped"]
    assert not (run_dir / cp.ARCHIVE_DIR).exists()
