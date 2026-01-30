"""
Parameter sweep orchestration for quantum experiments.

This module provides a generic framework for running parameter sweeps across quantum
algorithms. It reads TOML configuration files, expands parameter combinations, executes
scripts for each case, and organizes results in a structured directory hierarchy.

Function Categories:
    Configuration Management:
        - load_sweep_config: Parse TOML files with global params and experiment groups
        - expand_case_lists: Expand list-valued parameters into individual cases
        - build_command_args: Convert parameter dicts to command-line arguments

    Execution:
        - run_sweep: Main orchestrator - execute full parameter sweep
        - run_single: Execute a single case with full metadata capture
        - run_postproc: Execute postprocessing scripts on sweep results

Directory Structure:
    _<workflow_id>/
        q8020_sweep_meta<workflow_id>.json   # Overall sweep metadata with start/end times
        q8020_expanded_cases.json       # All parameter combinations
        q8020_<config>.toml             # Copy of input TOML
        <experiment_id>/
            q8020_params.json           # Case parameters with IDs
            q8020_stdout.txt            # Script stdout
            q8020_stderr.txt            # Script stderr
            q8020_metadata_<exp_id>.json  # Unified metadata (harvested)
            q8020_experiment_<exp_id>_0.json  # Metadata fragments
            q8020_case_<exp_id>_0.json
            q8020_code_<exp_id>_0.json
            q8020_exec_stats_<exp_id>_0.json
            q8020_artifacts_<exp_id>_0.json
            *.png, *.pdf                # Generated visualizations

TOML Configuration Format:
    [global]
    _output_dir = "./results"
    _script = "src/my_algorithm.py"

    [h2_sweep]
    molecule = "H2"
    shots = [1000, 2000, 4000]
    _group_postproc = ["python analyze.py"]

Usage:
    q8020-sweeper config.toml
    q8020-sweeper config.toml --script src/algo.py
    q8020-sweeper config.toml --dry-run
    q8020-sweeper config.toml --group h2_sweep
"""

#pylint: disable=broad-exception-caught

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

import tomllib

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
    """
    Capture environment snapshot for a library path.
    
    If the path looks like a venv, captures pip freeze output.
    Otherwise, captures a detailed file listing.
    
    Args:
        lib_path: Path to library directory or venv
        
    Returns:
        Dict with snapshot data
    """
    path = Path(lib_path).expanduser().resolve()
    
    if _is_venv(path):
        return _capture_venv_packages(path)
    else:
        return _capture_dir_listing(path)


def _inventory_artifacts(artifact_dir: Path) -> dict[str, Any]:
    """
    Scan directory and build artifact inventory.

    Args:
        artifact_dir: Directory to scan for artifacts

    Returns:
        Dict with directory path and list of file info dicts
    """
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
    """Build case metadata section.

    Args:
        command: The command that was executed
        cwd: Current working directory
        case_id: TOML case id (e.g., "h2_sweep_0"). If provided, used as name.
        case_params: TOML parameters for this case. If provided, included in output.

    Returns:
        Case metadata dict
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


def _make_code_section(
    command: list[str],
    env_before: dict[str, Any] | None = None,
    env_after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build code metadata section with optional library version snapshots."""
    interpreter = command[0] if command else "unknown"
    entry_point = command[1] if len(command) > 1 else None

    result: dict[str, Any] = {
        "_source": "sweep",
        "entry_point": entry_point,
        "interpreter": interpreter,
    }
    if env_before is not None:
        result["library_versions_before"] = env_before
    if env_after is not None:
        result["library_versions_after"] = env_after
    return result


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
) -> dict[str, Any]:
    """
    Run a single command with full metadata capture.

    This is the inner execution function called by run_sweep() for each case.
    The caller creates the directory; this function assumes case_dir exists.

    Args:
        command: Command and arguments to run
        case_dir: Directory for this case (must exist)
        experiment_id: Unique experiment identifier
        workflow_id: Workflow ID (required)
        case_id: TOML case id (e.g., "h2_sweep_0"). Used in metadata.
        case_params: TOML parameters for this case. Included in case metadata.
        timeout: Optional timeout in seconds
        artifact_dir: Directory where black box writes artifacts; defaults to
                      case_dir if not specified
        env_path: Path to environment/venv to snapshot before and after execution

    Returns:
        Result dict with command, case_id, experiment_id, status, returncode,
        output_dir. On timeout/exception, status is "timeout" or "exception".
    """
    # Default artifact_dir to case_dir if not specified
    if artifact_dir is None:
        artifact_dir = case_dir

    stderr_file = case_dir / "q8020_stderr.txt"
    params_file = case_dir / "q8020_params.json"

    cwd = os.getcwd()

    # Capture environment snapshot before execution
    env_before = None
    if env_path:
        env_before = capture_lib_snapshot(env_path)
        env_before_file = case_dir / "q8020_env_before.json"
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
        params_data.update({k: v for k, v in case_params.items() if not k.startswith("_")})

    with open(params_file, "w", encoding="utf-8") as f:
        json.dump(params_data, f, indent=2)

    # Append ID args to command so script knows its IDs
    command_with_ids = command + [
        "--experiment-id", experiment_id,
        "--workflow-id", workflow_id,
    ]

    # Record start time
    start_time = _get_iso_timestamp()
    start_dt = datetime.now(timezone.utc)

    # Base result dict (updated on success/failure)
    result_dict: dict[str, Any] = {
        "command": command_with_ids,
        "case_id": case_id,
        "experiment_id": experiment_id,
        "output_dir": str(case_dir),
    }

    try:
        # Run the command, capturing stdout and stderr
        result = subprocess.run(
            command_with_ids,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
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
        stdout_file = case_dir / "q8020_stdout.txt"
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
            command=command_with_ids,
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
            command=command_with_ids, cwd=cwd, case_id=case_id, case_params=case_params
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
            command=command_with_ids, cwd=cwd, case_id=case_id, case_params=case_params
        )
        write_experiment(case_dir, experiment_section, prefix="q8020_sweep", experiment_id=experiment_id)
        write_case(case_dir, case_section, prefix="q8020_sweep", experiment_id=experiment_id)
        # Note: write_code is deferred until after env_after is captured
        write_exec_stats(case_dir, exec_stats, prefix="q8020_sweep", experiment_id=experiment_id)

        result_dict["status"] = "exception"
        result_dict["error"] = str(e)

    # Capture environment snapshot after execution
    env_after = None
    if env_path:
        env_after = capture_lib_snapshot(env_path)
        env_after_file = case_dir / "q8020_env_after.json"
        with open(env_after_file, "w", encoding="utf-8") as f:
            json.dump(env_after, f, indent=2)

    # Write code fragment with both env snapshots (deferred from try/except blocks)
    code_section = _make_code_section(
        command=command_with_ids,
        env_before=env_before,
        env_after=env_after,
    )
    write_code(case_dir, code_section, prefix="q8020_sweep", experiment_id=experiment_id)

    return result_dict


# ---------------------------------------------------------------------------
# Sweep configuration and execution
# ---------------------------------------------------------------------------

def expand_case_lists(case_id: str, case_params: dict) -> list:
    """
    Expand a case with list-valued parameters into multiple subcases.
    
    For example, if a case has:
        shots = [100, 1000, 10000]
        ancilla = 6
    
    This will expand into 3 subcases:
        case_id_0: shots=100, ancilla=6
        case_id_1: shots=1000, ancilla=6
        case_id_2: shots=10000, ancilla=6
    
    Returns:
        List of (expanded_case_id, expanded_params) tuples
    """
    list_params = {}
    scalar_params = {}

    for key, value in case_params.items():
        if isinstance(value, list):
            list_params[key] = value
        else:
            scalar_params[key] = value

    # If no lists, return the original case
    if not list_params:
        return [(case_id, case_params.copy())]

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

    return expanded_cases


def load_sweep_config(toml_path: str) -> dict:
    """
    Load sweep configuration from a TOML file.
    
    Expected structure:
        [global]  # or [_global]
        _output_dir = "./results"
        _script = "python src/solver.py"  # full command, can include venv activation
        _inject_outdir = "outdir"
        _case_postproc = ["python harvester.py"]
        _group_postproc = ["python group_postproc.py"]
        _final_postproc = ["python final_postproc.py"]
        
        [case1]
        molecule = "H2"
        shots = [1000, 2000, 4000]
        
        [case2]
        molecule = "LiH"
        shots = 8192
        _group_postproc = ["python custom_postproc.py"]  # overrides global
    
    Meta-keys (underscore-prefixed) are inherited from global to groups.
    Group-level meta-keys override global ones.
    
    Returns:
        dict with 'global' config and 'groups' (original cases with their expansions)
    """
    toml_path = Path(toml_path)
    if not toml_path.is_file():
        raise FileNotFoundError(f"TOML file not found: {toml_path}")

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    # Support both "global" and "_global" as section names
    global_params = data.get("global", data.get("_global", {}))
    groups = {}  # original_case_id -> {params, expanded_cases}

    for key in data:
        if key in ("global", "_global"):
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


def build_command_args(params: dict, arg_mapping: dict = None) -> list:
    """
    Convert parameter dict to command-line arguments.
    
    Args:
        params: Parameter dict from TOML
        arg_mapping: Optional dict mapping param names to CLI arg names
                     e.g., {"bond_length": "--bond-length"}
                     If None, uses --{param_name} with hyphens (argparse convention)
                     Keys starting with "-" are used as-is (e.g., "-nelem" -> -nelem)
    
    Returns:
        List of command-line arguments
    """
    args = []
    skip_keys = {"executable", "_output_dir", "_case_postproc", "_group_postproc", "_final_postproc", "_inject_outdir", "_env"}
    
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


def run_postproc(postproc_list: list, postproc_json: Path, script_dir: Path = None, dry_run: bool = False) -> list:
    """
    Run postprocessing scripts with a JSON file as the single argument.
    
    Args:
        postproc_list: List of postproc commands (e.g., ["python analyze.py", "python plot.py --verbose"])
        postproc_json: Path to JSON file containing postproc context
        script_dir: Directory containing the script (added to PYTHONPATH for module imports)
        dry_run: If True, print commands without executing
    
    Returns:
        List of results for each postproc command
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
        cmd = postproc_cmd.split() + [str(postproc_json)]
        
        if dry_run:
            print(f"  Postproc (dry-run): {' '.join(cmd)}")
            results.append({"command": cmd, "status": "dry_run"})
            continue
        
        print(f"  Running postproc: {postproc_cmd}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                check=False,
                env=env
            )
            if result.returncode == 0:
                print(f"    {GREEN}✓ Postproc completed{RESET}")
                if result.stdout:
                    print(result.stdout)
            else:
                print(f"    {RED}✗ Postproc error (code {result.returncode}){RESET}")
                if result.stderr:
                    print(f"      {result.stderr[:200]}")
            results.append({
                "command": cmd,
                "status": "success" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "stdout": result.stdout[:500] if result.stdout else "",
                "stderr": result.stderr[:500] if result.stderr else ""
            })
        except Exception as e:
            print(f"    {RED}✗ Postproc exception: {e}{RESET}")
            results.append({"command": cmd, "status": "exception", "error": str(e)})
    
    return results


def run_sweep(toml_path: str, script: str, arg_mapping: dict = None, dry_run: bool = False, group_filter: list = None) -> dict:
    """
    Run a parameter sweep from a TOML configuration file.
    
    Args:
        toml_path: Path to the TOML configuration file
        script: Path to the Python script to run (will be invoked as 'python <script>')
        arg_mapping: Optional mapping of param names to CLI arg names
        dry_run: If True, print commands without executing
        group_filter: Optional list of group names to run (if None, run all groups)
    
    Returns:
        dict with results for each case
    """
    config = load_sweep_config(toml_path)
    global_params = config["global"]
    groups = config["groups"]
    
    # Filter groups if group_filter is specified
    if group_filter:
        missing = [g for g in group_filter if g not in groups]
        if missing:
            raise ValueError(f"Group(s) not found: {missing}. Available groups: {list(groups.keys())}")
        groups = {g: groups[g] for g in group_filter}
    
    # Build executable command: python <script>
    # May be None if each group specifies its own _script
    executable = f"python {script}" if script else None
    
    # Get script directory for PYTHONPATH (for module imports in postproc)
    # Use first group's script if no global script
    if script:
        script_path = Path(script)
        script_dir = script_path.parent.resolve()
    else:
        # Find first group with a _script to get script_dir
        for group_data in groups.values():
            group_script = group_data["params"].get("_script")
            if group_script:
                script_dir = Path(group_script).parent.resolve()
                break
        else:
            script_dir = Path(".").resolve()
    
    # Expand ~ to user home directory (works on Unix, Mac, Windows)
    output_dir_str = global_params.get("_output_dir", "./sweep_results")
    output_dir = Path(output_dir_str).expanduser().resolve()
    
    # Create output_dir if it doesn't exist
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create run subdirectory with workflow ID (_ prefix)
    workflow_id = generate_workflow_id()
    run_dir = output_dir / workflow_id
    
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
    
    print(f"Running sweep: {total_cases} cases in {len(groups)} groups")
    print(f"Output directory: {run_dir}")
    print()
    
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
        
        print(f"=== Group: {group_id} ({len(expanded_cases)} cases) ===")
        
        group_case_dirs = []
        
        # Run each expanded case in the group
        for case_id, params in expanded_cases.items():
            print(f"  Case: {case_id}")
            
            # Generate experiment_id for this case
            experiment_id = generate_experiment_id()

            # Create case output directory
            case_dir = run_dir / experiment_id
            if not dry_run:
                case_dir.mkdir(parents=True, exist_ok=True)
            group_case_dirs.append(case_dir)

            # Build command args
            cmd_args = build_command_args(params, arg_mapping)

            # Inject outdir arg if _inject_outdir is set (value is the full arg name, e.g., "-outdir")
            inject_outdir = params.get("_inject_outdir")
            if inject_outdir:
                cmd_args.extend([inject_outdir, str(case_dir)])

            # Check if _script contains shell operators (needs bash -c)
            if "&&" in group_executable or "||" in group_executable or "|" in group_executable:
                # Shell command - wrap with bash -c
                full_cmd = f"{group_executable} {' '.join(cmd_args)}"
                cmd = ["bash", "-c", full_cmd]
            else:
                # Simple command - split and expand paths
                cmd_parts = group_executable.split()
                cmd_parts = [str(Path(p).expanduser()) if p.startswith("~") or "/" in p or "\\" in p 
                             else p for p in cmd_parts]
                cmd = cmd_parts + cmd_args

            if dry_run:
                # For shell commands, show the bash -c with quoted argument
                if cmd[0] == "bash" and cmd[1] == "-c":
                    print(f"    Command: bash -c \"{cmd[2]}\"")
                else:
                    print(f"    Command: {' '.join(cmd)}")
                results["cases"][case_id] = {"command": cmd, "status": "dry_run"}

                # Run per-case postproc in dry-run mode
                case_postproc = params.get("_case_postproc", [])
                if case_postproc:
                    if isinstance(case_postproc, str):
                        case_postproc = [case_postproc]
                    case_postproc_json = case_dir / "q8020_case_postproc.json"
                    run_postproc(case_postproc, case_postproc_json, script_dir, dry_run)
                continue

            # Get _env path if specified for env snapshotting
            env_path = params.get("_env")

            # Run the case via run_single()
            case_result = run_single(
                command=cmd,
                case_dir=case_dir,
                experiment_id=experiment_id,
                workflow_id=workflow_id,
                case_id=case_id,
                case_params=params,
                timeout=3600,
                env_path=env_path,
            )

            results["cases"][experiment_id] = case_result

            if case_result["status"] == "success":
                print(f"    {GREEN}✓ Completed{RESET}")
            elif case_result["status"] == "error":
                print(f"    {RED}✗ Error (code {case_result.get('returncode')}){RESET}")
            else:
                print(f"    {RED}✗ {case_result['status']}{RESET}")

            # Run per-case postproc if specified
            case_postproc = params.get("_case_postproc", [])
            if case_postproc:
                if isinstance(case_postproc, str):
                    case_postproc = [case_postproc]

                # Prepare case postproc data
                case_postproc_data = {
                    "case_id": case_id,
                    "experiment_id": experiment_id,
                    "workflow_id": workflow_id,
                    "case_dir": str(case_dir),
                    "params": params,
                }
                case_postproc_json = case_dir / "q8020_case_postproc.json"
                if not dry_run:
                    with open(case_postproc_json, "w", encoding="utf-8") as f:
                        json.dump(case_postproc_data, f, indent=2)

                case_pp_results = run_postproc(
                    case_postproc, case_postproc_json, script_dir, dry_run
                )
                results["cases"][experiment_id]["_case_postproc"] = case_pp_results

            # Harvest all fragments into q8020_metadata_{exp_id}.json AFTER postproc
            if not dry_run:
                metadata_file = case_dir / f"q8020_metadata_{experiment_id}.json"
                unified_metadata, warnings, _ = harvest_metadata(case_dir)
                for warning in warnings:
                    print(f"    ⚠️  {warning}", file=sys.stderr)
                with open(metadata_file, "w", encoding="utf-8") as f:
                    json.dump(unified_metadata, f, indent=2)
        
        # Run group postproc for this group if specified
        group_postproc = group_params.get("_group_postproc", [])
        if group_postproc:
            # Ensure postproc is a list
            if isinstance(group_postproc, str):
                group_postproc = [group_postproc]
            
            # Prepare postproc data
            postproc_data = {
                "group_id": group_id,
                "workflow_id": workflow_id,
                "run_dir": str(run_dir),
                "case_dirs": [str(d) for d in group_case_dirs],
                "params": group_params
            }
            postproc_json = run_dir / f"_group_postproc_{group_id}.json"
            if not dry_run:
                with open(postproc_json, "w", encoding="utf-8") as f:
                    json.dump(postproc_data, f, indent=2)
            
            postproc_results = run_postproc(group_postproc, postproc_json, script_dir, dry_run)
            results["groups"][group_id] = {"_group_postproc": postproc_results}
        
        print()
    
    # Run _final_postproc if specified (runs after all groups complete)
    final_postproc = global_params.get("_final_postproc", [])
    if final_postproc:
        if isinstance(final_postproc, str):
            final_postproc = [final_postproc]
        
        # Collect all case directories from all groups
        all_case_dirs = []
        for group_id, group_data in groups.items():
            for case_id in group_data["expanded_cases"]:
                all_case_dirs.append(str(run_dir / case_id))
        
        # Write final_postproc JSON file
        final_postproc_data = {
            "workflow_id": workflow_id,
            "run_dir": str(run_dir),
            "case_dirs": all_case_dirs,
            "groups": list(groups.keys()),
            "global_params": global_params
        }
        final_postproc_json = run_dir / "_final_postproc.json"
        if not dry_run:
            with open(final_postproc_json, "w", encoding="utf-8") as f:
                json.dump(final_postproc_data, f, indent=2)
        
        print("=== Running final postproc ===")
        final_postproc_results = run_postproc(final_postproc, final_postproc_json, script_dir, dry_run)
        results["_final_postproc"] = final_postproc_results
        print()
    
    # Record sweep end time and save overall results
    sweep_end_time = _get_iso_timestamp()
    results["end_time"] = sweep_end_time

    if not dry_run:
        results_file = run_dir / f"q8020_sweep_meta{workflow_id}.json"
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to: {results_file}")

    return results


# *****************************************************************************
# CLI

def main():
    import argparse

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

    args = parser.parse_args()

    # Convert empty list to None for group_filter
    group_filter = args.groups if args.groups else None

    try:
        # Read _script from TOML
        with open(args.toml_file, "rb") as f:
            toml_data = tomllib.load(f)
            script = toml_data.get("global", toml_data.get("_global", {})).get("_script")
            # Allow None - groups may specify their own _script

        results = run_sweep(
            args.toml_file, script, dry_run=args.dry_run, group_filter=group_filter
        )

        # Print summary
        print("\n=== Summary ===")
        total = len(results["cases"])
        success = sum(
            1 for c in results["cases"].values() if c.get("status") == "success"
        )
        print(f"Total: {total}, Success: {success}, Failed: {total - success}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
