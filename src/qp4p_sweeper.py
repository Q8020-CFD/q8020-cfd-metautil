"""
Generic sweep runner for executing scripts with parameters from TOML files.

Reads a TOML file, expands list parameters into individual cases,
runs an executable for each case, and captures results to a directory.
"""

#pylint: disable=broad-exception-caught

import subprocess
import json
import shutil
import sys
import uuid
import importlib.metadata
from pathlib import Path
from datetime import datetime
from itertools import product

import tomli as tomllib

# ANSI color codes
GREEN = '\033[92m'
RED = '\033[91m'
RESET = '\033[0m'

def get_library_versions() -> dict:
    """
    Capture versions of key libraries in the current environment.
    
    Returns:
        Dictionary mapping package names to version strings
    """
    packages = [
        "qrisp",
        "qiskit",
        "qiskit-aer",
        "qiskit-ibm-runtime",
        "qiskit-algorithms",
        "qiskit-nature",
        "qiskit-addon-sqd",
        "numpy",
        "scipy",
        "pennylane",
        "pyscf",
        "tomli"
    ]
    
    versions = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    
    return versions


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
        [global]
        _output_dir = "./results"
        _final_postproc = ["python final_postproc.py"]
        
        [case1]
        molecule = "H2"
        shots = [1000, 2000, 4000]
        _group_postproc = ["python group_postproc.py"]
        
        [case2]
        molecule = "LiH"
        shots = 8192
        _group_postproc = ["python custom_postproc.py"]
    
    Returns:
        dict with 'global' config and 'groups' (original cases with their expansions)
    """
    toml_path = Path(toml_path)
    if not toml_path.is_file():
        raise FileNotFoundError(f"TOML file not found: {toml_path}")

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    global_params = data.get("global", {})
    groups = {}  # original_case_id -> {params, expanded_cases}

    for key in data:
        if key == "global":
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
    
    Normalizes hyphenated parameter names to use underscores.
    
    Args:
        params: Parameter dict from TOML
        arg_mapping: Optional dict mapping param names to CLI arg names
                     e.g., {"bond_length": "--bond-length"}
                     If None, uses --{param_name} (preserving underscores/hyphens as-is)
    
    Returns:
        List of command-line arguments
    """
    args = []
    skip_keys = {"executable", "_output_dir", "_metadata", "_case_postproc", "_group_postproc", "_final_postproc"}
    
    for key, value in params.items():
        if key in skip_keys or key.startswith("_"):
            continue
        
        # Convert underscores to hyphens for CLI args (argparse convention)
        cli_key = key.replace('_', '-')
        
        # Determine CLI arg name
        if arg_mapping and key in arg_mapping:
            arg_name = arg_mapping[key]
        else:
            # Use hyphenated key for CLI args
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
        import os
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
    
    # Create run subdirectory with short UUID
    run_id = uuid.uuid4().hex[:8]  # 8-character hex string
    run_dir = output_dir / run_id
    
    # Count total cases across all groups
    total_cases = sum(len(g["expanded_cases"]) for g in groups.values())
    
    if not dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
        # Copy input TOML to run directory
        shutil.copy2(toml_path, run_dir / Path(toml_path).name)
        # Write expanded cases to JSON before running
        expanded_cases_file = run_dir / "expanded_cases.json"
        all_cases = {}
        for group in groups.values():
            all_cases.update(group["expanded_cases"])
        with open(expanded_cases_file, "w", encoding="utf-8") as f:
            json.dump({"cases": all_cases, "global": global_params}, f, indent=2)
        # Write start time marker
        start_time_file = run_dir / "START"
        with open(start_time_file, "w", encoding="utf-8") as f:
            f.write(datetime.now().isoformat())
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Capture library versions once for the entire sweep
    library_versions = get_library_versions()
    
    results = {
        "config_file": str(toml_path),
        "output_dir": str(output_dir),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "timestamp": timestamp,
        "library_versions": library_versions,
        "groups": {},
        "cases": {}
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
            group_executable = f"python {group_script}"
        else:
            group_executable = executable
        
        print(f"=== Group: {group_id} ({len(expanded_cases)} cases) ===")
        
        group_case_dirs = []
        
        # Run each expanded case in the group
        for case_id, params in expanded_cases.items():
            print(f"  Case: {case_id}")
            
            # Build command - expand ~ in executable path
            cmd_parts = group_executable.split()
            cmd_parts = [str(Path(p).expanduser()) if p.startswith("~") or "/" in p or "\\" in p 
                         else p for p in cmd_parts]
            cmd_args = build_command_args(params, arg_mapping)
            cmd = cmd_parts + cmd_args
            
            if dry_run:
                print(f"    Command: {' '.join(cmd)}")
                results["cases"][case_id] = {"command": cmd, "status": "dry_run"}
                group_case_dirs.append(run_dir / case_id)
                continue
            
            # Create case output directory
            case_dir = run_dir / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            group_case_dirs.append(case_dir)
            
            # Save case parameters (include run_id)
            params_with_run_id = params.copy()
            params_with_run_id["_run_id"] = run_id
            params_file = case_dir / "params.json"
            with open(params_file, "w", encoding="utf-8") as f:
                json.dump(params_with_run_id, f, indent=2)
            
            # Write case START marker
            case_start_file = case_dir / "START"
            with open(case_start_file, "w", encoding="utf-8") as f:
                f.write(datetime.now().isoformat())
            
            # Run the command
            stdout_file = case_dir / "stdout.json"
            stderr_file = case_dir / "stderr.txt"
            
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=3600,  # 1 hour timeout
                    check=False
                )
                
                # Save stdout (expected to be JSON)
                with open(stdout_file, "w", encoding="utf-8") as f:
                    f.write(result.stdout)
                
                # Save stderr
                with open(stderr_file, "w", encoding="utf-8") as f:
                    f.write(result.stderr)
                
                # Write case END marker
                case_end_file = case_dir / "END"
                with open(case_end_file, "w", encoding="utf-8") as f:
                    f.write(datetime.now().isoformat())
                
                case_result = {
                    "command": cmd,
                    "status": "success" if result.returncode == 0 else "error",
                    "returncode": result.returncode,
                    "output_dir": str(case_dir)
                }
                
                if result.returncode == 0:
                    print(f"    {GREEN}✓ Completed{RESET}")
                else:
                    print(f"    {RED}✗ Error (code {result.returncode}){RESET}")
                    if result.stderr:
                        print(f"      {result.stderr[:200]}")
            
            except subprocess.TimeoutExpired:
                case_result = {
                    "command": cmd,
                    "status": "timeout",
                    "output_dir": str(case_dir)
                }
                print(f"    {RED}✗ Timeout{RESET}")
            
            except Exception as e:
                case_result = {
                    "command": cmd,
                    "status": "exception",
                    "error": str(e),
                    "output_dir": str(case_dir)
                }
                print(f"    {RED}✗ Exception: {e}{RESET}")
            
            results["cases"][case_id] = case_result
        
        # Run group postproc for this group if specified
        group_postproc = group_params.get("_group_postproc", [])
        if group_postproc:
            # Ensure postproc is a list
            if isinstance(group_postproc, str):
                group_postproc = [group_postproc]
            
            # Prepare postproc data
            postproc_data = {
                "group_id": group_id,
                "run_id": run_id,
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
            "run_id": run_id,
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
    
    # Save overall results
    if not dry_run:
        results_file = run_dir / "sweep_results.json"
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        # Write end time marker
        end_time_file = run_dir / "END"
        with open(end_time_file, "w", encoding="utf-8") as f:
            f.write(datetime.now().isoformat())
        print(f"Results saved to: {results_file}")
    
    return results


# *****************************************************************************
# CLI

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Run parameter sweeps from TOML configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example TOML:
    [global]
    output_dir = "./results"
    
    [h2_sweep]
    molecule = "H2"
    shots = [1000, 2000, 4000, 8192]
    ancilla = 6
    
    [lih_test]
    molecule = "LiH"
    shots = 8192
    ancilla = [4, 6, 8]

Usage:
    python qp4p_sweeper.py src/gs_qpe.py input/gs_qpe.toml
    python qp4p_sweeper.py src/gs_qpe.py input/gs_qpe.toml --dry-run
    python qp4p_sweeper.py src/gs_qpe.py input/gs_qpe.toml --group size_study
""")
    parser.add_argument("toml_file", help="Path to TOML configuration file")
    parser.add_argument("script", nargs="?", default=None, 
                        help="Python script to run (optional if TOML specifies scripts per experiment)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing")
    parser.add_argument("--group", type=str, action="append", default=None,
                        help="Run only specified group(s). Can be repeated: --group g1 --group g2")
    
    args = parser.parse_args()
    
    try:
        # If script not provided as argument, try to read from TOML
        script = args.script
        if script is None:
            import tomli as tomli_local
            with open(args.toml_file, "rb") as f:
                toml_data = tomli_local.load(f)
                script = toml_data.get("global", {}).get("_script")
                # Allow None - groups may specify their own _script
        
        results = run_sweep(args.toml_file, script, dry_run=args.dry_run, group_filter=args.group)
        
        # Print summary
        print("\n=== Summary ===")
        total = len(results["cases"])
        success = sum(1 for c in results["cases"].values() if c.get("status") == "success")
        print(f"Total: {total}, Success: {success}, Failed: {total - success}")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
