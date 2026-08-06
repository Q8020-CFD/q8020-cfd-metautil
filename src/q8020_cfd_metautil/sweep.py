"""Parameter sweep orchestration for quantum experiments.

Reads a TOML config, expands parameter combinations into cases, runs a
script per case with full metadata capture, and organizes results in a
dated workflow directory.  Supports local, parallel, and SLURM execution,
plus multi-stage sweeps.  See the README for the TOML format, directory
layout, and CLI usage.

Key entry points: load_sweep_config, expand_case_lists, run_sweep,
run_single, run_postproc.
"""

#pylint: disable=broad-exception-caught

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

import argparse
import json as _json

import tomllib

import subprocess_tee

from q8020_cfd_metautil.harvest import harvest_metadata
from q8020_cfd_metautil.meta_fragment import (
    generate_experiment_id,
    generate_workflow_id,
    make_library_meta,
    make_user_meta,
    write_artifacts,
    write_case,
    write_code,
    write_exec_stats,
    write_experiment,
)

# ANSI color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BOLD = '\033[1m'
DIM = '\033[2m'
RESET = '\033[0m'


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _get_iso_timestamp() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _derive_experiment_name(command: list[str]) -> str:
    """Derive experiment name from script path in command."""
    if len(command) > 1:
        script_path = Path(command[1])
        return script_path.stem
    return "unknown"


_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")


def _expand_templates(
    obj: Any,
    variables: dict[str, Any],
) -> Any:
    """Recursively replace ${VAR} tokens in string values.

    ``variables`` is typically the resolved [global] params (after
    --set overrides).  Non-string values and keys are left untouched.
    """
    if isinstance(obj, str):
        def _replace(m: re.Match) -> str:
            key = m.group(1)
            val = variables.get(key)
            if val is None:
                return m.group(0)  # leave unresolved
            return str(val)
        return _TEMPLATE_RE.sub(_replace, obj)
    if isinstance(obj, dict):
        return {
            k: _expand_templates(v, variables)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_expand_templates(v, variables) for v in obj]
    return obj


def _expand_templates_fixpoint(
    obj: Any,
    variables: dict[str, Any],
    max_passes: int = 10,
) -> Any:
    """Expand ``${VAR}`` tokens repeatedly until the result is stable, so
    variables whose values themselves contain ``${...}`` references resolve
    fully (e.g. ``_script = "${_launch} solver.py"`` with
    ``_launch = "srun -n ${_ranks} python"``).

    Unknown tokens are left verbatim (same as ``_expand_templates``).
    Raises ValueError if the expansion has not converged after *max_passes*
    iterations, which indicates a reference cycle.
    """
    for _ in range(max_passes):
        expanded = _expand_templates(obj, variables)
        if expanded == obj:
            return expanded
        obj = expanded
    # One more pass to name the offenders: anything that still changes
    # is part of a cycle.
    final = _expand_templates(obj, variables)
    unstable = sorted(
        {
            m
            for s in _iter_strings(final)
            for m in _TEMPLATE_RE.findall(s)
            if m in variables
        }
    )
    raise ValueError(
        f"Template expansion did not converge after {max_passes} passes; "
        f"likely a ${{VAR}} reference cycle involving: {unstable}"
    )


def _iter_strings(obj: Any):
    """Yield every string contained in a nested dict/list/str structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _extract_script_dir(script_cmd: str | None) -> Path | None:
    """Extract the script's parent dir from a (possibly compound) command.

    Handles commands like:
        "python solver.py"
        "source .venv/bin/activate && python3 ./dir/solver.py"
    Returns the absolute parent directory of the .py file, or None.
    """
    if not script_cmd:
        return None
    # Find the last python/python3 invocation and grab the next token
    tokens = script_cmd.replace("\\", " ").split()
    for i in range(len(tokens) - 1, -1, -1):
        base = os.path.basename(tokens[i])
        if base in ("python", "python3") and i + 1 < len(tokens):
            candidate = tokens[i + 1]
            if not candidate.startswith("-"):
                return Path(candidate).parent.absolute()
    # Fallback: if the whole thing looks like a simple path
    if not any(c in script_cmd for c in ("&&", "|", ";")):
        return Path(script_cmd.strip()).parent.absolute()
    return None


def _is_venv(path: Path) -> bool:
    """Check if path looks like a Python virtual environment."""
    path = path.expanduser().resolve()
    # Check for typical venv structure
    if (path / "bin" / "activate").exists() or (path / "Scripts" / "activate.bat").exists():
        return True
    if (path / "pyvenv.cfg").exists():
        return True
    return False


def _capture_venv_packages(venv_path: Path) -> dict[str, Any]:
    """Capture package versions from a venv using importlib.metadata (no pip required)."""
    venv_path = venv_path.expanduser().resolve()
    
    # Find python executable
    python_path = venv_path / "bin" / "python"
    if not python_path.exists():
        python_path = venv_path / "Scripts" / "python.exe"
    
    if not python_path.exists():
        return {"error": f"python not found in {venv_path}"}
    
    # Use importlib.metadata to get installed packages (works without pip)
    capture_script = """
import importlib.metadata
import json
packages = {d.name: d.version for d in importlib.metadata.distributions()}
print(json.dumps(packages))
"""
    
    try:
        result = subprocess.run(
            [str(python_path), "-c", capture_script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        
        if result.returncode != 0:
            return {"error": result.stderr.strip(), "path": str(venv_path)}
        
        packages = json.loads(result.stdout.strip())
        
        return {
            "type": "venv",
            "path": str(venv_path),
            "packages": packages,
            "package_count": len(packages),
            "timestamp": _get_iso_timestamp(),
        }
    except Exception as e:
        return {"error": str(e), "path": str(venv_path)}


def _capture_dir_listing(dir_path: Path) -> dict[str, Any]:
    """Capture detailed file listing for a directory."""
    dir_path = dir_path.expanduser().resolve()
    
    if not dir_path.exists():
        return {"error": f"Directory does not exist: {dir_path}"}
    
    if not dir_path.is_dir():
        return {"error": f"Path is not a directory: {dir_path}"}
    
    files = []
    total_size = 0
    
    try:
        for file_path in dir_path.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                total_size += stat.st_size
                files.append({
                    "path": str(file_path.relative_to(dir_path)),
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat().replace("+00:00", "Z"),
                })
        
        return {
            "type": "directory",
            "path": str(dir_path),
            "files": files,
            "file_count": len(files),
            "total_size_bytes": total_size,
            "timestamp": _get_iso_timestamp(),
        }
    except Exception as e:
        return {"error": str(e), "path": str(dir_path)}


def capture_lib_snapshot(lib_path: str | Path) -> dict[str, Any]:
    """Snapshot a library path: pip freeze if a venv, else a file listing."""
    path = Path(lib_path).expanduser().resolve()
    
    if _is_venv(path):
        return _capture_venv_packages(path)
    else:
        return _capture_dir_listing(path)


def _inventory_artifacts(artifact_dir: Path) -> dict[str, Any]:
    """Scan *artifact_dir* and build a file inventory."""
    result: dict[str, Any] = {
        "_source": "sweep",
        "directory": str(artifact_dir.resolve()),
        "files": [],
    }

    if not artifact_dir.exists():
        result["error"] = "Artifact directory does not exist"
        return result

    if not artifact_dir.is_dir():
        result["error"] = "Artifact path is not a directory"
        return result

    files_list: list[dict[str, Any]] = []
    for file_path in artifact_dir.rglob("*"):
        if file_path.is_file():
            stat = file_path.stat()
            ext = file_path.suffix.lstrip(".") if file_path.suffix else "unknown"
            files_list.append({
                "name": file_path.name,
                "path": str(file_path.relative_to(artifact_dir)),
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat().replace("+00:00", "Z"),
                "type": ext,
            })

    result["files"] = files_list
    return result


def _make_experiment_section(
    name: str,
    experiment_id: str,
    workflow_id: str,
    timestamp: str,
) -> dict[str, Any]:
    """Build experiment metadata section."""
    return {
        "_source": "sweep",
        "name": name,
        "experiment_id": experiment_id,
        "workflow_id": workflow_id,
        "timestamp": timestamp,
        "user": make_user_meta(),
    }


def _make_case_section(
    command: list[str],
    cwd: str,
    case_id: str | None = None,
    case_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the case metadata section.  With *case_params* (sweep mode)
    it carries case_id and params; without, it parses args from *command*
    (standalone mode).
    """
    script = command[1] if len(command) > 1 else None

    if case_params is not None:
        # Sweep mode: include case_id and params
        return {
            "_source": "sweep",
            "name": case_id or " ".join(command),
            "case_id": case_id,
            "params": case_params,
            "command": command,
            "script": script,
            "cwd": cwd,
        }
    else:
        # Standalone mode: include args parsed from command
        raw_args = command[2:] if len(command) > 2 else []
        return {
            "_source": "sweep",
            "name": case_id or " ".join(command),
            "command": command,
            "script": script,
            "args": raw_args,
            "cwd": cwd,
        }


_PY_ENTRY_PATTERN = re.compile(r"(\S+\.py)\b")


def _resolve_app_identity(
    command: list[str],
    case_params: dict[str, Any] | None,
) -> tuple[str, str | None, str | None]:
    """Resolve (interpreter, entry_point, algorithm) for the code section.

    The bare ``command[0]``/``command[1]`` slots record the shell wrapper
    (``bash -c "...python solver.py..."``), not the app.  Prefer the TOML
    ``_script`` (carried through as a case param) and the ``--method`` token
    so the code section names the app, not the shell.
    """
    interpreter = command[0] if command else "unknown"
    entry_point = command[1] if len(command) > 1 else None
    algorithm: str | None = None

    if case_params:
        script = case_params.get("_script")
        method = case_params.get("--method") or case_params.get("method")
        if isinstance(method, str):
            algorithm = method
        if isinstance(script, str) and script:
            # e.g. "python ./pkg/src/burgers_solver.py" -> py file + interp.
            toks = script.split()
            interpreter = toks[0] if toks else interpreter
            m = _PY_ENTRY_PATTERN.search(script)
            if m:
                entry_point = Path(m.group(1)).name

    # Fallback: unwrap a "bash -c <cmd>" wrapper when no _script was given.
    if entry_point in (None, "-c") and command:
        joined = " ".join(command)
        m = _PY_ENTRY_PATTERN.search(joined)
        if m:
            entry_point = Path(m.group(1)).name
            interpreter = "python"

    return interpreter, entry_point, algorithm


def _make_code_section(
    command: list[str],
    env_before: dict[str, Any] | None = None,
    env_after: dict[str, Any] | None = None,
    case_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build code metadata section with optional library version snapshots."""
    interpreter, entry_point, algorithm = _resolve_app_identity(
        command, case_params
    )

    result: dict[str, Any] = {
        "_source": "sweep",
        "entry_point": entry_point,
        "interpreter": interpreter,
    }
    if algorithm is not None:
        result["algorithm"] = algorithm
    if env_before is not None:
        result["library_versions_before"] = env_before
    if env_after is not None:
        result["library_versions_after"] = env_after
    return result


# ---------------------------------------------------------------------------
# Multi-stage helpers
# ---------------------------------------------------------------------------

_STAGE_VAR_PATTERN = re.compile(r"\{\{stages\.(\w+)\.(\w+)\}\}")


def _parse_stages(
    data: dict[str, Any],
    outer_global: dict[str, Any],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]] | None:
    """Parse ``[stage.<name>]`` sections into ordered
    ``(name, stage_global, stage_groups)`` tuples, or None if the TOML has
    no ``stage`` key.  Each stage merges *outer_global* as defaults
    (stage scalars override, groups override further).
    """
    if "stage" not in data:
        return None

    raw_stages = data["stage"]
    if not isinstance(raw_stages, dict) or not raw_stages:
        return None

    stages: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    for stage_name, stage_data in raw_stages.items():
        if not isinstance(stage_data, dict):
            continue

        # Validate stage name (must work in {{stages.<name>.prop}} syntax)
        if not re.match(r"^\w+$", stage_name):
            raise ValueError(
                f"Stage name '{stage_name}' contains invalid characters. "
                f"Stage names must be alphanumeric/underscores only."
            )

        # Separate scalar keys (stage-level params) from dict keys (groups)
        stage_global = outer_global.copy()
        stage_groups_raw: dict[str, dict[str, Any]] = {}

        for k, v in stage_data.items():
            if isinstance(v, dict):
                stage_groups_raw[k] = v
            else:
                stage_global[k] = v

        # Expand groups using the same logic as load_sweep_config
        groups: dict[str, Any] = {}
        for gid, gparams in stage_groups_raw.items():
            expanded = expand_case_lists(gid, gparams)
            expanded_cases: dict[str, dict[str, Any]] = {}
            for eid, ep in expanded:
                merged = stage_global.copy()
                merged.update(ep)
                expanded_cases[eid] = merged

            group_params = stage_global.copy()
            group_params.update(gparams)
            groups[gid] = {
                "params": group_params,
                "expanded_cases": expanded_cases,
            }

        stages.append((stage_name, stage_global, groups))

    return stages if stages else None


def _substitute_stage_vars(
    params: dict[str, Any],
    stage_context: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve ``{{stages.<name>.<prop>}}`` placeholders in *params* (not
    mutated) against *stage_context*.  Only strings (incl. those in lists)
    are scanned.  Raises ValueError on an unknown stage or property.
    """
    def _replacer(m: re.Match) -> str:
        sname, prop = m.group(1), m.group(2)
        if sname not in stage_context:
            raise ValueError(
                f"Stage variable '{{{{stages.{sname}.{prop}}}}}' references "
                f"unknown stage '{sname}'. "
                f"Completed stages: {list(stage_context.keys())}"
            )
        ctx = stage_context[sname]
        if prop not in ctx:
            raise ValueError(
                f"Stage variable '{{{{stages.{sname}.{prop}}}}}' references "
                f"unknown property '{prop}'. "
                f"Available: {list(ctx.keys())}"
            )
        return str(ctx[prop])

    result: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str) and "{{stages." in v:
            result[k] = _STAGE_VAR_PATTERN.sub(_replacer, v)
        elif isinstance(v, list):
            result[k] = [
                _STAGE_VAR_PATTERN.sub(_replacer, item)
                if isinstance(item, str) and "{{stages." in item
                else item
                for item in v
            ]
        else:
            result[k] = v
    return result


def _substitute_stage_vars_in_groups(
    groups: dict[str, Any],
    stage_context: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Apply stage variable substitution across all groups and their cases.

    Returns a new groups dict — the original is not mutated.
    """
    resolved: dict[str, Any] = {}
    for gid, gdata in groups.items():
        resolved_params = _substitute_stage_vars(
            gdata["params"], stage_context,
        )
        resolved_expanded: dict[str, dict[str, Any]] = {}
        for eid, ep in gdata["expanded_cases"].items():
            resolved_expanded[eid] = _substitute_stage_vars(
                ep, stage_context,
            )
        resolved[gid] = {
            "params": resolved_params,
            "expanded_cases": resolved_expanded,
        }
    return resolved


# ---------------------------------------------------------------------------
# Single case execution
# ---------------------------------------------------------------------------

def run_single(
    command: list[str],
    case_dir: Path,
    experiment_id: str,
    workflow_id: str,
    case_id: str | None = None,
    case_params: dict[str, Any] | None = None,
    timeout: int | None = None,
    artifact_dir: Path | None = None,
    env_path: str | None = None,
    process_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one case command with full metadata capture (the inner function
    run_sweep() calls per case).  *case_dir* must already exist; *env_path*
    is snapshotted before and after.  *process_env* replaces the subprocess
    environment when given (None = inherit).  Returns a result dict whose
    status is "success", "timeout", or "exception".
    """
    # Default artifact_dir to case_dir if not specified
    if artifact_dir is None:
        artifact_dir = case_dir

    stderr_file = case_dir / f"q8020_stderr_{experiment_id}.txt"
    params_file = case_dir / f"q8020_params_{experiment_id}.json"

    cwd = os.getcwd()

    # Time the pre-solver overhead (env capture, params write)
    t_pre_start = time.perf_counter()

    # Capture environment snapshot before execution
    # Always capture library versions for reproducibility
    if env_path:
        env_before = capture_lib_snapshot(env_path)
    else:
        # No explicit env_path - capture from current process environment
        env_before = {
            "type": "current_process",
            "packages": make_library_meta(),
            "timestamp": _get_iso_timestamp(),
        }
    env_before_file = (
        case_dir / f"q8020_env_before_{experiment_id}.json"
    )
    with open(env_before_file, "w", encoding="utf-8") as f:
        json.dump(env_before, f, indent=2)

    # Build params.json content
    params_data: dict[str, Any] = {
        "command": command,
        "script": command[1] if len(command) > 1 else None,
        "args": command[2:] if len(command) > 2 else [],
        "workflow_id": workflow_id,
        "experiment_id": experiment_id,
        "timestamp": datetime.now().isoformat(),
        "cwd": cwd,
    }
    if case_id is not None:
        params_data["_case_id"] = case_id
    if case_params is not None:
        params_data.update(
            {k: v for k, v in case_params.items()
             if not k.startswith("_")}
        )

    with open(params_file, "w", encoding="utf-8") as f:
        json.dump(params_data, f, indent=2)

    t_pre_seconds = time.perf_counter() - t_pre_start

    # Record start time
    start_time = _get_iso_timestamp()
    start_dt = datetime.now(timezone.utc)

    # Base result dict (updated on success/failure)
    result_dict: dict[str, Any] = {
        "command": command,
        "case_id": case_id,
        "experiment_id": experiment_id,
        "output_dir": str(case_dir),
    }

    try:
        # Run the command, capturing stdout and stderr while also echoing to console
        run_kwargs: dict[str, Any] = {}
        if process_env is not None:
            run_kwargs["env"] = process_env
        result = subprocess_tee.run(
            command,
            check=False,
            timeout=timeout,
            **run_kwargs,
        )

        # Record end time
        end_time = _get_iso_timestamp()
        end_dt = datetime.now(timezone.utc)
        duration_seconds = (end_dt - start_dt).total_seconds()

        # Build exec_stats
        exec_stats: dict[str, Any] = {
            "_source": "sweep",
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration_seconds,
            "exit_code": result.returncode,
            "success": result.returncode == 0,
        }

        # Write stderr
        with open(stderr_file, "w", encoding="utf-8") as f:
            f.write(result.stderr)

        # Write stdout
        stdout_content = result.stdout.strip()
        stdout_file = case_dir / f"q8020_stdout_{experiment_id}.txt"
        with open(stdout_file, "w", encoding="utf-8") as f:
            f.write(stdout_content)

        # Write metadata fragments
        experiment_name = case_id if case_id else _derive_experiment_name(command)
        experiment_section = _make_experiment_section(
            name=experiment_name,
            experiment_id=experiment_id,
            workflow_id=workflow_id,
            timestamp=start_time,
        )
        case_section = _make_case_section(
            command=command,
            cwd=cwd,
            case_id=case_id,
            case_params=case_params,
        )
        artifacts_section = _inventory_artifacts(artifact_dir)

        write_experiment(case_dir, experiment_section, prefix="q8020_sweep", experiment_id=experiment_id)
        write_case(case_dir, case_section, prefix="q8020_sweep", experiment_id=experiment_id)
        # Note: write_code is deferred until after env_after is captured
        write_exec_stats(case_dir, exec_stats, prefix="q8020_sweep", experiment_id=experiment_id)
        write_artifacts(case_dir, artifacts_section, prefix="q8020_sweep", experiment_id=experiment_id)

        # Note: harvest_metadata() is called by run_sweep() AFTER postproc completes

        result_dict["status"] = "success" if result.returncode == 0 else "error"
        result_dict["returncode"] = result.returncode

    except subprocess.TimeoutExpired:
        # Write partial metadata on timeout
        end_time = _get_iso_timestamp()
        end_dt = datetime.now(timezone.utc)
        duration_seconds = (end_dt - start_dt).total_seconds()

        exec_stats = {
            "_source": "sweep",
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration_seconds,
            "exit_code": None,
            "success": False,
            "error": "timeout",
        }

        experiment_name = case_id if case_id else _derive_experiment_name(command)
        experiment_section = _make_experiment_section(
            name=experiment_name,
            experiment_id=experiment_id,
            workflow_id=workflow_id,
            timestamp=start_time,
        )
        case_section = _make_case_section(
            command=command, cwd=cwd, case_id=case_id, case_params=case_params
        )
        write_experiment(case_dir, experiment_section, prefix="q8020_sweep", experiment_id=experiment_id)
        write_case(case_dir, case_section, prefix="q8020_sweep", experiment_id=experiment_id)
        # Note: write_code is deferred until after env_after is captured
        write_exec_stats(case_dir, exec_stats, prefix="q8020_sweep", experiment_id=experiment_id)

        result_dict["status"] = "timeout"

    except Exception as e:
        # Write partial metadata on exception
        end_time = _get_iso_timestamp()
        end_dt = datetime.now(timezone.utc)
        duration_seconds = (end_dt - start_dt).total_seconds()

        exec_stats = {
            "_source": "sweep",
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration_seconds,
            "exit_code": None,
            "success": False,
            "error": str(e),
        }

        experiment_name = case_id if case_id else _derive_experiment_name(command)
        experiment_section = _make_experiment_section(
            name=experiment_name,
            experiment_id=experiment_id,
            workflow_id=workflow_id,
            timestamp=start_time,
        )
        case_section = _make_case_section(
            command=command, cwd=cwd, case_id=case_id, case_params=case_params
        )
        write_experiment(case_dir, experiment_section, prefix="q8020_sweep", experiment_id=experiment_id)
        write_case(case_dir, case_section, prefix="q8020_sweep", experiment_id=experiment_id)
        # Note: write_code is deferred until after env_after is captured
        write_exec_stats(case_dir, exec_stats, prefix="q8020_sweep", experiment_id=experiment_id)

        result_dict["status"] = "exception"
        result_dict["error"] = str(e)

    # Time the post-solver overhead (env_after, fragment writes)
    t_post_start = time.perf_counter()

    # Capture environment snapshot after execution
    # Always capture library versions for reproducibility
    if env_path:
        env_after = capture_lib_snapshot(env_path)
    else:
        # No explicit env_path - capture from current process environment
        env_after = {
            "type": "current_process",
            "packages": make_library_meta(),
            "timestamp": _get_iso_timestamp(),
        }
    env_after_file = (
        case_dir / f"q8020_env_after_{experiment_id}.json"
    )
    with open(env_after_file, "w", encoding="utf-8") as f:
        json.dump(env_after, f, indent=2)

    # Write code fragment with both env snapshots
    code_section = _make_code_section(
        command=command,
        env_before=env_before,
        env_after=env_after,
        case_params=case_params,
    )
    write_code(
        case_dir, code_section,
        prefix="q8020_sweep", experiment_id=experiment_id,
    )

    t_post_seconds = time.perf_counter() - t_post_start

    # Add overhead timing to result
    result_dict["overhead"] = {
        "pre_seconds": round(t_pre_seconds, 6),
        "post_seconds": round(t_post_seconds, 6),
    }

    return result_dict


# ---------------------------------------------------------------------------
# Parallel execution support
# ---------------------------------------------------------------------------

def run_case_pipeline(
    cmd: list[str],
    case_dir: Path,
    experiment_id: str,
    workflow_id: str,
    case_id: str,
    case_params: dict[str, Any],
    env_path: str | None,
    script_dir: Path,
    case_preproc: list[str],
    case_postproc: list[str],
) -> dict[str, Any]:
    """Run the full per-case pipeline: preproc, solver, postproc, harvest.

    Used by sweep_worker.py for parallel execution. Each parallel
    subprocess calls this to run one case end-to-end.
    """
    case_dir = Path(case_dir)
    script_dir = Path(script_dir)
    result: dict[str, Any] = {
        "case_id": case_id,
        "experiment_id": experiment_id,
    }

    # --- preproc ---
    t_preproc = 0.0
    if case_preproc:
        preproc_data = {
            "case_id": case_id,
            "experiment_id": experiment_id,
            "workflow_id": workflow_id,
            "case_dir": str(case_dir),
            "params": case_params,
        }
        preproc_json = (
            case_dir / f"q8020_case_preproc_{experiment_id}.json"
        )
        with open(preproc_json, "w", encoding="utf-8") as f:
            json.dump(preproc_data, f, indent=2)
        t_pp_start = time.perf_counter()
        pre_results = run_postproc(
            case_preproc, preproc_json, script_dir,
            dry_run=False, case_dir=case_dir,
            proc_type="preproc", experiment_id=experiment_id,
        )
        t_preproc = time.perf_counter() - t_pp_start
        result["_case_preproc"] = pre_results

    # --- solver (subprocess.run, file-based output) ---
    cwd = os.getcwd()
    stderr_file = case_dir / f"q8020_stderr_{experiment_id}.txt"
    stdout_file = case_dir / f"q8020_stdout_{experiment_id}.txt"
    params_file = case_dir / f"q8020_params_{experiment_id}.json"

    # env snapshot before
    t_pre_start = time.perf_counter()
    if env_path:
        env_before = capture_lib_snapshot(env_path)
    else:
        env_before = {
            "type": "current_process",
            "packages": make_library_meta(),
            "timestamp": _get_iso_timestamp(),
        }
    env_before_file = (
        case_dir / f"q8020_env_before_{experiment_id}.json"
    )
    with open(env_before_file, "w", encoding="utf-8") as f:
        json.dump(env_before, f, indent=2)

    # params file
    params_data: dict[str, Any] = {
        "command": cmd,
        "script": cmd[1] if len(cmd) > 1 else None,
        "args": cmd[2:] if len(cmd) > 2 else [],
        "workflow_id": workflow_id,
        "experiment_id": experiment_id,
        "timestamp": datetime.now().isoformat(),
        "cwd": cwd,
        "_case_id": case_id,
    }
    params_data.update(
        {k: v for k, v in case_params.items()
         if not k.startswith("_")}
    )
    with open(params_file, "w", encoding="utf-8") as f:
        json.dump(params_data, f, indent=2)

    t_pre_seconds = time.perf_counter() - t_pre_start

    start_time = _get_iso_timestamp()
    start_dt = datetime.now(timezone.utc)

    try:
        with open(stdout_file, "w", encoding="utf-8") as fout, \
             open(stderr_file, "w", encoding="utf-8") as ferr:
            proc_result = subprocess.run(
                cmd,
                stdout=fout,
                stderr=ferr,
                check=False,
                timeout=case_params.get("_case_timeout"),
            )
        returncode = proc_result.returncode
        end_time = _get_iso_timestamp()
        end_dt = datetime.now(timezone.utc)
        duration = (end_dt - start_dt).total_seconds()

        exec_stats: dict[str, Any] = {
            "_source": "sweep",
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration,
            "exit_code": returncode,
            "success": returncode == 0,
        }
        result["status"] = (
            "success" if returncode == 0 else "error"
        )
        result["returncode"] = returncode

    except subprocess.TimeoutExpired:
        end_time = _get_iso_timestamp()
        end_dt = datetime.now(timezone.utc)
        duration = (end_dt - start_dt).total_seconds()
        exec_stats = {
            "_source": "sweep",
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration,
            "exit_code": None,
            "success": False,
            "error": "timeout",
        }
        result["status"] = "timeout"

    except Exception as e:
        end_time = _get_iso_timestamp()
        end_dt = datetime.now(timezone.utc)
        duration = (end_dt - start_dt).total_seconds()
        exec_stats = {
            "_source": "sweep",
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration,
            "exit_code": None,
            "success": False,
            "error": str(e),
        }
        result["status"] = "exception"
        result["error"] = str(e)

    # write metadata fragments
    experiment_name = case_id or _derive_experiment_name(cmd)
    experiment_section = _make_experiment_section(
        name=experiment_name,
        experiment_id=experiment_id,
        workflow_id=workflow_id,
        timestamp=start_time,
    )
    case_section = _make_case_section(
        command=cmd, cwd=cwd,
        case_id=case_id, case_params=case_params,
    )
    artifacts_section = _inventory_artifacts(case_dir)

    write_experiment(
        case_dir, experiment_section,
        prefix="q8020_sweep", experiment_id=experiment_id,
    )
    write_case(
        case_dir, case_section,
        prefix="q8020_sweep", experiment_id=experiment_id,
    )
    write_exec_stats(
        case_dir, exec_stats,
        prefix="q8020_sweep", experiment_id=experiment_id,
    )
    write_artifacts(
        case_dir, artifacts_section,
        prefix="q8020_sweep", experiment_id=experiment_id,
    )

    # env snapshot after
    t_post_start = time.perf_counter()
    if env_path:
        env_after = capture_lib_snapshot(env_path)
    else:
        env_after = {
            "type": "current_process",
            "packages": make_library_meta(),
            "timestamp": _get_iso_timestamp(),
        }
    env_after_file = (
        case_dir / f"q8020_env_after_{experiment_id}.json"
    )
    with open(env_after_file, "w", encoding="utf-8") as f:
        json.dump(env_after, f, indent=2)

    code_section = _make_code_section(
        command=cmd,
        env_before=env_before,
        env_after=env_after,
        case_params=case_params,
    )
    write_code(
        case_dir, code_section,
        prefix="q8020_sweep", experiment_id=experiment_id,
    )
    t_post_seconds = time.perf_counter() - t_post_start

    # --- postproc ---
    t_postproc = 0.0
    if case_postproc:
        postproc_data = {
            "case_id": case_id,
            "experiment_id": experiment_id,
            "workflow_id": workflow_id,
            "case_dir": str(case_dir),
            "params": case_params,
        }
        postproc_json = (
            case_dir / f"q8020_case_postproc_{experiment_id}.json"
        )
        with open(postproc_json, "w", encoding="utf-8") as f:
            json.dump(postproc_data, f, indent=2)
        t_pp_start = time.perf_counter()
        pp_results = run_postproc(
            case_postproc, postproc_json, script_dir,
            dry_run=False, case_dir=case_dir,
            proc_type="postproc", experiment_id=experiment_id,
        )
        t_postproc = time.perf_counter() - t_pp_start
        result["_case_postproc"] = pp_results

    # --- harvest ---
    t_harvest_start = time.perf_counter()
    metadata_file = (
        case_dir / f"q8020_metadata_{experiment_id}.json"
    )
    unified_metadata, warnings, _ = harvest_metadata(case_dir)
    for warning in warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(unified_metadata, f, indent=2)
    t_harvest = time.perf_counter() - t_harvest_start

    result["output_dir"] = str(case_dir)
    result["command"] = cmd
    result["overhead"] = {
        "pre_seconds": round(t_pre_seconds, 6),
        "post_seconds": round(t_post_seconds, 6),
        "preproc_seconds": round(t_preproc, 6),
        "postproc_seconds": round(t_postproc, 6),
        "harvest_seconds": round(t_harvest, 6),
    }

    return result


def _build_case_launch_info(
    case_id: str,
    params: dict[str, Any],
    run_dir: Path,
    workflow_id: str,
    group_executable: str,
    script_dir: Path,
) -> dict[str, Any]:
    """Build launch info for a single case (shared by local and SLURM).

    Returns dict with experiment_id, case_dir, cmd, pipeline_args,
    and the path to the serialized args file.
    """
    experiment_id = generate_experiment_id()
    # Nest trial directories under their parent case directory
    parent_case_id = params.get("_parent_case_id")
    if parent_case_id:
        trial_index = params.get("_trial_index", 0)
        case_dir = run_dir / parent_case_id / f"trial_{trial_index}_{experiment_id}"
    else:
        case_dir = run_dir / experiment_id
    case_dir.mkdir(parents=True, exist_ok=True)

    # Build solver command (same logic as run_sweep sequential path)
    cmd_args = build_command_args(params)
    inject_outdir = params.get("_inject_outdir")
    if inject_outdir:
        cmd_args.extend([inject_outdir, str(case_dir)])
    inject_exp = params.get("_inject_experiment_id")
    if inject_exp:
        cmd_args.extend([inject_exp, experiment_id])
    inject_wf = params.get("_inject_workflow_id")
    if inject_wf:
        cmd_args.extend([inject_wf, workflow_id])

    if ("&&" in group_executable
            or "||" in group_executable
            or "|" in group_executable):
        full_cmd = f"{group_executable} {' '.join(cmd_args)}"
        cmd = ["bash", "-c", full_cmd]
    else:
        cmd_parts = group_executable.split()
        cmd_parts = [
            str(Path(p).expanduser())
            if p.startswith("~") or "/" in p or "\\" in p
            else p
            for p in cmd_parts
        ]
        cmd = cmd_parts + cmd_args

    env_path = params.get("_env")
    case_preproc = params.get("_case_preproc", [])
    if isinstance(case_preproc, str):
        case_preproc = [case_preproc]
    case_postproc = params.get("_case_postproc", [])
    if isinstance(case_postproc, str):
        case_postproc = [case_postproc]

    pipeline_args = {
        "cmd": cmd,
        "case_dir": str(case_dir),
        "experiment_id": experiment_id,
        "workflow_id": workflow_id,
        "case_id": case_id,
        "case_params": params,
        "env_path": env_path,
        "script_dir": str(script_dir),
        "case_preproc": case_preproc,
        "case_postproc": case_postproc,
    }

    args_file = case_dir / "pipeline_args.json"
    with open(args_file, "w", encoding="utf-8") as f:
        json.dump(pipeline_args, f, indent=2)

    return {
        "experiment_id": experiment_id,
        "case_id": case_id,
        "case_dir": case_dir,
        "cmd": cmd,
        "args_file": args_file,
        "params": params,
    }


def _finalize_slurm_interactive(
    all_cases_info: list[dict[str, Any]],
    all_results: dict[str, Any],
    global_params: dict[str, Any],
    dry_run: bool = False,
) -> None:
    """Launch all collected cases via srun within an active salloc allocation.

    Requires an existing SLURM allocation (e.g. from ``salloc``).
    Each case is launched as a separate ``srun`` job step and polled
    until completion, similar to ``_finalize_local_parallel`` but
    distributing work across allocated compute nodes.

    Mutates all_results in place with final statuses.
    """
    if "SLURM_JOB_ID" not in os.environ:
        if dry_run:
            print(
                f"  {YELLOW}Warning: SLURM_JOB_ID not found."
                f" Interactive mode requires an active"
                f" salloc allocation.{RESET}"
            )
        else:
            raise RuntimeError(
                "SLURM interactive mode requires an active salloc "
                "allocation. No SLURM_JOB_ID found in environment. "
                "Run 'salloc -A <project> -N <nodes> -t <time> "
                "-p batch' first, then re-run q8020-sweep."
            )

    cores = int(global_params.get("_slurm_cores_per_task", 1))
    poll_interval = float(global_params.get("_poll_interval", 2))

    if dry_run:
        for info in all_cases_info:
            srun_cmd = [
                "srun", "-n", "1", "-c", str(cores), "--exclusive",
                sys.executable, "-m",
                "q8020_cfd_metautil.sweep_worker",
                str(info["args_file"]),
            ]
            print(
                f"  {DIM}Would launch:{RESET}"
                f" {' '.join(srun_cmd)}"
            )
        for eid in all_results:
            all_results[eid]["status"] = "dry_run"
        return

    alloc_id = os.environ.get("SLURM_JOB_ID", "?")
    print(
        f"  {CYAN}Using salloc allocation:{RESET} {alloc_id}"
    )

    worker_env = _resolve_env_exports(global_params)
    inflight: list[dict[str, Any]] = []

    for info in all_cases_info:
        srun_cmd = [
            "srun", "-n", "1", "-c", str(cores), "--exclusive",
            sys.executable, "-m",
            "q8020_cfd_metautil.sweep_worker",
            str(info["args_file"]),
        ]

        print(f"  {YELLOW}Launched:{RESET} {info['case_id']}"
              f" ({info['experiment_id']})")

        stdout_log = (
            info["case_dir"] / "pipeline_stdout.txt"
        )
        stderr_log = (
            info["case_dir"] / "pipeline_stderr.txt"
        )
        fout = open(stdout_log, "w", encoding="utf-8")
        ferr = open(stderr_log, "w", encoding="utf-8")

        proc = subprocess.Popen(
            srun_cmd,
            stdout=fout,
            stderr=ferr,
            env=worker_env,
        )
        inflight.append({
            "proc": proc,
            "info": info,
            "start_time": time.time(),
            "fout": fout,
            "ferr": ferr,
        })

    # Poll loop
    total = len(inflight)
    completed = 0
    failed = 0

    print(
        f"\n  {CYAN}Waiting for {total} cases"
        f" (srun interactive)...{RESET}\n",
        flush=True,
    )

    remaining = list(inflight)
    while remaining:
        for entry in remaining[:]:
            proc = entry["proc"]
            ret = proc.poll()
            if ret is None:
                continue

            remaining.remove(entry)
            entry["fout"].close()
            entry["ferr"].close()
            info = entry["info"]
            elapsed = time.time() - entry["start_time"]
            eid = info["experiment_id"]
            cid = info["case_id"]

            result_file = (
                info["case_dir"] / "pipeline_result.json"
            )
            if result_file.exists():
                with open(result_file, encoding="utf-8") as f:
                    case_result = json.load(f)
            else:
                case_result = {
                    "case_id": cid,
                    "experiment_id": eid,
                    "status": "error",
                    "returncode": ret,
                    "output_dir": str(info["case_dir"]),
                }

            all_results[eid] = case_result

            if ret == 0:
                completed += 1
                mins = int(elapsed // 60)
                secs = elapsed % 60
                print(
                    f"  {GREEN}{BOLD}done:{RESET} {cid}"
                    f" ({mins}m {secs:.0f}s)"
                )
            else:
                failed += 1
                print(
                    f"  {RED}{BOLD}fail:{RESET} {cid}"
                    f" (exit {ret})"
                )

            done = completed + failed
            print(
                f"       {done}/{total} "
                f"({completed} ok, {failed} err)",
                flush=True,
            )

        if remaining:
            time.sleep(poll_interval)


def _finalize_local_parallel(
    all_cases_info: list[dict[str, Any]],
    all_results: dict[str, Any],
    global_params: dict[str, Any],
    dry_run: bool = False,
) -> None:
    """Launch all collected cases concurrently via local Popen.

    Mutates all_results in place with final statuses.
    """
    poll_interval = float(
        global_params.get("_poll_interval", 2)
    )

    if dry_run:
        for info in all_cases_info:
            worker_cmd = [
                sys.executable, "-m",
                "q8020_cfd_metautil.sweep_worker",
                str(info["args_file"]),
            ]
            print(
                f"  {DIM}Would launch:{RESET}"
                f" {' '.join(worker_cmd)}"
            )
        for eid in all_results:
            all_results[eid]["status"] = "dry_run"
        return

    worker_env = _resolve_env_exports(global_params)
    inflight: list[dict[str, Any]] = []

    for info in all_cases_info:
        worker_cmd = [
            sys.executable, "-m",
            "q8020_cfd_metautil.sweep_worker",
            str(info["args_file"]),
        ]

        print(f"  {YELLOW}Launched:{RESET} {info['case_id']}"
              f" ({info['experiment_id']})")

        stdout_log = (
            info["case_dir"] / "pipeline_stdout.txt"
        )
        stderr_log = (
            info["case_dir"] / "pipeline_stderr.txt"
        )
        fout = open(stdout_log, "w", encoding="utf-8")
        ferr = open(stderr_log, "w", encoding="utf-8")

        proc = subprocess.Popen(
            worker_cmd,
            stdout=fout,
            stderr=ferr,
            env=worker_env,
        )
        inflight.append({
            "proc": proc,
            "info": info,
            "start_time": time.time(),
            "fout": fout,
            "ferr": ferr,
        })

    # Poll loop
    total = len(inflight)
    completed = 0
    failed = 0

    print(
        f"\n  {CYAN}Waiting for {total} cases...{RESET}\n",
        flush=True,
    )

    remaining = list(inflight)
    while remaining:
        for entry in remaining[:]:
            proc = entry["proc"]
            ret = proc.poll()
            if ret is None:
                continue

            remaining.remove(entry)
            entry["fout"].close()
            entry["ferr"].close()
            info = entry["info"]
            elapsed = time.time() - entry["start_time"]
            eid = info["experiment_id"]
            cid = info["case_id"]

            result_file = (
                info["case_dir"] / "pipeline_result.json"
            )
            if result_file.exists():
                with open(result_file, encoding="utf-8") as f:
                    case_result = json.load(f)
            else:
                case_result = {
                    "case_id": cid,
                    "experiment_id": eid,
                    "status": "error",
                    "returncode": ret,
                    "output_dir": str(info["case_dir"]),
                }

            all_results[eid] = case_result

            if ret == 0:
                completed += 1
                mins = int(elapsed // 60)
                secs = elapsed % 60
                print(
                    f"  {GREEN}{BOLD}done:{RESET} {cid}"
                    f" ({mins}m {secs:.0f}s)"
                )
            else:
                failed += 1
                print(
                    f"  {RED}{BOLD}fail:{RESET} {cid}"
                    f" (exit {ret})"
                )

            done = completed + failed
            print(
                f"       {done}/{total} "
                f"({completed} ok, {failed} err)",
                flush=True,
            )

        if remaining:
            time.sleep(poll_interval)


def _pack_venvs(
    venv_paths: list[str],
    run_dir: Path,
    dry_run: bool = False,
) -> dict[str, Path]:
    """Tar each venv for NVMe broadcast.

    Returns mapping of resolved venv path -> tarball Path.
    Skips rebuild if tarball is newer than venv's lib/ directory.
    """
    packed: dict[str, Path] = {}
    for raw_path in venv_paths:
        venv_dir = Path(raw_path).expanduser().resolve()
        if not venv_dir.is_dir():
            print(
                f"{YELLOW}Warning: venv not found,"
                f" skipping pack: {venv_dir}{RESET}",
                file=sys.stderr,
            )
            continue

        # Use venv directory name as tarball stem
        safe_name = str(venv_dir).replace("/", "_").strip("_")
        tarball = run_dir / f"{safe_name}.tar.gz"

        # Freshness: skip if tarball exists and is newer than
        # the venv lib/ dir (proxy for pip install changes)
        lib_dir = venv_dir / "lib"
        if (
            tarball.exists()
            and lib_dir.exists()
            and tarball.stat().st_mtime
            > lib_dir.stat().st_mtime
        ):
            print(
                f"  {DIM}Pack up-to-date:{RESET} {tarball}"
            )
            packed[str(venv_dir)] = tarball
            continue

        if dry_run:
            print(
                f"  {DIM}Would pack:{RESET}"
                f" {venv_dir} -> {tarball}"
            )
            packed[str(venv_dir)] = tarball
            continue

        print(
            f"  Packing {venv_dir} ...", end="", flush=True
        )
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(str(venv_dir), arcname=".")
        size_mb = tarball.stat().st_size / (1024 * 1024)
        print(f" {size_mb:.1f} MB")
        packed[str(venv_dir)] = tarball

    return packed


def _get_env_exports(global_params: dict[str, Any]) -> dict[str, str]:
    """Validate and normalize the ``_env_exports`` directive.

    Returns a ``{name: value}`` dict with values coerced to str.  Raises
    ValueError if the key is present but not a TOML table.
    """
    raw = global_params.get("_env_exports")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            "_env_exports must be a TOML table of NAME = value pairs, "
            f"got {type(raw).__name__}: {raw!r}"
        )
    return {str(k): str(v) for k, v in raw.items()}


def _resolve_env_exports(
    global_params: dict[str, Any],
) -> dict[str, str] | None:
    """Build the process environment for local / interactive execution:
    ``os.environ`` overlaid with ``_env_exports`` values.  ``$VAR``
    references in values are expanded against the current environment
    (unknown vars are left verbatim).  Returns None when no exports are
    configured, so callers can pass it straight to ``env=`` (None =
    inherit).
    """
    exports = _get_env_exports(global_params)
    if not exports:
        return None
    env = os.environ.copy()
    for name, value in exports.items():
        env[name] = os.path.expandvars(value)
    return env


_LAUNCHER_BASENAMES = {"srun", "mpirun", "mpiexec"}


def _script_has_launcher(script_cmd: str) -> bool:
    """True if any whitespace token of *script_cmd* is itself a parallel
    launcher (srun/mpirun/mpiexec).  Matches on the token's basename so
    ``/usr/bin/srun`` counts but a path merely containing the substring
    (e.g. ``~/my-srun-env/bin/python``) does not.
    """
    for token in script_cmd.split():
        if os.path.basename(token) in _LAUNCHER_BASENAMES:
            return True
    return False


def _synthesize_launcher_prefix(params: dict[str, Any]) -> str | None:
    """Build the ``srun ...`` prefix for a multi-rank Slurm case from the
    ``_slurm_*`` geometry directives, or return None if no launcher should
    be synthesized.  *params* is the group's merged param dict.

    ``_slurm_launcher`` modes:
      - ``"auto"`` (default): synthesize when ``_slurm_tasks_per_case > 1``
        unless ``_script`` already carries its own launcher token.
      - ``"srun"``: always synthesize (no back-off).
      - ``"none"``: never synthesize.
    """
    mode = str(params.get("_slurm_launcher", "auto")).lower()
    if mode not in ("auto", "srun", "none"):
        raise ValueError(
            f"_slurm_launcher must be 'auto', 'srun', or 'none', "
            f"got: {mode!r}"
        )
    if mode == "none":
        return None

    tasks_per_case = int(params.get("_slurm_tasks_per_case", 1))
    if tasks_per_case <= 1:
        return None

    tasks_per_node = int(params.get("_slurm_tasks_per_node", 8))
    n_nodes = -(-tasks_per_case // tasks_per_node)

    parts = [
        "srun",
        f"-N {n_nodes}",
        f"-n {tasks_per_case}",
        f"--ntasks-per-node={tasks_per_node}",
    ]
    gpus_per_task = params.get("_slurm_gpus_per_task")
    if gpus_per_task is not None:
        parts.append(f"--gpus-per-task={int(gpus_per_task)}")
    parts.append("--exclusive")
    return " ".join(parts)


def _generate_sbatch_script(
    cases_info: list[dict[str, Any]],
    global_params: dict[str, Any],
    run_dir: Path,
    dependency_job_id: str | None = None,
    packed_venvs: dict[str, Path] | None = None,
) -> str:
    """Generate one sbatch script running all cases in parallel, returned
    as a string.  *dependency_job_id* adds ``--dependency=afterok:<id>``;
    *packed_venvs* maps resolved venv path -> tarball (from ``_pack_venvs``).
    """
    project = global_params.get("_slurm_project", "<PROJECT_ID>")
    walltime = global_params.get("_slurm_walltime", "02:00:00")
    partition = global_params.get("_slurm_partition", "batch")
    conda_archive = global_params.get("_slurm_conda_archive", "")
    cores = int(global_params.get("_slurm_cores_per_task", 1))
    exclusive_node = bool(
        global_params.get("_slurm_exclusive_node", False)
    )

    tasks_per_case = int(
        global_params.get("_slurm_tasks_per_case", 1)
    )
    mpi_tasks_per_node = int(
        global_params.get("_slurm_tasks_per_node", 8)
    )

    n_cases = len(cases_info)
    cores_per_node = int(
        global_params.get("_slurm_cores_per_node", 64)
    )
    if tasks_per_case > 1:
        # Distributed (MPI) cases run SEQUENTIALLY on one shared node
        # pool sized for a single case: concurrent multi-rank job steps
        # in one allocation race in Cray MPICH's PMI/shared-memory init
        # ("MPIDU_Init_shm_init: unable to attach to shared memory").
        n_nodes = -(-tasks_per_case // mpi_tasks_per_node)
    elif exclusive_node:
        n_nodes = n_cases  # 1 node per case
    else:
        tasks_per_node = max(1, cores_per_node // cores)
        n_nodes = max(1, -(-n_cases // tasks_per_node))

    # Collect worker commands
    srun_lines = []
    for info in cases_info:
        if tasks_per_case > 1:
            # No srun wrapper: the case's _script carries its own
            # `srun -n {tasks_per_case}` job step. Wrapping the worker
            # in a 1-task srun would make that inner srun hang waiting
            # for resources already held by the outer step.
            # No trailing `&` either: cases run one after another so
            # multi-rank steps never overlap during MPI init.
            worker = (
                f"python3 -m q8020_cfd_metautil.sweep_worker "
                f"{info['args_file']}"
            )
        elif exclusive_node:
            worker = (
                f"srun -N 1 -n 1 -c {cores} --exclusive "
                f"python3 -m q8020_cfd_metautil.sweep_worker "
                f"{info['args_file']} &"
            )
        else:
            worker = (
                f"srun -n 1 -c {cores} --exclusive "
                f"python3 -m q8020_cfd_metautil.sweep_worker "
                f"{info['args_file']} &"
            )
        srun_lines.append(worker)

    srun_block = "\n".join(srun_lines)

    # Environment broadcast section
    env_section = ""
    nvme_venv_map: dict[str, str] = {}  # original path -> NVMe path

    if packed_venvs:
        lines = [
            "# --- Broadcast packed venvs to NVMe ---",
        ]
        for i, (orig_path, tarball) in enumerate(
            packed_venvs.items()
        ):
            env_name = f"venv_{i}"
            nvme_path = f"/mnt/bb/${{USER}}/{env_name}"
            nvme_venv_map[orig_path] = nvme_path
            lines.append(f"echo 'Broadcasting {tarball.name}...'")
            lines.append(
                f"sbcast -pf {tarball}"
                f" /mnt/bb/${{USER}}/{env_name}.tar.gz"
            )
            lines.append(
                f'if [ "$?" != "0" ]; then'
                f' echo "ERROR: sbcast {tarball.name} failed";'
                f" exit 1; fi"
            )
            lines.append(
                f"srun -N ${{SLURM_NNODES}}"
                f" --ntasks-per-node 1"
                f" mkdir -p {nvme_path}"
            )
            lines.append(
                f"srun -N ${{SLURM_NNODES}}"
                f" --ntasks-per-node 1"
                f" tar -xzf /mnt/bb/${{USER}}/{env_name}.tar.gz"
                f" -C {nvme_path}"
            )
            # Fix VIRTUAL_ENV in activate script
            lines.append(
                f'srun -N ${{SLURM_NNODES}}'
                f' --ntasks-per-node 1'
                f' sed -i'
                f' "s|VIRTUAL_ENV=.*|VIRTUAL_ENV=\\"{nvme_path}\\"|"'
                f' {nvme_path}/bin/activate'
            )
        lines.append('echo "Environments ready"')
        lines.append("")
        env_section = "\n".join(lines)

        # Rewrite srun commands to use NVMe venv paths
        rewritten = []
        for srun_line in srun_lines:
            for orig, nvme in nvme_venv_map.items():
                srun_line = srun_line.replace(orig, nvme)
            rewritten.append(srun_line)
        srun_lines = rewritten
        srun_block = "\n".join(srun_lines)
    elif conda_archive:
        env_section = f"""\
# --- SBCAST conda environment to NVMe ---
ENV_ARCHIVE={conda_archive}
ENV_NAME=sweep_conda_env
NVME_ENV_PATH=/mnt/bb/${{USER}}/${{ENV_NAME}}

echo "Broadcasting conda env to ${{SLURM_NNODES}} nodes..."
sbcast -pf $ENV_ARCHIVE /mnt/bb/${{USER}}/${{ENV_NAME}}.tar.gz
if [ "$?" != "0" ]; then
    echo "ERROR: sbcast failed"
    exit 1
fi

srun -N ${{SLURM_NNODES}} --ntasks-per-node 1 \\
    mkdir -p $NVME_ENV_PATH
srun -N ${{SLURM_NNODES}} --ntasks-per-node 1 \\
    tar -xzf /mnt/bb/${{USER}}/${{ENV_NAME}}.tar.gz \\
    -C $NVME_ENV_PATH

source ${{NVME_ENV_PATH}}/bin/activate

echo "Environment ready"
echo ""
"""

    dependency_line = ""
    if dependency_job_id:
        dependency_line = f"#SBATCH --dependency=afterok:{dependency_job_id}\n"

    # User-declared environment (_env_exports).  Emitted AFTER the default
    # thread-count exports so user values win.  Values are NOT expanded
    # here: bash expands $VARS at runtime on the compute node (needed for
    # e.g. LD_LIBRARY_PATH appends and $SLURM_CPUS_PER_TASK).
    env_exports_block = ""
    env_exports = _get_env_exports(global_params)
    if env_exports:
        lines = ["# --- User environment (_env_exports) ---"]
        for name, value in env_exports.items():
            lines.append(f'export {name}="{value}"')
        lines.append("")
        env_exports_block = "\n".join(lines)

    script = f"""\
#!/bin/bash
#SBATCH -J q8020_sweep
#SBATCH -A {project}
#SBATCH -t {walltime}
#SBATCH -N {n_nodes}
#SBATCH -C nvme
#SBATCH -p {partition}
#SBATCH -o {run_dir}/slurm_%j.out
#SBATCH -e {run_dir}/slurm_%j.err
{dependency_line}
echo "Job ID: $SLURM_JOB_ID"
echo "Nodes:  $SLURM_JOB_NUM_NODES"
echo "Cases:  {n_cases}"
echo "Start:  $(date)"
echo ""

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
{env_exports_block}
cd $SLURM_SUBMIT_DIR || exit 1

{env_section}
# --- Launch all cases in parallel ---
echo "Launching {n_cases} cases..."
START_TIME=$(date +%s)

{srun_block}

# Wait for all background srun tasks
wait

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
echo ""
echo "All cases finished."
echo "Wall time: $((ELAPSED / 60))m $((ELAPSED % 60))s"
"""
    return script


def _collect_parallel_cases(
    expanded_cases: dict[str, dict[str, Any]],
    run_dir: Path,
    workflow_id: str,
    group_executable: str,
    script_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Prepare cases for parallel launch (local or SLURM).

    Returns (results_dict, cases_info_list). The caller
    accumulates cases_info across groups then launches
    everything at once.
    """
    results: dict[str, Any] = {}
    cases_info: list[dict[str, Any]] = []

    for case_id, params in expanded_cases.items():
        info = _build_case_launch_info(
            case_id, params, run_dir, workflow_id,
            group_executable, script_dir,
        )
        cases_info.append(info)
        results[info["experiment_id"]] = {
            "case_id": case_id,
            "experiment_id": info["experiment_id"],
            "output_dir": str(info["case_dir"]),
            "status": "pending",
        }
        print(f"  {DIM}Prepared:{RESET} {case_id}"
              f" ({info['experiment_id']})")

    return results, cases_info


def _parse_slurm_job_id(stdout: str) -> str | None:
    """Extract job ID from sbatch stdout (e.g. 'Submitted batch job 12345')."""
    match = re.search(r"Submitted batch job (\d+)", stdout)
    if match:
        return match.group(1)
    # Fallback: last numeric token
    for word in reversed(stdout.strip().split()):
        if word.isdigit():
            return word
    return None


def _poll_slurm_job(
    job_id: str,
    all_results: dict[str, Any],
    run_dir: Path,
    poll_wait: float = 5,
) -> None:
    """Poll a submitted Slurm job briefly to catch instant failures.

    Waits up to ``poll_wait`` seconds, checking ``sacct`` each second.
    If the job finishes within that window, updates all_results with
    the real outcome (success/failed) instead of leaving them as
    'submitted'. Also reads pipeline_result.json from each case dir
    if available.

    If the job is still running after ``poll_wait``, leaves status as
    'submitted' and returns.
    """
    terminal_states = {
        "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT",
        "NODE_FAIL", "PREEMPTED", "OUT_OF_MEMORY",
    }

    for _ in range(int(poll_wait)):
        time.sleep(1)
        try:
            result = subprocess.run(
                ["sacct", "-j", job_id, "--format=State", "-n",
                 "--parsable2", "--noheader"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            return

        states = {
            s.strip() for s in result.stdout.strip().split("\n")
            if s.strip()
        }
        if not states:
            continue

        # If any state is still non-terminal, job is running
        if not states.issubset(terminal_states):
            continue

        # Job finished — determine outcome
        job_failed = "FAILED" in states
        job_status = "slurm_failed" if job_failed else "slurm_completed"

        # Try to read per-case results from pipeline_result.json
        case_results_found = 0
        for _, info in all_results.items():
            case_dir = Path(info.get("output_dir", ""))
            result_file = case_dir / "pipeline_result.json"
            if result_file.exists():
                with open(result_file, encoding="utf-8") as f:
                    case_result = _json.load(f)
                info.update(case_result)
                case_results_found += 1
            else:
                info["status"] = job_status

        if job_failed:
            # Read slurm stderr for diagnostics
            err_files = list(run_dir.glob("slurm_*.err"))
            err_msg = ""
            for ef in err_files:
                content = ef.read_text(encoding="utf-8").strip()
                if content:
                    err_msg = content[:500]
                    break

            if err_msg:
                print(
                    f"\n{RED}{BOLD}Job {job_id} failed:{RESET}"
                )
                for line in err_msg.split("\n")[:10]:
                    print(f"  {DIM}{line}{RESET}")
            else:
                print(
                    f"\n{RED}{BOLD}Job {job_id} failed{RESET}"
                    f" (check compute node logs)"
                )
        else:
            ok = sum(
                1 for r in all_results.values()
                if r.get("status") == "success"
            )
            total = len(all_results)
            print(
                f"\n{GREEN}{BOLD}Job {job_id} completed:{RESET}"
                f" {ok}/{total} cases succeeded"
            )
        return

    # Job still running after poll_wait — leave as submitted
    print(
        f"  {DIM}Job {job_id} still running"
        f" (check with: sacct -j {job_id}){RESET}"
    )


def _finalize_slurm(
    all_cases_info: list[dict[str, Any]],
    all_results: dict[str, Any],
    global_params: dict[str, Any],
    run_dir: Path,
    dry_run: bool = False,
    dependency_job_id: str | None = None,
) -> str | None:
    """Generate and submit one sbatch script for all collected cases,
    mutating *all_results* in place to update status.  *dependency_job_id*
    adds ``--dependency=afterok:<id>``.  Returns the SLURM job ID, or None.
    """
    # Auto-pack venvs for NVMe broadcast if requested
    packed_venvs = None
    pack_venvs_list = global_params.get("_slurm_pack_venvs", [])
    if isinstance(pack_venvs_list, str):
        pack_venvs_list = [pack_venvs_list]
    if pack_venvs_list:
        run_dir.mkdir(parents=True, exist_ok=True)
        packed_venvs = _pack_venvs(
            pack_venvs_list, run_dir, dry_run,
        )

        # Build mapping of venv path -> NVMe path.
        # Include both raw (as in template-expanded cmd) and
        # resolved forms so string replacement catches both.
        user = os.environ.get("USER", "unknown")
        nvme_map: dict[str, str] = {}
        for i, raw_path in enumerate(pack_venvs_list):
            nvme = f"/mnt/bb/{user}/venv_{i}"
            resolved = str(
                Path(raw_path).expanduser().resolve()
            )
            nvme_map[resolved] = nvme
            # Also map the raw form (e.g. "./fvm_euler_1d_solver/.venv")
            if raw_path != resolved:
                nvme_map[raw_path] = nvme

        # Sort longest-first to avoid partial matches
        # (e.g. "./.venv" inside "./solver/.venv")
        nvme_replacements = sorted(
            nvme_map.items(), key=lambda kv: len(kv[0]),
            reverse=True,
        )

        # Rewrite cmd and postproc in each pipeline_args.json
        for info in all_cases_info:
            args_file = Path(info["args_file"])
            if not args_file.exists():
                continue
            with open(args_file, encoding="utf-8") as f:
                pargs = json.load(f)
            changed = False
            # Rewrite cmd list entries
            new_cmd = []
            for part in pargs.get("cmd", []):
                for orig, nvme in nvme_replacements:
                    part = part.replace(orig, nvme)
                new_cmd.append(part)
            if new_cmd != pargs.get("cmd"):
                pargs["cmd"] = new_cmd
                changed = True
            # Rewrite case_postproc entries
            new_pp = []
            for pp in pargs.get("case_postproc", []):
                for orig, nvme in nvme_replacements:
                    pp = pp.replace(orig, nvme)
                new_pp.append(pp)
            if new_pp != pargs.get("case_postproc"):
                pargs["case_postproc"] = new_pp
                changed = True
            if changed:
                with open(
                    args_file, "w", encoding="utf-8"
                ) as f:
                    json.dump(pargs, f, indent=2)

    sbatch_content = _generate_sbatch_script(
        all_cases_info, global_params, run_dir,
        dependency_job_id=dependency_job_id,
        packed_venvs=packed_venvs,
    )
    sbatch_file = run_dir / "q8020_sweep.sbatch"
    run_dir.mkdir(parents=True, exist_ok=True)

    with open(sbatch_file, "w", encoding="utf-8") as f:
        f.write(sbatch_content)

    print(f"{CYAN}Generated:{RESET} {sbatch_file}")
    print(
        f"{DIM}{len(all_cases_info)} cases"
        f" in one job{RESET}"
    )

    if dry_run:
        for eid in all_results:
            all_results[eid]["status"] = "dry_run"
        return None

    try:
        sub = subprocess.run(
            ["sbatch", str(sbatch_file)],
            capture_output=True, text=True,
            check=True,
        )
        job_line = sub.stdout.strip()
        print(
            f"{GREEN}{BOLD}Submitted:{RESET} {job_line}"
        )
        job_id = _parse_slurm_job_id(job_line)
        for eid in all_results:
            all_results[eid]["status"] = "submitted"
            all_results[eid]["slurm_output"] = job_line

        # Brief poll to catch jobs that fail instantly
        if job_id:
            poll_wait = float(
                global_params.get("_slurm_poll_wait", 5)
            )
            _poll_slurm_job(
                job_id, all_results, run_dir,
                poll_wait,
            )

        return job_id
    except subprocess.CalledProcessError as e:
        sbatch_stderr = (e.stderr or "").strip()
        print(
            f"{RED}{BOLD}sbatch failed:{RESET} {e}",
            file=sys.stderr,
        )
        if sbatch_stderr:
            print(sbatch_stderr, file=sys.stderr)
        # Write log file for post-mortem diagnosis
        error_log = run_dir / "slurm_submit_error.log"
        with open(error_log, "w", encoding="utf-8") as f:
            f.write(f"sbatch failed with exit code {e.returncode}\n\n")
            f.write(sbatch_stderr or "(no stderr captured)")
        for eid in all_results:
            all_results[eid]["status"] = "submit_error"
            all_results[eid]["error"] = str(e)
            if sbatch_stderr:
                all_results[eid]["sbatch_stderr"] = sbatch_stderr
        return None
    except Exception as e:
        print(
            f"{RED}{BOLD}sbatch failed:{RESET} {e}",
            file=sys.stderr,
        )
        for eid in all_results:
            all_results[eid]["status"] = "submit_error"
            all_results[eid]["error"] = str(e)
        return None


# ---------------------------------------------------------------------------
# Dependent SLURM post-processing job (group + final aggregation)
# ---------------------------------------------------------------------------

# Dependency types accepted for the post-proc job.  ``afterok`` runs the job
# only if the compute job succeeded; ``afterany`` runs it regardless (useful
# for aggregating partial/failed results).
_VALID_POSTPROC_DEP_TYPES = {"afterok", "afterany"}


def _generate_postproc_sbatch(
    run_dir: Path,
    global_params: dict[str, Any],
    dependency_job_id: str,
    dep_type: str = "afterok",
) -> str:
    """Generate a single-node sbatch that runs group + final post-proc.

    The job is held in the queue via ``--dependency=<dep_type>:<id>`` until the
    compute job finishes, then runs :mod:`q8020_cfd_metautil.sweep_postproc`
    directly on the batch node (no ``srun`` fan-out -- this is a lightweight
    serial aggregation step).

    Unlike the compute job, this script does NOT replay ``_slurm_pack_venvs``
    NVMe broadcasting: post-proc commands are expected to reference a
    shared-filesystem interpreter (e.g. ``${_venv}/bin/python``), which is
    reachable from the batch node without staging.

    Returns the sbatch script content as a string.
    """
    if dep_type not in _VALID_POSTPROC_DEP_TYPES:
        raise ValueError(
            f"_slurm_postproc_dependency_type must be one of "
            f"{sorted(_VALID_POSTPROC_DEP_TYPES)}, got {dep_type!r}"
        )

    project = global_params.get("_slurm_project", "<PROJECT_ID>")
    walltime = global_params.get("_slurm_postproc_walltime", "00:30:00")
    partition = global_params.get("_slurm_partition", "batch")
    conda_archive = global_params.get("_slurm_conda_archive", "")

    # Shared-FS environment bootstrap (mirrors the compute job's conda path,
    # but never the packed-venv NVMe staging).
    env_section = ""
    if conda_archive:
        env_section = f"""\
# --- SBCAST conda environment to NVMe ---
ENV_ARCHIVE={conda_archive}
ENV_NAME=sweep_conda_env
NVME_ENV_PATH=/mnt/bb/${{USER}}/${{ENV_NAME}}

echo "Broadcasting conda env to ${{SLURM_NNODES}} nodes..."
sbcast -pf $ENV_ARCHIVE /mnt/bb/${{USER}}/${{ENV_NAME}}.tar.gz
if [ "$?" != "0" ]; then
    echo "ERROR: sbcast failed"
    exit 1
fi

srun -N ${{SLURM_NNODES}} --ntasks-per-node 1 \\
    mkdir -p $NVME_ENV_PATH
srun -N ${{SLURM_NNODES}} --ntasks-per-node 1 \\
    tar -xzf /mnt/bb/${{USER}}/${{ENV_NAME}}.tar.gz \\
    -C $NVME_ENV_PATH

source ${{NVME_ENV_PATH}}/bin/activate

echo "Environment ready"
echo ""
"""

    script = f"""\
#!/bin/bash
#SBATCH -J q8020_postproc
#SBATCH -A {project}
#SBATCH -t {walltime}
#SBATCH -N 1
#SBATCH -C nvme
#SBATCH -p {partition}
#SBATCH -o {run_dir}/slurm_postproc_%j.out
#SBATCH -e {run_dir}/slurm_postproc_%j.err
#SBATCH --dependency={dep_type}:{dependency_job_id}

echo "Job ID:     $SLURM_JOB_ID"
echo "Depends on: {dependency_job_id} ({dep_type})"
echo "Start:      $(date)"
echo ""

cd $SLURM_SUBMIT_DIR || exit 1

{env_section}
# --- Run group + final post-processing (serial, on the batch node) ---
python3 -m q8020_cfd_metautil.sweep_postproc {run_dir}
STATUS=$?

echo ""
echo "Post-processing finished with status $STATUS."
exit $STATUS
"""
    return script


def _finalize_slurm_postproc(
    run_dir: Path,
    global_params: dict[str, Any],
    compute_job_id: str,
    dep_type: str = "afterok",
    dry_run: bool = False,
) -> str | None:
    """Submit the dependent post-proc job for a SLURM sweep, waiting on
    *compute_job_id* (*dep_type* ``afterok``/``afterany``).  Returns the
    post-proc job ID, or None on dry-run/failure.
    """
    sbatch_content = _generate_postproc_sbatch(
        run_dir, global_params, compute_job_id, dep_type,
    )
    sbatch_file = run_dir / "q8020_postproc.sbatch"
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(sbatch_file, "w", encoding="utf-8") as f:
        f.write(sbatch_content)

    print(f"{CYAN}Generated:{RESET} {sbatch_file}")
    print(
        f"{DIM}post-proc job depends on {compute_job_id}"
        f" ({dep_type}){RESET}"
    )

    if dry_run:
        return None

    try:
        sub = subprocess.run(
            ["sbatch", str(sbatch_file)],
            capture_output=True, text=True,
            check=True,
        )
        job_line = sub.stdout.strip()
        print(f"{GREEN}{BOLD}Submitted postproc:{RESET} {job_line}")
        return _parse_slurm_job_id(job_line)
    except subprocess.CalledProcessError as e:
        sbatch_stderr = (e.stderr or "").strip()
        print(
            f"{RED}{BOLD}postproc sbatch failed:{RESET} {e}",
            file=sys.stderr,
        )
        if sbatch_stderr:
            print(sbatch_stderr, file=sys.stderr)
        error_log = run_dir / "slurm_postproc_submit_error.log"
        with open(error_log, "w", encoding="utf-8") as f:
            f.write(f"sbatch failed with exit code {e.returncode}\n\n")
            f.write(sbatch_stderr or "(no stderr captured)")
        return None
    except Exception as e:
        print(
            f"{RED}{BOLD}postproc sbatch failed:{RESET} {e}",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# Sweep configuration and execution
# ---------------------------------------------------------------------------

def expand_case_lists(case_id: str, case_params: dict) -> list:
    """Expand list-valued params into ``(case_id_N, params)`` subcases (one
    per combination).  E.g. ``shots=[100,1000]`` -> two cases.  ``_trials>1``
    replicates each case, nested via ``_parent_case_id``/``_trial_index``
    (underscore keys, never passed to the solver).
    """
    # Extract _trials before processing (meta-key, not a solver param)
    trials = int(case_params.pop("_trials", 1))

    list_params = {}
    scalar_params = {}

    for key, value in case_params.items():
        if isinstance(value, list):
            list_params[key] = value
        else:
            scalar_params[key] = value

    # If no lists, return the original case
    if not list_params:
        expanded_cases = [(case_id, case_params.copy())]
    else:
        # Generate all combinations of list values
        param_names = list(list_params.keys())
        param_values = [list_params[name] for name in param_names]

        expanded_cases = []
        for i, combination in enumerate(product(*param_values)):
            expanded_id = f"{case_id}_{i}"
            expanded_params = scalar_params.copy()
            for param_name, param_value in zip(param_names, combination):
                expanded_params[param_name] = param_value
            expanded_cases.append((expanded_id, expanded_params))

    # Replicate each case _trials times
    if trials > 1:
        replicated = []
        for eid, eparams in expanded_cases:
            for t in range(trials):
                trial_id = f"{eid}_trial_{t}"
                trial_params = eparams.copy()
                trial_params["_trial_index"] = t
                trial_params["_parent_case_id"] = eid
                replicated.append((trial_id, trial_params))
        expanded_cases = replicated

    return expanded_cases


def load_sweep_config(toml_path: str) -> dict:
    """Load a sweep TOML into ``{'global', 'groups'}`` (or ``'stages'`` for
    multi-stage).  Each non-global section is a group; list-valued params
    are expanded per group.  Underscore meta-keys inherit global -> group,
    group overriding.  See the README for the full TOML format.
    """
    path = Path(toml_path)
    if not path.is_file():
        raise FileNotFoundError(f"TOML file not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # Support both "global" and "_global" as section names
    global_params = data.get("global", data.get("_global", {}))

    # Check for multi-stage configuration ([stage.<name>] sections)
    stages = _parse_stages(data, global_params)
    if stages is not None:
        return {"global": global_params, "groups": {}, "stages": stages}

    # Single-stage: every non-global section is a group
    groups = {}  # original_case_id -> {params, expanded_cases}

    for key in data:
        if key in ("global", "_global", "stage"):
            continue

        # Expand this case
        expanded = expand_case_lists(key, data[key])

        # Merge global params with case params for each expanded case
        expanded_cases = {}
        for expanded_id, expanded_params in expanded:
            merged = global_params.copy()
            merged.update(expanded_params)
            expanded_cases[expanded_id] = merged

        # Store the group with its merged params (for postproc) and expanded cases
        group_params = global_params.copy()
        group_params.update(data[key])
        groups[key] = {
            "params": group_params,
            "expanded_cases": expanded_cases
        }

    return {"global": global_params, "groups": groups}


def build_command_args(
    params: dict, arg_mapping: dict | None = None
) -> list:
    """Convert a param dict to CLI args.  Names map via *arg_mapping* if
    given, else become ``--key-name`` (underscores to hyphens); keys already
    starting with "-" are used as-is.  Bools are flags; lists expand to
    repeated values; ``_``-prefixed keys are skipped.
    """
    args = []
    skip_keys = {"executable", "_output_dir", "_case_preproc", "_case_postproc", "_group_postproc", "_final_postproc", "_inject_outdir", "_inject_experiment_id", "_inject_workflow_id", "_env"}
    
    for key, value in params.items():
        if key in skip_keys or key.startswith("_"):
            continue
        
        # Determine CLI arg name
        if arg_mapping and key in arg_mapping:
            arg_name = arg_mapping[key]
        elif key.startswith("-"):
            # Key already has dash prefix, use as-is
            arg_name = key
        else:
            # Default: double dash, convert underscores to hyphens: --key-name
            cli_key = key.replace('_', '-')
            arg_name = f"--{cli_key}"
        
        # Handle different value types
        if isinstance(value, bool):
            if value:
                args.append(arg_name)
        elif isinstance(value, list):
            # Expand list into multiple arguments
            args.append(arg_name)
            for item in value:
                args.append(str(item))
        elif value is not None:
            args.append(arg_name)
            args.append(str(value))
    
    return args


def run_postproc(
    postproc_list: list,
    postproc_json: Path,
    script_dir: Path | None = None,
    dry_run: bool = False,
    case_dir: Path | None = None,
    proc_type: str = "postproc",
    experiment_id: str | None = None,
) -> list:
    """Run each pre/postproc command in *postproc_list*, passing
    *postproc_json* as the single argument.  *script_dir* is added to
    PYTHONPATH.  Returns a result per command.
    """
    results = []
    
    # Set up environment with PYTHONPATH
    env = None
    if script_dir:
        env = os.environ.copy()
        existing_path = env.get('PYTHONPATH', '')
        if existing_path:
            env['PYTHONPATH'] = f"{script_dir}:{existing_path}"
        else:
            env['PYTHONPATH'] = str(script_dir)
    
    for postproc_cmd in postproc_list:
        # Check if command contains shell operators (needs bash -c)
        if "&&" in postproc_cmd or "||" in postproc_cmd or "|" in postproc_cmd or "$(" in postproc_cmd or "`" in postproc_cmd:
            # Shell command - wrap with bash -c, append JSON path to the command string
            cmd = ["bash", "-c", f"{postproc_cmd} {postproc_json}"]
        else:
            # Simple command - split and append JSON path as argument
            cmd = postproc_cmd.split() + [str(postproc_json)]
        
        # Capitalize proc_type for display
        proc_label = proc_type.capitalize()
        
        if dry_run:
            if cmd[0] == "bash" and cmd[1] == "-c":
                print(f"  {proc_label} (dry-run): bash -c \"{cmd[2]}\"")
            else:
                print(f"  {proc_label} (dry-run): {' '.join(cmd)}")
            results.append({"command": cmd, "status": "dry_run"})
            continue
        
        print(f"  {YELLOW}Running {proc_type}:{RESET} {DIM}{postproc_cmd}{RESET}")
        try:
            result = subprocess_tee.run(
                cmd,
                timeout=3600,
                check=False,
                env=env,
            )
            if result.returncode == 0:
                print(f"    {GREEN}{BOLD}✓ {proc_label} completed{RESET}")
            else:
                print(f"    {RED}{BOLD}✗ {proc_label} error (code {result.returncode}){RESET}")
            
            # Write stdout/stderr to files if case_dir provided
            if case_dir and experiment_id:
                if result.stdout:
                    stdout_file = case_dir / f"q8020_{proc_type}_stdout_{experiment_id}.txt"
                    with open(stdout_file, "w", encoding="utf-8") as f:
                        f.write(result.stdout)
                if result.stderr:
                    stderr_file = case_dir / f"q8020_{proc_type}_stderr_{experiment_id}.txt"
                    with open(stderr_file, "w", encoding="utf-8") as f:
                        f.write(result.stderr)
            
            results.append({
                "command": cmd,
                "status": "success" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "stdout": result.stdout[:500] if result.stdout else "",
                "stderr": result.stderr[:500] if result.stderr else ""
            })
        except Exception as e:
            print(f"    {RED}{BOLD}✗ {proc_label} exception:{RESET} {e}")
            results.append({"command": cmd, "status": "exception", "error": str(e)})
    
    return results


def run_sweep(
    toml_path: str,
    script: str | None,
    arg_mapping: dict | None = None,
    dry_run: bool = False,
    group_filter: list | None = None,
    overrides: dict[str, str] | None = None,
    *,
    _preloaded_config: dict | None = None,
    _dependency_job_id: str | None = None,
    _run_dir_override: Path | None = None,
    _workflow_id_override: str | None = None,
) -> dict:
    """Run a parameter sweep from a TOML config.  *overrides* are CLI
    ``--set`` values applied to [global].  The ``_``-prefixed keyword args
    are internal hooks used by the multi-stage orchestrator (preloaded
    config, SLURM dependency, run-dir/workflow-id overrides).
    """
    config = _preloaded_config or load_sweep_config(toml_path)
    global_params = config["global"]

    # Apply CLI overrides to global params.  Track the *resolved* keys
    # (after dashed-variant matching) so the re-merge below can correctly
    # force them into case params even when the override was given bare
    # (e.g. --set target=hardware -> resolved key "--target").
    resolved_override_keys: set[str] = set()
    if overrides:
        for key, value in overrides.items():
            # Resolve the target key: if a bare key like "job-id" is
            # given but the TOML already has "--job-id" or "-job-id",
            # override the existing dashed variant instead of creating
            # a duplicate.
            target_key = key
            if not key.startswith("-"):
                for prefix in ("--", "-"):
                    candidate = prefix + key
                    if candidate in global_params:
                        target_key = candidate
                        break
            resolved_override_keys.add(target_key)

            # Coerce "true"/"false" to bool
            if value.lower() == "true":
                global_params[target_key] = True
            elif value.lower() == "false":
                global_params[target_key] = False
            else:
                # Try int, then float, then keep as string
                try:
                    global_params[target_key] = int(value)
                except ValueError:
                    try:
                        global_params[target_key] = float(value)
                    except ValueError:
                        global_params[target_key] = value

    # Expand ${VAR} templates throughout the config using global params.
    # Fixpoint expansion: values may reference other ${VAR}s that themselves
    # contain templates, so iterate until stable (cycle-guarded).
    global_params.update(
        _expand_templates_fixpoint(global_params, global_params)
    )
    config["groups"] = _expand_templates_fixpoint(
        config["groups"], global_params
    )

    # Re-merge global params into expanded cases so --set overrides
    # propagate into case_params / pipeline_args.json.
    # For _-prefixed meta keys, global (with overrides) wins over
    # stale copies made during load_sweep_config.  For solver params
    # (no _ prefix), case-specific values still win — unless the
    # global key came from --set and matches a dashed variant
    # (e.g. --set "job-id=X" should override "--job-id" in the case).
    # Use the keys as resolved during the apply step (dashed-variant
    # aware), so a bare --set on a dashed solver param still forces its
    # value into the case.
    override_keys = resolved_override_keys

    for group_data in config["groups"].values():
        merged_gp = {**group_data["params"]}
        for k, v in global_params.items():
            # A global key wins over a case param when it is absent from
            # the case OR an explicit --set override (which must beat the
            # stale case copy duplicated at load time).  Group-authored
            # values — including _-prefixed meta-keys like a per-group
            # _script — are preserved: group-level keys override globals.
            if k not in merged_gp or k in override_keys:
                # If this is an override key given bare, also remove any
                # dashed variant so we don't get duplicates on the CLI.
                if k in override_keys and not k.startswith("-"):
                    for pfx in ("--", "-"):
                        merged_gp.pop(pfx + k, None)
                merged_gp[k] = v
        group_data["params"] = merged_gp

        for cid, cparams in group_data["expanded_cases"].items():
            merged_cp = {**cparams}
            for k, v in global_params.items():
                if k not in merged_cp or k in override_keys:
                    if k in override_keys and not k.startswith("-"):
                        for pfx in ("--", "-"):
                            merged_cp.pop(pfx + k, None)
                    merged_cp[k] = v
            group_data["expanded_cases"][cid] = merged_cp

    groups = config["groups"]
    
    # Filter groups if group_filter is specified
    if group_filter:
        missing = [g for g in group_filter if g not in groups]
        if missing:
            raise ValueError(f"Group(s) not found: {missing}. Available groups: {list(groups.keys())}")
        groups = {g: groups[g] for g in group_filter}
    else:
        # When no filter specified, skip groups starting with _ (they must be explicitly named)
        groups = {g: groups[g] for g in groups if not g.startswith("_")}
    
    # Build executable command: python <script>
    # May be None if each group specifies its own _script
    executable = f"python {script}" if script else None
    
    # Get script directory for PYTHONPATH (for module imports in postproc)
    # Use expanded global _script first, then fall back to group _script
    resolved_script = global_params.get("_script", script)
    script_dir = _extract_script_dir(resolved_script)
    if script_dir is None:
        for group_data in groups.values():
            group_script = group_data["params"].get("_script")
            if group_script:
                script_dir = _extract_script_dir(group_script)
                if script_dir is not None:
                    break
    if script_dir is None:
        script_dir = Path(".").absolute()
    
    # Expand ~ to user home directory (works on Unix, Mac, Windows)
    output_dir_str = global_params.get("_output_dir", "./sweep_results")
    # Use .absolute() instead of .resolve() so symlink paths (e.g.
    # /ccs/home/... on OLCF Frontier) are preserved.  .resolve() follows
    # symlinks to /autofs/... which may not be visible on compute nodes.
    output_dir = Path(output_dir_str).expanduser().absolute()

    # Create date directory (yyyy-mm-dd) under output_dir
    date_str = datetime.now().strftime("%Y-%m-%d")
    date_dir = output_dir / date_str

    # Create output_dir and date_dir if they don't exist
    if not dry_run:
        date_dir.mkdir(parents=True, exist_ok=True)

    # Create run subdirectory with workflow ID under date dir
    if _run_dir_override is not None:
        workflow_id = _workflow_id_override or generate_workflow_id()
        run_dir = _run_dir_override
    else:
        workflow_id = generate_workflow_id()
        run_dir = date_dir / workflow_id
    
    # Count total cases across all groups
    total_cases = sum(len(g["expanded_cases"]) for g in groups.values())
    
    # Record sweep start time
    sweep_start_time = _get_iso_timestamp()

    if not dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
        # Copy input TOML to run directory with q8020_ prefix
        toml_name = Path(toml_path).name
        shutil.copy2(toml_path, run_dir / f"q8020_{toml_name}")
        # Write expanded cases to JSON before running
        expanded_cases_file = run_dir / "q8020_expanded_cases.json"
        all_cases = {}
        for group in groups.values():
            all_cases.update(group["expanded_cases"])
        with open(expanded_cases_file, "w", encoding="utf-8") as f:
            json.dump({"cases": all_cases, "global": global_params}, f, indent=2)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Capture library versions once for the entire sweep
    library_versions = make_library_meta()

    results: dict[str, Any] = {
        "config_file": str(toml_path),
        "output_dir": str(output_dir),
        "workflow_id": workflow_id,
        "run_dir": str(run_dir),
        "timestamp": timestamp,
        "start_time": sweep_start_time,
        "sweeper_library_versions": library_versions,
        "groups": {},
        "cases": {},
    }
    
    all_case_dirs: list[Path] = []

    run_mode = global_params.get("_run_mode", "sequential")
    use_slurm = bool(global_params.get("_slurm", False))

    mode_label = run_mode
    if run_mode == "parallel" and use_slurm:
        if global_params.get("_slurm_interactive"):
            mode_label = "parallel (slurm interactive)"
        else:
            mode_label = "parallel (slurm)"

    print(
        f"{CYAN}{BOLD}Running sweep:{RESET}"
        f" {total_cases} cases in {len(groups)} groups"
        f" [{mode_label}]"
    )
    print(f"{CYAN}{BOLD}Output directory:{RESET} {run_dir}")
    print()

    # For parallel modes, accumulate cases across groups
    parallel_all_cases_info: list[dict[str, Any]] = []
    parallel_all_results: dict[str, Any] = {}

    # Process each group
    for group_id, group_data in groups.items():
        group_params = group_data["params"]
        expanded_cases = group_data["expanded_cases"]

        # Check for per-group _script override
        group_script = group_params.get("_script")
        if group_script:
            group_executable = group_script
        else:
            group_executable = executable
        if not group_executable:
            raise ValueError(
                f"Group '{group_id}' has no executable: set a global "
                "_script or a per-group _script."
            )

        # Synthesize the multi-rank srun launcher for Slurm batch cases so
        # _script can stay a bare `python solver.py` across platforms.
        # Injected before the _env activation prepend: the composed command
        # is `source .../activate && srun ... python solver.py`.
        if use_slurm and run_mode == "parallel":
            launcher_prefix = _synthesize_launcher_prefix(group_params)
            if launcher_prefix:
                launcher_mode = str(
                    group_params.get("_slurm_launcher", "auto")
                ).lower()
                if (
                    launcher_mode == "auto"
                    and _script_has_launcher(group_executable)
                ):
                    print(
                        f"  {DIM}_script already carries a launcher; "
                        f"not synthesizing srun for group "
                        f"'{group_id}'{RESET}"
                    )
                else:
                    group_executable = (
                        f"{launcher_prefix} {group_executable}"
                    )

        # If _env is set, prepend venv activation
        env_path = group_params.get("_env")
        if env_path and group_executable:
            env_path_resolved = (
                Path(env_path).expanduser().absolute()
            )
            activate_cmd = (
                f"source {env_path_resolved}/bin/activate"
            )
            if activate_cmd not in group_executable:
                group_executable = (
                    f"{activate_cmd} && {group_executable}"
                )

        n_cases = len(expanded_cases)
        print(
            f"{CYAN}{BOLD}=== Group: {group_id}"
            f" ({n_cases} cases) ==={RESET}"
        )

        # ----- parallel path (collect all, launch after loop) ---
        if run_mode == "parallel":
            group_results, group_cases_info = (
                _collect_parallel_cases(
                    expanded_cases, run_dir, workflow_id,
                    group_executable, script_dir,
                )
            )
            parallel_all_cases_info.extend(group_cases_info)
            parallel_all_results.update(group_results)
            results["cases"].update(group_results)

            # Collect case_dirs for group/final postproc
            for eid, cr in group_results.items():
                odir = cr.get("output_dir")
                if odir:
                    all_case_dirs.append(Path(odir))

            # Group postproc: print command only, don't run
            group_postproc = group_params.get(
                "_group_postproc", []
            )
            if group_postproc:
                if isinstance(group_postproc, str):
                    group_postproc = [group_postproc]
                case_dirs_list = [
                    cr.get("output_dir", "")
                    for cr in group_results.values()
                ]
                postproc_data = {
                    "group_id": group_id,
                    "workflow_id": workflow_id,
                    "run_dir": str(run_dir),
                    "case_dirs": case_dirs_list,
                    "params": group_params,
                    "script_dir": str(script_dir),
                }
                postproc_json = (
                    run_dir
                    / f"_group_postproc_{group_id}.json"
                )
                if not dry_run:
                    with open(
                        postproc_json, "w", encoding="utf-8"
                    ) as f:
                        json.dump(postproc_data, f, indent=2)
                if use_slurm and global_params.get(
                    "_slurm_postproc_job", False
                ):
                    print(
                        f"\n  {YELLOW}Group postproc"
                        f" (dependent SLURM job):{RESET}"
                    )
                else:
                    print(
                        f"\n  {YELLOW}Group postproc"
                        f" (run manually):{RESET}"
                    )
                for pp_cmd in group_postproc:
                    print(
                        f"    {pp_cmd} {postproc_json}"
                    )

            print()
            continue

        # ----- sequential path (unchanged) -----
        group_case_dirs = []

        # Run each expanded case in the group
        for case_id, params in expanded_cases.items():
            print(f"  {YELLOW}Case:{RESET} {case_id}")

            # Generate experiment_id for this case
            experiment_id = generate_experiment_id()

            # Create case output directory (nest trials under parent)
            parent_case_id = params.get("_parent_case_id")
            if parent_case_id:
                trial_index = params.get("_trial_index", 0)
                case_dir = run_dir / parent_case_id / f"trial_{trial_index}_{experiment_id}"
            else:
                case_dir = run_dir / experiment_id
            if not dry_run:
                case_dir.mkdir(parents=True, exist_ok=True)
            group_case_dirs.append(case_dir)
            all_case_dirs.append(case_dir)

            # Build command args
            cmd_args = build_command_args(params, arg_mapping)

            # Inject outdir/experiment/workflow args
            inject_outdir = params.get("_inject_outdir")
            if inject_outdir:
                cmd_args.extend([inject_outdir, str(case_dir)])
            inject_exp = params.get("_inject_experiment_id")
            if inject_exp:
                cmd_args.extend([inject_exp, experiment_id])
            inject_wf = params.get("_inject_workflow_id")
            if inject_wf:
                cmd_args.extend([inject_wf, workflow_id])

            # Check if _script contains shell operators
            if ("&&" in group_executable
                    or "||" in group_executable
                    or "|" in group_executable):
                full_cmd = (
                    f"{group_executable}"
                    f" {' '.join(cmd_args)}"
                )
                cmd = ["bash", "-c", full_cmd]
            else:
                cmd_parts = group_executable.split()
                cmd_parts = [
                    str(Path(p).expanduser())
                    if p.startswith("~")
                    or "/" in p or "\\" in p
                    else p
                    for p in cmd_parts
                ]
                cmd = cmd_parts + cmd_args

            if dry_run:
                if cmd[0] == "bash" and cmd[1] == "-c":
                    print(
                        f"    Command: bash -c \"{cmd[2]}\""
                    )
                else:
                    print(
                        f"    Command: {' '.join(cmd)}"
                    )
                results["cases"][experiment_id] = {
                    "command": cmd, "status": "dry_run",
                }

                case_preproc = params.get(
                    "_case_preproc", []
                )
                if case_preproc:
                    if isinstance(case_preproc, str):
                        case_preproc = [case_preproc]
                    pp_json = (
                        case_dir
                        / f"q8020_case_preproc_"
                        f"{experiment_id}.json"
                    )
                    run_postproc(
                        case_preproc, pp_json,
                        script_dir, dry_run,
                    )

                case_postproc = params.get(
                    "_case_postproc", []
                )
                if case_postproc:
                    if isinstance(case_postproc, str):
                        case_postproc = [case_postproc]
                    pp_json = (
                        case_dir
                        / f"q8020_case_postproc_"
                        f"{experiment_id}.json"
                    )
                    run_postproc(
                        case_postproc, pp_json,
                        script_dir, dry_run,
                    )
                continue

            # Run per-case preproc if specified
            case_preproc = params.get("_case_preproc", [])
            t_preproc = 0.0
            if case_preproc:
                if isinstance(case_preproc, str):
                    case_preproc = [case_preproc]

                case_preproc_data = {
                    "case_id": case_id,
                    "experiment_id": experiment_id,
                    "workflow_id": workflow_id,
                    "case_dir": str(case_dir),
                    "params": params,
                }
                case_preproc_json = (
                    case_dir
                    / f"q8020_case_preproc_"
                    f"{experiment_id}.json"
                )
                if not dry_run:
                    with open(
                        case_preproc_json, "w",
                        encoding="utf-8",
                    ) as f:
                        json.dump(
                            case_preproc_data, f, indent=2
                        )

                t_pp_start = time.perf_counter()
                case_pre_results = run_postproc(
                    case_preproc, case_preproc_json,
                    script_dir, dry_run,
                    case_dir=case_dir, proc_type="preproc",
                    experiment_id=experiment_id,
                )
                t_preproc = (
                    time.perf_counter() - t_pp_start
                )
                if experiment_id not in results["cases"]:
                    results["cases"][experiment_id] = {}
                results["cases"][experiment_id][
                    "_case_preproc"
                ] = case_pre_results

            # Get _env path for env snapshotting
            env_path = params.get("_env")

            # Run the case via run_single()
            case_result = run_single(
                command=cmd,
                case_dir=case_dir,
                experiment_id=experiment_id,
                workflow_id=workflow_id,
                case_id=case_id,
                case_params=params,
                timeout=params.get("_case_timeout"),
                env_path=env_path,
                process_env=_resolve_env_exports(global_params),
            )

            results["cases"][experiment_id] = case_result

            if case_result["status"] == "success":
                print(
                    f"    {GREEN}{BOLD}"
                    f"done{RESET}"
                )
            elif case_result["status"] == "error":
                print(
                    f"    {RED}{BOLD}"
                    f"error (code "
                    f"{case_result.get('returncode')})"
                    f"{RESET}"
                )
            else:
                print(
                    f"    {RED}{BOLD}"
                    f"{case_result['status']}{RESET}"
                )

            # Run per-case postproc if specified
            case_postproc = params.get(
                "_case_postproc", []
            )
            t_postproc = 0.0
            if case_postproc:
                if isinstance(case_postproc, str):
                    case_postproc = [case_postproc]

                case_postproc_data = {
                    "case_id": case_id,
                    "experiment_id": experiment_id,
                    "workflow_id": workflow_id,
                    "case_dir": str(case_dir),
                    "params": params,
                }
                case_postproc_json = (
                    case_dir
                    / f"q8020_case_postproc_"
                    f"{experiment_id}.json"
                )
                if not dry_run:
                    with open(
                        case_postproc_json, "w",
                        encoding="utf-8",
                    ) as f:
                        json.dump(
                            case_postproc_data, f, indent=2
                        )

                t_pp_start = time.perf_counter()
                case_pp_results = run_postproc(
                    case_postproc, case_postproc_json,
                    script_dir, dry_run,
                    case_dir=case_dir, proc_type="postproc",
                    experiment_id=experiment_id,
                )
                t_postproc = (
                    time.perf_counter() - t_pp_start
                )
                results["cases"][experiment_id][
                    "_case_postproc"
                ] = case_pp_results

            # Harvest all fragments into metadata JSON
            t_harvest_start = time.perf_counter()
            if not dry_run:
                metadata_file = (
                    case_dir
                    / f"q8020_metadata_"
                    f"{experiment_id}.json"
                )
                unified_metadata, warnings, _ = (
                    harvest_metadata(case_dir)
                )
                for warning in warnings:
                    print(
                        f"    warning: {warning}",
                        file=sys.stderr,
                    )
                with open(
                    metadata_file, "w", encoding="utf-8"
                ) as f:
                    json.dump(
                        unified_metadata, f, indent=2
                    )
            t_harvest = (
                time.perf_counter() - t_harvest_start
            )

            # Merge overhead timing into case result
            overhead = case_result.get("overhead", {})
            overhead["preproc_seconds"] = round(
                t_preproc, 6
            )
            overhead["postproc_seconds"] = round(
                t_postproc, 6
            )
            overhead["harvest_seconds"] = round(
                t_harvest, 6
            )
            case_result["overhead"] = overhead
            results["cases"][experiment_id] = case_result

        # Run group postproc (sequential mode)
        group_postproc = group_params.get(
            "_group_postproc", []
        )
        if group_postproc:
            if isinstance(group_postproc, str):
                group_postproc = [group_postproc]

            postproc_data = {
                "group_id": group_id,
                "workflow_id": workflow_id,
                "run_dir": str(run_dir),
                "case_dirs": [
                    str(d) for d in group_case_dirs
                ],
                "params": group_params,
                "script_dir": str(script_dir),
            }
            postproc_json = (
                run_dir / f"_group_postproc_{group_id}.json"
            )
            if not dry_run:
                with open(
                    postproc_json, "w", encoding="utf-8"
                ) as f:
                    json.dump(postproc_data, f, indent=2)

            postproc_results = run_postproc(
                group_postproc, postproc_json,
                script_dir, dry_run,
            )
            results["groups"][group_id] = {
                "_group_postproc": postproc_results,
            }

        print()

    # Launch all parallel cases after groups are collected
    slurm_job_id = None
    if run_mode == "parallel" and parallel_all_cases_info:
        if use_slurm and global_params.get("_slurm_interactive"):
            _finalize_slurm_interactive(
                parallel_all_cases_info,
                parallel_all_results,
                global_params, dry_run,
            )
        elif use_slurm:
            slurm_job_id = _finalize_slurm(
                parallel_all_cases_info,
                parallel_all_results,
                global_params, run_dir, dry_run,
                dependency_job_id=_dependency_job_id,
            )
        else:
            _finalize_local_parallel(
                parallel_all_cases_info,
                parallel_all_results,
                global_params, dry_run,
            )
        # Update statuses in main results
        for eid, pr in parallel_all_results.items():
            if eid in results["cases"]:
                results["cases"][eid].update(pr)
        print()

    # Final postproc
    final_postproc = global_params.get(
        "_final_postproc", []
    )
    if final_postproc:
        if isinstance(final_postproc, str):
            final_postproc = [final_postproc]

        final_postproc_data = {
            "workflow_id": workflow_id,
            "run_dir": str(run_dir),
            "case_dirs": [str(d) for d in all_case_dirs],
            "groups": list(groups.keys()),
            "global_params": global_params,
            "script_dir": str(script_dir),
        }
        final_postproc_json = run_dir / "_final_postproc.json"
        if not dry_run:
            with open(
                final_postproc_json, "w", encoding="utf-8"
            ) as f:
                json.dump(final_postproc_data, f, indent=2)

        if run_mode == "parallel":
            # Print command only, don't run inline (a dependent SLURM job
            # runs it later when _slurm_postproc_job is set).
            if use_slurm and global_params.get(
                "_slurm_postproc_job", False
            ):
                print(
                    f"{YELLOW}Final postproc"
                    f" (dependent SLURM job):{RESET}"
                )
            else:
                print(
                    f"{YELLOW}Final postproc"
                    f" (run manually):{RESET}"
                )
            for fp_cmd in final_postproc:
                print(f"  {fp_cmd} {final_postproc_json}")
        else:
            print(
                f"{CYAN}{BOLD}"
                f"=== Running final postproc ==={RESET}"
            )
            fp_results = run_postproc(
                final_postproc, final_postproc_json,
                script_dir, dry_run,
            )
            results["_final_postproc"] = fp_results
        print()

    # Dependent SLURM post-proc job: submit a single -N 1 job (held by
    # --dependency) that runs group + final post-proc once the compute job
    # finishes.  Opt-in via _slurm_postproc_job; all context JSONs it needs
    # were written above (group JSONs in the loop, final JSON just now).
    if (
        use_slurm
        and slurm_job_id
        and global_params.get("_slurm_postproc_job", False)
    ):
        has_group_pp = any(
            gd["params"].get("_group_postproc")
            for gd in groups.values()
        )
        has_any_pp = has_group_pp or bool(
            global_params.get("_final_postproc")
        )
        if has_any_pp:
            dep_type = global_params.get(
                "_slurm_postproc_dependency_type", "afterok"
            )
            postproc_job_id = _finalize_slurm_postproc(
                run_dir, global_params, slurm_job_id,
                dep_type, dry_run,
            )
            if postproc_job_id:
                results["slurm_postproc_job_id"] = postproc_job_id
            print()

    # Record sweep end time and save overall results
    sweep_end_time = _get_iso_timestamp()
    results["end_time"] = sweep_end_time
    if slurm_job_id:
        results["slurm_job_id"] = slurm_job_id

    if not dry_run:
        results_file = run_dir / f"q8020_sweep_meta{workflow_id}.json"
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"{CYAN}{BOLD}Results saved to:{RESET} {results_file}")

    return results


# ---------------------------------------------------------------------------
# Multi-stage orchestration
# ---------------------------------------------------------------------------

def _run_multi_stage_sweep(
    toml_path: str,
    outer_global: dict[str, Any],
    stages: list[tuple[str, dict[str, Any], dict[str, Any]]],
    script: str | None,
    dry_run: bool = False,
    group_filter: list | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a multi-stage sweep where each stage executes after the previous.

    SLURM stages chain via ``--dependency=afterok:<id>``; local stages run
    sequentially in-process.  A non-SLURM stage after a SLURM stage raises
    ``ValueError`` (the local process cannot block on a queued job).
    Returns a combined results dict with per-stage sub-results.
    """
    # Shared workflow ID and base run directory for all stages
    workflow_id = generate_workflow_id()
    output_dir_str = outer_global.get("_output_dir", "./sweep_results")
    output_dir = Path(output_dir_str).expanduser().absolute()
    date_str = datetime.now().strftime("%Y-%m-%d")
    date_dir = output_dir / date_str
    base_run_dir = date_dir / workflow_id

    if not dry_run:
        base_run_dir.mkdir(parents=True, exist_ok=True)
        # Copy input TOML to base run directory
        toml_name = Path(toml_path).name
        shutil.copy2(toml_path, base_run_dir / f"q8020_{toml_name}")

    print(
        f"{CYAN}{BOLD}Multi-stage sweep:{RESET}"
        f" {len(stages)} stages"
    )
    print(f"{CYAN}{BOLD}Output directory:{RESET} {base_run_dir}")
    for i, (sname, _, sgroups) in enumerate(stages, 1):
        n = sum(len(g["expanded_cases"]) for g in sgroups.values())
        print(f"  Stage {i}: {sname} ({n} cases)")
    print()

    stage_context: dict[str, dict[str, Any]] = {}
    all_stage_results: dict[str, Any] = {
        "config_file": str(toml_path),
        "output_dir": str(output_dir),
        "workflow_id": workflow_id,
        "run_dir": str(base_run_dir),
        "start_time": _get_iso_timestamp(),
        "stages": {},
    }
    prev_job_id: str | None = None

    for stage_name, stage_global, stage_groups in stages:
        print(
            f"{CYAN}{BOLD}{'=' * 60}{RESET}"
        )
        print(
            f"{CYAN}{BOLD}Stage: {stage_name}{RESET}"
        )
        print(
            f"{CYAN}{BOLD}{'=' * 60}{RESET}"
        )

        # Substitute {{stages.X.Y}} placeholders
        resolved_global = _substitute_stage_vars(
            stage_global, stage_context,
        )
        resolved_groups = _substitute_stage_vars_in_groups(
            stage_groups, stage_context,
        )

        # Validate: non-SLURM stage after SLURM stage is invalid
        use_slurm = bool(resolved_global.get("_slurm", False))
        if prev_job_id and not use_slurm:
            raise ValueError(
                f"Stage '{stage_name}' uses local execution mode "
                f"but follows a SLURM stage. All stages after a "
                f"SLURM stage must also use _slurm = true, because "
                f"a local process cannot wait for a queued SLURM job."
            )

        stage_run_dir = base_run_dir / stage_name

        # Build a synthetic config dict for run_sweep
        stage_config = {
            "global": resolved_global,
            "groups": resolved_groups,
        }

        # Determine script for this stage
        stage_script = resolved_global.get("_script") or script

        stage_results = run_sweep(
            toml_path=toml_path,
            script=stage_script,
            dry_run=dry_run,
            group_filter=group_filter,
            overrides=overrides,
            _preloaded_config=stage_config,
            _dependency_job_id=prev_job_id,
            _run_dir_override=stage_run_dir,
            _workflow_id_override=workflow_id,
        )

        # Track stage context for variable substitution
        job_id = stage_results.get("slurm_job_id")
        stage_context[stage_name] = {
            "run_dir": str(stage_run_dir),
            "slurm_job_id": job_id or "",
        }
        if job_id:
            prev_job_id = job_id

        all_stage_results["stages"][stage_name] = stage_results
        print()

    all_stage_results["end_time"] = _get_iso_timestamp()

    if not dry_run:
        results_file = (
            base_run_dir / f"q8020_sweep_meta{workflow_id}.json"
        )
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(all_stage_results, f, indent=2)
        print(
            f"{CYAN}{BOLD}Multi-stage results saved to:"
            f"{RESET} {results_file}"
        )

    return all_stage_results


# *****************************************************************************
# CLI

def main():

    parser = argparse.ArgumentParser(
        description="Run parameter sweeps from TOML configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example TOML:
    [global]
    _output_dir = "./results"
    _script = "src/my_algorithm.py"

    [h2_sweep]
    molecule = "H2"
    shots = [1000, 2000, 4000]

Usage:
    q8020-sweep config.toml
    q8020-sweep config.toml --dry-run
    q8020-sweep config.toml h2_sweep          # run only h2_sweep group
    q8020-sweep config.toml h2_sweep li_sweep # run multiple groups
""")
    parser.add_argument("toml_file", help="Path to TOML configuration file")
    parser.add_argument(
        "groups", nargs="*", default=None,
        help="Optional group name(s) to run. If omitted, runs all groups."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print commands without executing"
    )
    parser.add_argument(
        "--set", action="append", default=[],
        metavar="KEY=VALUE", dest="overrides",
        help="Override a [global] _key "
        "(e.g. --set _output_dir=/scratch/out)"
    )

    args = parser.parse_args()

    # Parse --set overrides into a dict
    overrides: dict[str, str] = {}
    for item in args.overrides:
        if "=" not in item:
            print(
                f"Error: --set requires KEY=VALUE, "
                f"got: {item}",
                file=sys.stderr,
            )
            sys.exit(1)
        key, value = item.split("=", 1)
        overrides[key] = value

    # Convert empty list to None for group_filter
    group_filter = args.groups if args.groups else None

    try:
        # Load config to check for multi-stage
        config = load_sweep_config(args.toml_file)
        global_sect = config["global"]
        script = overrides.get(
            "_script",
            global_sect.get("_script"),
        )

        if "stages" in config:
            # Multi-stage sweep
            results = _run_multi_stage_sweep(
                toml_path=args.toml_file,
                outer_global=global_sect,
                stages=config["stages"],
                script=script,
                dry_run=args.dry_run,
                group_filter=group_filter,
                overrides=overrides,
            )

            # Print multi-stage summary
            print(f"\n{CYAN}{BOLD}=== Multi-Stage Summary ==={RESET}")
            for sname, sresults in results.get("stages", {}).items():
                cases = sresults.get("cases", {})
                total = len(cases)
                success = sum(
                    1 for c in cases.values()
                    if c.get("status") == "success"
                )
                submitted = sum(
                    1 for c in cases.values()
                    if c.get("status") == "submitted"
                )
                dry = sum(
                    1 for c in cases.values()
                    if c.get("status") == "dry_run"
                )
                failed = total - success - submitted - dry
                if dry == total:
                    status_str = f"{DIM}dry run{RESET}"
                elif submitted > 0:
                    status_str = f"{CYAN}submitted{RESET}"
                elif failed == 0:
                    status_str = f"{GREEN}all passed{RESET}"
                else:
                    status_str = f"{RED}{failed} failed{RESET}"
                print(f"  {sname}: {total} cases, {status_str}")
        else:
            # Single-stage sweep (existing behavior)
            results = run_sweep(
                args.toml_file, script,
                dry_run=args.dry_run,
                group_filter=group_filter,
                overrides=overrides,
            )

            # Print summary
            print(f"\n{CYAN}{BOLD}=== Summary ==={RESET}")
            total = len(results["cases"])
            success = sum(
                1 for c in results["cases"].values()
                if c.get("status") in ("success", "slurm_completed")
            )
            submitted = sum(
                1 for c in results["cases"].values() if c.get("status") == "submitted"
            )
            generated = sum(
                1 for c in results["cases"].values() if c.get("status") == "generated"
            )
            dry = sum(
                1 for c in results["cases"].values() if c.get("status") == "dry_run"
            )
            failed = total - success - submitted - generated - dry
            if dry == total:
                print(f"{BOLD}Total:{RESET} {total} ({DIM}dry run{RESET})")
            elif submitted > 0:
                failed_str = f", {RED}{BOLD}Failed:{RESET} {failed}" if failed > 0 else ""
                print(f"{BOLD}Total:{RESET} {total}, {CYAN}{BOLD}Submitted:{RESET} {submitted}{failed_str}")
            elif generated > 0:
                failed_str = f", {RED}{BOLD}Failed:{RESET} {failed}" if failed > 0 else ""
                print(f"{BOLD}Total:{RESET} {total}, {YELLOW}Generated:{RESET} {generated}{failed_str}")
            else:
                failed_str = f"{RED}{BOLD}Failed:{RESET} {failed}" if failed > 0 else f"Failed: {failed}"
                print(f"{BOLD}Total:{RESET} {total}, {GREEN}{BOLD}Success:{RESET} {success}, {failed_str}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
