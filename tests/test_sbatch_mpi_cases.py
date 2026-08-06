"""Tests for multi-task (MPI) case support in the compute sbatch.

``_slurm_tasks_per_case > 1`` marks each case as a distributed MPI run:
the sbatch must size ``-N`` for tasks_per_case / tasks_per_node nodes per
case, and must launch the worker WITHOUT an ``srun`` wrapper — the solver
command itself (from ``_script``) contains the ``srun -n N`` that creates
the multi-task job step. Wrapping the worker in ``srun -n 1`` would pin it
inside a 1-task step and the nested solver srun would deadlock.
"""

from pathlib import Path

from q8020_cfd_metautil.sweep import _generate_sbatch_script


def _base_params():
    return {
        "_slurm_project": "ard189",
        "_slurm_partition": "batch",
    }


def _cases_info(tmp_path, n=1):
    return [
        {"args_file": tmp_path / f"case{i}" / "pipeline_args.json"}
        for i in range(n)
    ]


def _worker_lines(script):
    return [
        ln for ln in script.splitlines()
        if "q8020_cfd_metautil.sweep_worker" in ln
    ]


# --------------------------------------------------------------------------
# Default behavior unchanged (regression)
# --------------------------------------------------------------------------

def test_default_worker_wrapped_in_single_task_srun(tmp_path):
    script = _generate_sbatch_script(
        _cases_info(tmp_path), _base_params(), tmp_path,
    )
    (worker,) = _worker_lines(script)
    assert worker.strip().startswith("srun ")
    assert "-n 1" in worker


def test_default_single_case_is_single_node(tmp_path):
    script = _generate_sbatch_script(
        _cases_info(tmp_path), _base_params(), tmp_path,
    )
    assert "#SBATCH -N 1" in script


# --------------------------------------------------------------------------
# _slurm_tasks_per_case > 1 (MPI cases)
# --------------------------------------------------------------------------

def test_mpi_case_scales_nodes_by_tasks_per_node(tmp_path):
    params = _base_params()
    params["_slurm_tasks_per_case"] = 16
    params["_slurm_tasks_per_node"] = 8
    script = _generate_sbatch_script(
        _cases_info(tmp_path, 1), params, tmp_path,
    )
    assert "#SBATCH -N 2" in script


def test_mpi_cases_run_sequentially_on_same_nodes(tmp_path):
    # MPI cases execute one at a time (concurrent multi-rank steps race
    # in PMI/shm init), so the job only needs nodes for ONE case.
    params = _base_params()
    params["_slurm_tasks_per_case"] = 16
    params["_slurm_tasks_per_node"] = 8
    script = _generate_sbatch_script(
        _cases_info(tmp_path, 3), params, tmp_path,
    )
    assert "#SBATCH -N 2" in script


def test_mpi_node_count_rounds_up(tmp_path):
    params = _base_params()
    params["_slurm_tasks_per_case"] = 9
    params["_slurm_tasks_per_node"] = 8
    script = _generate_sbatch_script(
        _cases_info(tmp_path, 1), params, tmp_path,
    )
    assert "#SBATCH -N 2" in script


def test_mpi_worker_not_wrapped_in_srun(tmp_path):
    params = _base_params()
    params["_slurm_tasks_per_case"] = 16
    script = _generate_sbatch_script(
        _cases_info(tmp_path, 1), params, tmp_path,
    )
    (worker,) = _worker_lines(script)
    assert "srun" not in worker
    assert worker.strip().startswith("python3 -m")


def test_mpi_workers_run_sequentially_not_backgrounded(tmp_path):
    # No trailing `&`: each case's multi-rank step must fully finish
    # before the next starts, so steps never contend during MPI init.
    params = _base_params()
    params["_slurm_tasks_per_case"] = 16
    script = _generate_sbatch_script(
        _cases_info(tmp_path, 2), params, tmp_path,
    )
    workers = _worker_lines(script)
    assert len(workers) == 2
    assert all(not w.rstrip().endswith("&") for w in workers)


# --------------------------------------------------------------------------
# _env_exports emission
# --------------------------------------------------------------------------

def test_env_exports_absent_by_default(tmp_path):
    script = _generate_sbatch_script(
        _cases_info(tmp_path), _base_params(), tmp_path,
    )
    assert "# --- User environment (_env_exports) ---" not in script


def test_env_exports_emitted_as_export_lines(tmp_path):
    params = _base_params()
    params["_env_exports"] = {
        "MPICH_GPU_SUPPORT_ENABLED": "1",
        "LD_LIBRARY_PATH": "/opt/rocm/lib:$LD_LIBRARY_PATH",
    }
    script = _generate_sbatch_script(
        _cases_info(tmp_path), params, tmp_path,
    )
    assert 'export MPICH_GPU_SUPPORT_ENABLED="1"' in script
    # $VARS left for bash to expand at runtime on the compute node
    assert 'export LD_LIBRARY_PATH="/opt/rocm/lib:$LD_LIBRARY_PATH"' in script


def test_env_exports_after_default_thread_exports(tmp_path):
    # User exports must come after the hardcoded OMP/MKL exports so a
    # user-specified OMP_NUM_THREADS wins.
    params = _base_params()
    params["_env_exports"] = {"OMP_NUM_THREADS": "$SLURM_CPUS_PER_TASK"}
    script = _generate_sbatch_script(
        _cases_info(tmp_path), params, tmp_path,
    )
    default_pos = script.index("export OMP_NUM_THREADS=1")
    user_pos = script.index(
        'export OMP_NUM_THREADS="$SLURM_CPUS_PER_TASK"'
    )
    assert user_pos > default_pos


def test_env_exports_values_coerced_to_str(tmp_path):
    params = _base_params()
    params["_env_exports"] = {"SHOTS": 1000}
    script = _generate_sbatch_script(
        _cases_info(tmp_path), params, tmp_path,
    )
    assert 'export SHOTS="1000"' in script


def test_env_exports_rejects_non_table(tmp_path):
    import pytest

    params = _base_params()
    params["_env_exports"] = "FOO=1"
    with pytest.raises(ValueError, match="_env_exports"):
        _generate_sbatch_script(
            _cases_info(tmp_path), params, tmp_path,
        )
