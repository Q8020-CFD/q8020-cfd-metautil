# 8020-CFD-metautil

Helpers for metadata capture &amp; management.

## Arguments

args.py contains a set of argument groups for quantum experiments. You can add them to your script individually (e.g., add_noise_args_()) or as a complete group (e.g., add_standard_quantum_args()). argparse is used.


## Output

We want to capture metadata about the experiment in a consistent way - output.py provides a set of helper functions for building structured dicts. create_experiment_output() assembles the complete experiment output into a nested dict ready for JSON serialization. The main pieces of that structure - case, code, backend - each come with their own helper functions (make_case(), make_code(), make_backend()). 

Other structures may be algorithm or backend-specific - results, metrics. This is expected. In some places the catch-all "**extras" argument is used for this purpose.


## Running

The run.py script provides a wrapper for running a quantum experiment and capturing metadata. The script may or may not use the args.py argument groups or other q8020 helpers, but it should always use the output.py helper functions to build the experiment data/metadata so it can be as regular as possible.

The user script is free to dump this metadata to a file or write it to stdout. If the latter, the run.py script will handle the rest. The output directory is structured as follows:

```
~/q8020/{workflow_id}/{case_name}/
```

The run.py script will generate a workflow ID and case name if they are not specified.

```bash
# Basic run (auto-generates workflow ID and case name)
q8020-run python some_script.py --arg1 value --arg2 value

# Custom output directory (e.g., project share)
q8020-run --output-dir /proj/myProjId python some_script.py --arg1 value

# Specify workflow and case names
q8020-run --workflow my_experiment --case run1 python some_script.py --arg1 value
```

## Sweeper

It would be common to want to sweep over a range of parameters. The sweep.py script provides a simple way to do this. You point it at a TOML config file that defines the sweep parameters. Output is saved to the same directory structure as the run.py script.

```bash
q8020-sweep sweep_config.toml
```

