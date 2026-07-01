"""Aggregation worker for the dependent SLURM post-processing job.

Invoked on a compute node (via ``q8020_postproc.sbatch``) as:
    python -m q8020_cfd_metautil.sweep_postproc <run_dir>

When a sweep runs with ``_slurm = true`` the solver cases are dispatched as a
single parallel SLURM job and group/final post-processing cannot run inline
(the case outputs do not exist until that job finishes).  With
``_slurm_postproc_job = true`` the sweeper submits *this* worker as a second,
dependent job (``--dependency=afterok:<compute_job_id>``) so aggregation runs
automatically once the compute job completes.

This worker re-reads the context JSONs the sweeper already wrote into
``run_dir`` and replays them through :func:`run_postproc`, preserving the
sequential contract: every ``_group_postproc_*.json`` runs first (order among
groups is irrelevant), then ``_final_postproc.json`` runs last so a final
step may depend on group outputs.
"""

import json
import sys
from pathlib import Path

from q8020_cfd_metautil.sweep import run_postproc


def _as_list(value) -> list:
    """Normalize a postproc directive (str or list) to a list of commands."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _run_context(json_path: Path, commands: list, script_dir: Path) -> bool:
    """Replay one context JSON's postproc commands.  Returns True on success."""
    if not commands:
        return True
    results = run_postproc(
        commands, json_path, script_dir, dry_run=False,
    )
    return all(r.get("status") == "success" for r in results)


def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage: python -m q8020_cfd_metautil.sweep_postproc <run_dir>",
            file=sys.stderr,
        )
        sys.exit(2)

    run_dir = Path(sys.argv[1])
    if not run_dir.is_dir():
        print(f"ERROR: run_dir does not exist: {run_dir}", file=sys.stderr)
        sys.exit(2)

    ok = True

    # --- Group post-proc: all groups first, order among them irrelevant ---
    group_jsons = sorted(run_dir.glob("_group_postproc_*.json"))
    for gj in group_jsons:
        with open(gj, encoding="utf-8") as f:
            ctx = json.load(f)
        commands = _as_list(ctx.get("params", {}).get("_group_postproc"))
        script_dir = Path(ctx.get("script_dir", "."))
        print(f"=== Group postproc: {gj.name} ===")
        if not _run_context(gj, commands, script_dir):
            ok = False

    # --- Final post-proc: runs last so it may depend on group outputs ---
    final_json = run_dir / "_final_postproc.json"
    if final_json.exists():
        with open(final_json, encoding="utf-8") as f:
            ctx = json.load(f)
        commands = _as_list(
            ctx.get("global_params", {}).get("_final_postproc")
        )
        script_dir = Path(ctx.get("script_dir", "."))
        print("=== Final postproc ===")
        if not _run_context(final_json, commands, script_dir):
            ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
