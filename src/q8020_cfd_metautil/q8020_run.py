"""
q8020_run - Capture one-off script executions to the experiment output directory.

Wraps any Python script execution, capturing stdout to the standard
workflow folder structure so even ad-hoc runs are tracked and visible
in the experiment explorer.

Output structure:
    ~/q8020/{workflow_id}/{case_name}/
        stdout.json    - Script output (expects JSON)
        stderr.txt     - Any stderr output
        params.json    - Command-line args and metadata

Usage:
    # Basic - auto-generates workflow ID and case name
    q8020-run python ax_equals_b_hhl.py --size 2
    
    # Specify case name
    q8020-run --case hhl_test python ax_equals_b_hhl.py --size 2
    
    # Specify workflow ID (to group related runs)
    q8020-run --workflow my_experiment --case run1 python ax_equals_b_hhl.py --size 2
    
    # Custom output directory
    q8020-run --output-dir ~/my_experiments python ax_equals_b_hhl.py --size 2

The script's stdout is captured to stdout.json. If the output is valid JSON,
it's stored as-is. Otherwise, it's wrapped in a JSON object.
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path


def generate_workflow_id() -> str:
    """Generate a short unique workflow ID."""
    return uuid.uuid4().hex[:8]


def generate_case_name(script_path: str) -> str:
    """Generate a case name from script name and timestamp."""
    script_name = Path(script_path).stem if script_path else "run"
    timestamp = datetime.now().strftime("%H%M%S")
    return f"{script_name}_{timestamp}"


def run_and_capture(
    command: list[str],
    output_dir: Path,
    case_name: str,
    workflow_id: str
) -> int:
    """
    Run a command and capture its output to the experiment directory.
    
    Args:
        command: Command and arguments to run
        output_dir: Base output directory (e.g., ~/q8020)
        case_name: Name for this case/run
        workflow_id: Workflow ID for grouping runs
    
    Returns:
        Exit code from the subprocess
    """
    # Create output directory structure
    case_dir = output_dir / workflow_id / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    
    stdout_file = case_dir / "stdout.json"
    stderr_file = case_dir / "stderr.txt"
    params_file = case_dir / "params.json"
    
    # Save params/metadata
    params = {
        "command": command,
        "script": command[1] if len(command) > 1 else None,
        "args": command[2:] if len(command) > 2 else [],
        "workflow_id": workflow_id,
        "case_name": case_name,
        "timestamp": datetime.now().isoformat(),
        "cwd": os.getcwd()
    }
    with open(params_file, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2)
    
    print(f"📁 Output: {case_dir}", file=sys.stderr)
    print(f"▶️  Running: {' '.join(command)}", file=sys.stderr)
    print("-" * 60, file=sys.stderr)
    
    # Run the command, capturing stdout and stderr
    # check=False because we handle exit codes ourselves
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False
    )
    
    # Write stderr if any
    if result.stderr:
        with open(stderr_file, "w", encoding="utf-8") as f:
            f.write(result.stderr)
        # Also print stderr to terminal
        print(result.stderr, file=sys.stderr, end="")
    
    # Process stdout
    stdout_content = result.stdout.strip()
    
    # Also print stdout to terminal
    if stdout_content:
        print(stdout_content)
    
    # Try to parse as JSON, otherwise wrap it
    if stdout_content:
        try:
            # Validate it's JSON
            json_data = json.loads(stdout_content)
            with open(stdout_file, "w", encoding="utf-8") as f:
                json.dump(json_data, f, indent=2)
        except json.JSONDecodeError:
            # Wrap non-JSON output
            wrapped = {
                "raw_output": stdout_content,
                "status": "success" if result.returncode == 0 else "error",
                "exit_code": result.returncode
            }
            with open(stdout_file, "w", encoding="utf-8") as f:
                json.dump(wrapped, f, indent=2)
    else:
        # Empty output
        with open(stdout_file, "w", encoding="utf-8") as f:
            json.dump({
                "status": "error" if result.returncode != 0 else "empty",
                "exit_code": result.returncode
            }, f, indent=2)
    
    print("-" * 60, file=sys.stderr)
    if result.returncode == 0:
        print(f"✅ Success: {stdout_file}", file=sys.stderr)
    else:
        print(f"❌ Failed (exit {result.returncode}): {stderr_file}", file=sys.stderr)
    
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Capture script execution to experiment output directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  q8020-run python ax_equals_b_hhl.py --size 2
  q8020-run --case my_test python ax_equals_b_vqls.py --size 4
  q8020-run --workflow exp1 --case run1 python script.py
"""
    )
    parser.add_argument("--output-dir", "-o", type=str, default="~/q8020",
                        help="Base output directory (default: ~/q8020)")
    parser.add_argument("--workflow", "-w", type=str, default=None,
                        help="Workflow ID (default: auto-generated)")
    parser.add_argument("--case", "-c", type=str, default=None,
                        help="Case name (default: script_HHMMSS)")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="Command to run (e.g., python script.py --arg value)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Resolve paths and generate IDs
    output_dir = Path(args.output_dir).expanduser()
    workflow_id = args.workflow or generate_workflow_id()
    
    # Find the script in the command for case name generation
    script_path = None
    for part in args.command:
        if part.endswith(".py"):
            script_path = part
            break
    
    case_name = args.case or generate_case_name(script_path)
    
    # Run and capture
    exit_code = run_and_capture(
        command=args.command,
        output_dir=output_dir,
        case_name=case_name,
        workflow_id=workflow_id
    )
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
