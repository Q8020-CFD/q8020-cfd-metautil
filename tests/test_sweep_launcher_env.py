"""Integration tests for the synthesized srun launcher, the per-group
_script override (merge fix), and _env_exports propagation.

Driven through ``run_sweep(dry_run=True)``: even in dry-run the sweeper
writes per-case ``pipeline_args.json`` (solver cmd) and, for Slurm mode,
the generated sbatch — enough to assert on without submitting anything.
"""

import json
import sys
from pathlib import Path

from q8020_cfd_metautil.sweep import run_sweep


def _write_toml(tmp_path: Path, body: str) -> Path:
    toml = tmp_path / "sweep.toml"
    toml.write_text(body, encoding="utf-8")
    return toml


def _pipeline_cmds(results: dict) -> dict[str, list[str]]:
    """Map case_id -> cmd list from each case's pipeline_args.json."""
    cmds = {}
    for case in results["cases"].values():
        args_file = Path(case["output_dir"]) / "pipeline_args.json"
        with open(args_file, encoding="utf-8") as f:
            pargs = json.load(f)
        cmds[case["case_id"]] = pargs["cmd"]
    return cmds


def _slurm_global(tmp_path: Path, extra: str = "") -> str:
    return f"""
[global]
_output_dir = "{tmp_path}/out"
_run_mode = "parallel"
_slurm = true
_slurm_project = "test"
_slurm_tasks_per_case = 16
_slurm_tasks_per_node = 8
_slurm_gpus_per_task = 1
_script = "python solver.py"
{extra}

[g1]
qubits = 32
"""


# --------------------------------------------------------------------------
# Launcher synthesis
# --------------------------------------------------------------------------

def test_launcher_synthesized_for_multirank_slurm(tmp_path):
    toml = _write_toml(tmp_path, _slurm_global(tmp_path))
    results = run_sweep(str(toml), None, dry_run=True)
    (cmd,) = _pipeline_cmds(results).values()
    assert cmd[0] == "srun"
    joined = " ".join(cmd)
    assert "-N 2" in joined
    assert "-n 16" in joined
    assert "--ntasks-per-node=8" in joined
    assert "--gpus-per-task=1" in joined
    assert "--exclusive" in joined
    assert "python" in cmd
    assert "solver.py" in cmd


def test_launcher_auto_backs_off_when_script_has_srun(tmp_path):
    toml = _write_toml(tmp_path, f"""
[global]
_output_dir = "{tmp_path}/out"
_run_mode = "parallel"
_slurm = true
_slurm_tasks_per_case = 16
_script = "srun -n 16 python solver.py"

[g1]
qubits = 32
""")
    results = run_sweep(str(toml), None, dry_run=True)
    (cmd,) = _pipeline_cmds(results).values()
    # Exactly the user's srun, not a doubled one
    assert cmd[0] == "srun"
    assert " ".join(cmd).count("srun") == 1


def test_launcher_auto_ignores_srun_substring_in_paths(tmp_path):
    # A path merely containing "srun" must not suppress synthesis.
    toml = _write_toml(tmp_path, f"""
[global]
_output_dir = "{tmp_path}/out"
_run_mode = "parallel"
_slurm = true
_slurm_tasks_per_case = 16
_slurm_tasks_per_node = 8
_script = "/opt/mysrun-env/bin/python solver.py"

[g1]
qubits = 32
""")
    results = run_sweep(str(toml), None, dry_run=True)
    (cmd,) = _pipeline_cmds(results).values()
    assert cmd[0] == "srun"


def test_launcher_none_suppresses_synthesis(tmp_path):
    toml = _write_toml(
        tmp_path, _slurm_global(tmp_path, '_slurm_launcher = "none"'),
    )
    results = run_sweep(str(toml), None, dry_run=True)
    (cmd,) = _pipeline_cmds(results).values()
    assert "srun" not in cmd


def test_no_launcher_without_slurm(tmp_path):
    toml = _write_toml(tmp_path, f"""
[global]
_output_dir = "{tmp_path}/out"
_run_mode = "parallel"
_slurm = false
_slurm_tasks_per_case = 16
_script = "python solver.py"

[g1]
qubits = 32
""")
    results = run_sweep(str(toml), None, dry_run=True)
    (cmd,) = _pipeline_cmds(results).values()
    assert "srun" not in cmd


def test_no_launcher_for_single_rank(tmp_path):
    toml = _write_toml(tmp_path, f"""
[global]
_output_dir = "{tmp_path}/out"
_run_mode = "parallel"
_slurm = true
_script = "python solver.py"

[g1]
qubits = 32
""")
    results = run_sweep(str(toml), None, dry_run=True)
    (cmd,) = _pipeline_cmds(results).values()
    assert "srun" not in cmd


def test_launcher_composes_with_env_activation(tmp_path):
    # With _env, the composed command is a bash -c one-liner:
    # activation && srun ... python solver.py
    venv = tmp_path / "venv"
    toml = _write_toml(
        tmp_path, _slurm_global(tmp_path, f'_env = "{venv}"'),
    )
    results = run_sweep(str(toml), None, dry_run=True)
    (cmd,) = _pipeline_cmds(results).values()
    assert cmd[:2] == ["bash", "-c"]
    assert "activate && srun -N 2 -n 16" in cmd[2]


# --------------------------------------------------------------------------
# Per-group _script override (merge fix) and --set precedence
# --------------------------------------------------------------------------

def test_group_script_survives_global_script(tmp_path):
    toml = _write_toml(tmp_path, f"""
[global]
_output_dir = "{tmp_path}/out"
_run_mode = "parallel"
_script = "python global_solver.py"

[g1]
qubits = 32

[g2]
_script = "python group_solver.py"
qubits = 32
""")
    results = run_sweep(str(toml), None, dry_run=True)
    cmds = _pipeline_cmds(results)
    assert "global_solver.py" in cmds["g1"]
    assert "group_solver.py" in cmds["g2"]


def test_set_script_overrides_group_and_global(tmp_path):
    toml = _write_toml(tmp_path, f"""
[global]
_output_dir = "{tmp_path}/out"
_run_mode = "parallel"
_script = "python global_solver.py"

[g1]
_script = "python group_solver.py"
qubits = 32
""")
    results = run_sweep(
        str(toml), None, dry_run=True,
        overrides={"_script": "python override_solver.py"},
    )
    (cmd,) = _pipeline_cmds(results).values()
    assert "override_solver.py" in cmd


# --------------------------------------------------------------------------
# Template indirection through _script (fixpoint expansion end-to-end)
# --------------------------------------------------------------------------

def test_nested_template_in_script_resolves(tmp_path):
    toml = _write_toml(tmp_path, f"""
[global]
_output_dir = "{tmp_path}/out"
_run_mode = "parallel"
_launch = "custom_tool -n ${{_ranks}}"
_ranks = 4
_script = "${{_launch}} python solver.py"

[g1]
qubits = 32
""")
    results = run_sweep(str(toml), None, dry_run=True)
    (cmd,) = _pipeline_cmds(results).values()
    assert cmd[:3] == ["custom_tool", "-n", "4"]


# --------------------------------------------------------------------------
# _env_exports reaches a locally-run solver (non-dry, local parallel)
# --------------------------------------------------------------------------

def test_env_exports_reach_local_solver(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os\n"
        'print(os.environ.get("Q8020_TEST_VAR", "UNSET"))\n',
        encoding="utf-8",
    )
    toml = _write_toml(tmp_path, f"""
[global]
_output_dir = "{tmp_path}/out"
_run_mode = "parallel"
_script = "{sys.executable} {probe}"

[global._env_exports]
Q8020_TEST_VAR = "hello-env"

[g1]
qubits = 32
""")
    results = run_sweep(str(toml), None, dry_run=False)
    (case,) = results["cases"].values()
    assert case["status"] == "success"
    (stdout_file,) = Path(case["output_dir"]).glob("q8020_stdout_*.txt")
    assert "hello-env" in stdout_file.read_text(encoding="utf-8")
