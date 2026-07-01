"""Tests for the dependent SLURM post-processing job (Option A).

Covers:
- ``_generate_postproc_sbatch`` renders the dependency line, is single-node,
  invokes the worker without ``srun``, and never emits NVMe/sbcast staging.
- Dependency-type validation.
- ``sweep_postproc.main()`` runs all group contexts before the final one and
  exits non-zero when a command fails.
"""

import json
import sys
from pathlib import Path

import pytest

from q8020_cfd_metautil.sweep import _generate_postproc_sbatch
import q8020_cfd_metautil.sweep_postproc as spp


# --------------------------------------------------------------------------
# _generate_postproc_sbatch
# --------------------------------------------------------------------------

def _base_params():
    return {
        "_slurm_project": "ard189",
        "_slurm_partition": "batch",
    }


def test_sbatch_has_afterok_dependency(tmp_path):
    script = _generate_postproc_sbatch(
        tmp_path, _base_params(), "12345", "afterok",
    )
    assert "#SBATCH --dependency=afterok:12345" in script


def test_sbatch_has_afterany_dependency(tmp_path):
    script = _generate_postproc_sbatch(
        tmp_path, _base_params(), "999", "afterany",
    )
    assert "#SBATCH --dependency=afterany:999" in script


def test_sbatch_is_single_node(tmp_path):
    script = _generate_postproc_sbatch(
        tmp_path, _base_params(), "1", "afterok",
    )
    assert "#SBATCH -N 1" in script


def test_sbatch_invokes_worker_without_srun(tmp_path):
    script = _generate_postproc_sbatch(
        tmp_path, _base_params(), "1", "afterok",
    )
    assert "python3 -m q8020_cfd_metautil.sweep_postproc" in script
    # The aggregation step runs on the batch node, not via srun fan-out.
    worker_line = next(
        ln for ln in script.splitlines()
        if "sweep_postproc" in ln and ln.strip().startswith("python3")
    )
    assert "srun" not in worker_line


def test_sbatch_omits_nvme_staging_without_conda(tmp_path):
    # Even if the compute job packed venvs, the postproc job must not stage
    # to NVMe -- it uses the shared-FS interpreter.
    params = _base_params()
    params["_slurm_pack_venvs"] = ["/some/.venv"]
    script = _generate_postproc_sbatch(tmp_path, params, "1", "afterok")
    assert "sbcast" not in script
    assert "/mnt/bb/" not in script


def test_sbatch_honors_postproc_walltime(tmp_path):
    params = _base_params()
    params["_slurm_postproc_walltime"] = "01:15:00"
    script = _generate_postproc_sbatch(tmp_path, params, "1", "afterok")
    assert "#SBATCH -t 01:15:00" in script


def test_sbatch_default_walltime(tmp_path):
    script = _generate_postproc_sbatch(
        tmp_path, _base_params(), "1", "afterok",
    )
    assert "#SBATCH -t 00:30:00" in script


def test_sbatch_rejects_bad_dependency_type(tmp_path):
    with pytest.raises(ValueError):
        _generate_postproc_sbatch(tmp_path, _base_params(), "1", "afterbogus")


# --------------------------------------------------------------------------
# sweep_postproc.main()
# --------------------------------------------------------------------------

def _write_group_json(run_dir, group_id, commands):
    path = run_dir / f"_group_postproc_{group_id}.json"
    path.write_text(json.dumps({
        "group_id": group_id,
        "params": {"_group_postproc": commands},
        "script_dir": str(run_dir),
    }))
    return path


def _write_final_json(run_dir, commands):
    path = run_dir / "_final_postproc.json"
    path.write_text(json.dumps({
        "global_params": {"_final_postproc": commands},
        "script_dir": str(run_dir),
    }))
    return path


def test_main_runs_groups_before_final(tmp_path, monkeypatch):
    _write_group_json(tmp_path, "beta", ["echo b"])
    _write_group_json(tmp_path, "alpha", ["echo a"])
    _write_final_json(tmp_path, ["echo final"])

    calls = []

    def fake_run_postproc(commands, json_path, script_dir, dry_run=False):
        calls.append((commands, Path(json_path).name))
        return [{"status": "success"} for _ in commands]

    monkeypatch.setattr(spp, "run_postproc", fake_run_postproc)
    monkeypatch.setattr(sys, "argv", ["prog", str(tmp_path)])

    with pytest.raises(SystemExit) as exc:
        spp.main()
    assert exc.value.code == 0

    names = [name for _, name in calls]
    # All group contexts run before the final one.
    assert names[-1] == "_final_postproc.json"
    assert set(names[:-1]) == {
        "_group_postproc_alpha.json",
        "_group_postproc_beta.json",
    }


def test_main_exits_nonzero_on_failure(tmp_path, monkeypatch):
    _write_group_json(tmp_path, "g1", ["false"])

    def fake_run_postproc(commands, json_path, script_dir, dry_run=False):
        return [{"status": "error", "returncode": 1}]

    monkeypatch.setattr(spp, "run_postproc", fake_run_postproc)
    monkeypatch.setattr(sys, "argv", ["prog", str(tmp_path)])

    with pytest.raises(SystemExit) as exc:
        spp.main()
    assert exc.value.code == 1


def test_main_handles_only_final(tmp_path, monkeypatch):
    _write_final_json(tmp_path, ["echo final"])
    seen = []

    def fake_run_postproc(commands, json_path, script_dir, dry_run=False):
        seen.append(Path(json_path).name)
        return [{"status": "success"}]

    monkeypatch.setattr(spp, "run_postproc", fake_run_postproc)
    monkeypatch.setattr(sys, "argv", ["prog", str(tmp_path)])

    with pytest.raises(SystemExit) as exc:
        spp.main()
    assert exc.value.code == 0
    assert seen == ["_final_postproc.json"]


def test_main_missing_run_dir_exits_2(tmp_path, monkeypatch):
    missing = tmp_path / "nope"
    monkeypatch.setattr(sys, "argv", ["prog", str(missing)])
    with pytest.raises(SystemExit) as exc:
        spp.main()
    assert exc.value.code == 2
