"""q8020_run - Capture one-off script executions to the experiment output directory.

Wraps any Python script execution, capturing stdout to the standard
workflow folder structure so even ad-hoc runs are tracked and visible
in the experiment explorer.

Output structure:
    ~/q8020/_<experiment_id>/<experiment_id>/
        stdout.json    - Script output (expects JSON)
        stderr.txt     - Any stderr output
        params.json    - Command-line args and metadata

For single runs, workflow_id = _<experiment_id> so directory depth
matches sweep runs: _<workflow_id>/<experiment_id>/

Usage:
    # Basic - auto-generates experiment ID
    q8020-run python ax_equals_b_hhl.py --size 2
    
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
from datetime import datetime
from pathlib import Path

from q8020_cfd_metautil.make_meta import _generate_experiment_id, _generate_workflow_id


def run_and_capture(
    command: list[str],
    output_dir: Path,
    experiment_id: str,
    workflow_id: str | None = None,
    timeout: int | None = None,
) -> int:
    """
    Run a command and capture its output to the experiment directory.

    Args:
        command: Command and arguments to run
        output_dir: Base output directory (e.g., ~/q8020)
        experiment_id: Unique experiment identifier
        workflow_id: Optional workflow ID; auto-generated if None
        timeout: Optional timeout in seconds

    Returns:
        Exit code from the subprocess
    """
    # Create output directory structure: <workflow_id>/<exp_id>/
    if workflow_id is None:
        workflow_id = _generate_workflow_id(experiment_id)
    experiment_dir = output_dir / workflow_id / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    
    stderr_file = experiment_dir / "stderr.txt"
    params_file = experiment_dir / "params.json"
    
    # Save params/metadata
    params = {
        "command": command,
        "script": command[1] if len(command) > 1 else None,
        "args": command[2:] if len(command) > 2 else [],
        "workflow_id": workflow_id,
        "experiment_id": experiment_id,
        "timestamp": datetime.now().isoformat(),
        "cwd": os.getcwd()
    }
    with open(params_file, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2)
    
    print(f"📁 Output: {experiment_dir}", file=sys.stderr)
    # Append ID args to command so script knows its IDs
    command_with_ids = command + [
        "--experiment-id", experiment_id,
        "--workflow-id", workflow_id
    ]
    
    print(f"▶️  Running: {' '.join(command_with_ids)}", file=sys.stderr)
    print("-" * 60, file=sys.stderr)
    
    # Run the command, capturing stdout and stderr
    # check=False because we handle exit codes ourselves
    result = subprocess.run(
        command_with_ids,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout
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
    
    # Detect if stdout is JSON and use appropriate extension
    if stdout_content:
        try:
            json_data = json.loads(stdout_content)
            stdout_file = experiment_dir / "stdout.json"
            with open(stdout_file, "w", encoding="utf-8") as f:
                json.dump(json_data, f, indent=2)
        except json.JSONDecodeError:
            stdout_file = experiment_dir / "stdout.dat"
            with open(stdout_file, "w", encoding="utf-8") as f:
                f.write(stdout_content)
    else:
        # Empty output - no stdout file created
        stdout_file = None
    
    print("-" * 60, file=sys.stderr)
    if result.returncode == 0:
        if stdout_file:
            print(f"✅ Success: {stdout_file}", file=sys.stderr)
        else:
            print("✅ Success (no output)", file=sys.stderr)
    else:
        print(f"❌ Failed (exit {result.returncode}): {stderr_file}", file=sys.stderr)
    
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Capture script execution to experiment output directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  q8020-run python ax_equals_b_hhl.py --size 2
  q8020-run --output-dir ~/my_experiments python ax_equals_b_hhl.py --size 2
"""
    )
    parser.add_argument("--output-dir", "-o", type=str, default="~/q8020",
                        help="Base output directory (default: ~/q8020)")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="Command to run (e.g., python script.py --arg value)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Resolve paths and generate experiment ID
    output_dir = Path(args.output_dir).expanduser()
    experiment_id = _generate_experiment_id()
    
    # Run and capture
    exit_code = run_and_capture(
        command=args.command,
        output_dir=output_dir,
        experiment_id=experiment_id,
    )
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
