"""Worker process for parallel sweep execution.

Invoked by the parallel launcher as:
    python -m q8020_cfd_metautil.sweep_worker pipeline_args.json

Reads case arguments from JSON, runs the full per-case pipeline
(preproc -> solver -> postproc -> harvest), writes result JSON.
"""

import json
import sys
from pathlib import Path

from q8020_cfd_metautil.sweep import run_case_pipeline


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m q8020_cfd_metautil.sweep_worker "
              "<pipeline_args.json>", file=sys.stderr)
        sys.exit(1)

    args_file = Path(sys.argv[1])
    with open(args_file, encoding="utf-8") as f:
        args = json.load(f)

    # Convert case_dir and script_dir back to Path objects
    args["case_dir"] = Path(args["case_dir"])
    args["script_dir"] = Path(args["script_dir"])

    result = run_case_pipeline(**args)

    result_file = Path(args["case_dir"]) / "pipeline_result.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
