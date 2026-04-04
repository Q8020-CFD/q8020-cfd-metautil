# q8020-cfd-metautil

Metadata capture, harvesting, and analysis for quantum CFD experiments.

## Metadata schema

Eight top-level sections, each stored as a list of dicts in the
assembled JSON:

| Section | Required fields | Content |
|---|---|---|
| **experiment** | `name` (ID, timestamp, user auto-generated) | Run identification |
| **case** | `name` + `**extras` | Problem definition (e.g. matrix, RHS, dimension) |
| **code** | `algorithm`, `entry_point` | Algorithm, environment, library versions |
| **backend** | (extracted from object) | Execution target -- simulator or hardware |
| **artifacts** | -- | Circuit details, transpilation, file inventory |
| **exec_stats** | -- | Timing, exit code, success flag |
| **results** | -- | Solution vectors, per-iteration data |
| **analysis** | -- | Fidelity, error norms, quality metrics |

The first four sections are expected for a complete record; the last
four are optional.  Within each section very few fields are
structurally required -- builders accept `**extras` so the schema
stays open to algorithm- and backend-specific data.

Flattened keys follow the pattern `section.index.field`, e.g.
`backend.0.name`, `results.0.fidelity`.

For the full field-by-field schema specification, see
[docs/experiment_metadata_schema.md](docs/experiment_metadata_schema.md).
For LLM-based metadata review instructions, see
[docs/llm-metadata-review.md](docs/llm-metadata-review.md).

## Two instrumentation models

### Open box -- direct instrumentation

When you control the solver source, call metautil helpers directly to
capture rich metadata from live objects:

```python
from q8020_cfd_metautil.meta_fragment import (
    make_backend_meta,
    make_code_meta,
    write_backend,
    write_results,
)

# Pass a Qiskit backend object; extracts noise model, gate errors,
# coupling map, T1/T2, qubit count automatically.
backend_dict = make_backend_meta(backend, shots=1024)
write_backend(case_dir, backend_dict)

# Pass a transpiled circuit; extracts gate counts, depth, width.
write_artifacts(case_dir, {"transpile_passes": transpile_info})

# Algorithm and environment captured together.
code_dict = make_code_meta("hhl", __file__, run_args=vars(args))
```

`make_backend_meta` auto-detects AerSimulator and IBMBackend via lazy
imports -- no hard dependency on either.  The `**extras` catch-all on
every builder lets algorithm-specific fields ride along.

### Closed box -- harvest from outputs

When the solver is opaque (third-party script, compiled binary),
metadata is reconstructed from observable outputs after the run.

The harvester can pick up on its own:

- **Fragment files** the solver wrote (`q8020_{section}_{id}.json`)
- **stdout JSON** parsed into a `results` fragment
- **Artifact inventory** -- file listing with size, mtime, extension

That is not enough for a complete metadata record.  The required
sections -- **experiment**, **case**, **code** -- cannot be inferred
from outputs alone.  Someone has to write them:

- An **orchestrator** (sweeper, `q8020-run`, a wrapper script) writes
  them from config parameters or CLI args before/after the solver run.
- A **`--harvester` module** that knows the solver's conventions can
  reconstruct them.  The module exposes
  `generate_metadata(outdir, experiment_id, write_dir)` and writes
  whatever fragments it can extract.

Without either path, `q8020-harvest` assembles what it finds but the
metadata file will be incomplete.

Example: harvesting a black-box CFD solver with a custom harvester.
The solver wrote its outputs to `/data/runs/2025-11-11/` (many case
subdirs).  We harvest into a clean destination, leaving the source
untouched:

```bash
q8020-harvest \
    --source /data/runs/2025-11-11 \
    --harvester ./fvm_euler_1d_solver_harvester.py \
    --dest /tmp/harvest-out
```

The harvester module discovers case dirs recursively under `--source`.
For each case dir it calls the harvester's
`generate_metadata(outdir, experiment_id, write_dir)`, which reads
solver artifacts (CSVs, logs, etc.) and writes fragment files to
`--dest`.  Then `q8020-harvest` rolls up all fragments per case into
`q8020_metadata_{experiment_id}.json`.

Without `--dest`, fragments and the assembled metadata are written
alongside the source files.

## Arguments

`args.py` provides argparse argument groups for quantum experiments.
Add them individually (`add_noise_args()`) or as a bundle
(`add_standard_quantum_args()`).

## Running

`q8020-run` wraps a solver script, captures metadata, and organizes
output:

```
~/q8020/{workflow_id}/{case_name}/
```

```bash
q8020-run python solver.py --dim 4 --shots 1024
q8020-run --output-dir /proj/run1 python solver.py --dim 4
q8020-run --workflow sweep1 --case run1 python solver.py
```

## Sweeper

`q8020-sweep` runs parameter sweeps from a TOML config.  List-valued
parameters are expanded into individual cases.  The sweeper captures
environment snapshots before and after each case, writes all fragments,
and records overhead timing separately from solver time.

```bash
q8020-sweep sweep_config.toml
q8020-sweep sweep_config.toml --dry-run
q8020-sweep sweep_config.toml --groups ideal shots
q8020-sweep sweep_config.toml --set _output_dir=/tmp/test
```

`--set KEY=VALUE` overrides any `[global]` key before the sweep
runs.  Repeatable (`--set _output_dir=/tmp --set _slurm=false`).
Values are coerced to bool/int/float where possible, otherwise kept
as strings.  Combined with `${VAR}` templates, `--set` lets you
parameterize a TOML without editing it.

### TOML format

A sweep config has a `[global]` section and one or more **group**
sections.  Each group becomes one or more cases.  Global parameters are
inherited by all groups; group-level keys override globals.

```toml
[global]
_output_dir = "~/q8020"
_script = "source .venv/bin/activate && python solver.py"
_inject_outdir = "-outdir"
"-nelem" = 5
"-time_scheme" = "BDF1"

[ideal]
# inherits all global params, no overrides

[shots]
"-shots" = 1024
```

Parameters whose keys start with `-` are passed as CLI args to the
solver (e.g. `"-nelem" = 5` becomes `-nelem 5`).  Parameters whose
keys start with `_` are sweeper directives and are never passed to the
solver.

#### List expansion

Any parameter with a list value is expanded into a cross-product of
cases:

```toml
[sweep]
"-cfl" = [1, 5, 10, 25]
"-shots" = [1024, 8192]
```

This produces 8 cases (4 CFL values x 2 shot counts).

#### Template variables (`${VAR}`)

Any string value in the TOML may contain `${VAR}` tokens.  After
`--set` overrides are applied, the sweeper resolves these from
`[global]` params.  This works anywhere — in `_script`, solver
args, postproc commands, group-level overrides, etc.

```toml
[global]
_output_dir = "~/q8020"
_solver_dir = "./fvm_euler_1d_solver/.venv"
_script = """\
source ${_solver_dir}/bin/activate && \
python solver.py\
"""
```

Override at the CLI:

```bash
q8020-sweep config.toml --set _solver_dir=/opt/venvs/solver
```

Unresolved tokens (no matching global key) are left as-is.

#### Sweeper directives (`_` keys)

**Execution:**

| Key | Type | Default | Description |
|---|---|---|---|
| `_script` | string | -- | Shell command to run.  May include `&&`, pipes, etc. |
| `_env` | string | -- | Path to venv; auto-activated before the solver command and snapshotted before/after each case |
| `_output_dir` | string | `./sweep_results` | Root output directory |
| `_run_mode` | string | `sequential` | `sequential` or `parallel` |
| `_case_timeout` | int/null | none | Per-case timeout in seconds |
| `_trials` | int | 1 | Replicate each case N times (nested dirs) |

**Injection (passing sweep context to the solver):**

| Key | Type | Description |
|---|---|---|
| `_inject_outdir` | string | CLI flag name for case output directory |
| `_inject_experiment_id` | string | CLI flag name for the experiment ID |
| `_inject_workflow_id` | string | CLI flag name for the workflow ID |

For example, with:

```toml
_inject_outdir = "-outdir"
_inject_experiment_id = "-exp"
_inject_workflow_id = "-wf"
```

The sweeper appends `-outdir /path/to/case_dir -exp a1b2c3d4 -wf
_f8e9a0b1` to the solver command.

**Hooks (pre/post-processing):**

| Key | Type | Description |
|---|---|---|
| `_case_preproc` | str or list[str] | Commands run before each case |
| `_case_postproc` | str or list[str] | Commands run after each case |
| `_group_postproc` | str or list[str] | Commands run after all cases in a group |
| `_final_postproc` | str or list[str] | Commands run after the entire sweep |

Hook commands run in the **sweeper's own environment**, not the
solver's `_env` venv.  The sweeper adds `script_dir` to `PYTHONPATH`
but does not activate any venv for hooks.  If a hook needs packages
from a specific venv, use a multi-line command with `&&` to activate
it:

```toml
_case_postproc = [
    "source ./analysis/.venv/bin/activate && python harvester.py"
]
```

Commands containing shell operators (`&&`, `||`, `|`, `` ` ``,
`$(...)`) are automatically wrapped in `bash -c`.  Each hook command
receives a JSON context file as its last argument, containing the
`case_id`, `experiment_id`, `workflow_id`, `case_dir`, and `params`.

**SLURM:**

When `_slurm = true`, the sweeper generates an sbatch script and submits
it automatically.  Use `--dry-run` to generate the script without
submitting.

| Key | Type | Default | Description |
|---|---|---|---|
| `_slurm` | bool | false | Enable SLURM sbatch generation |
| `_slurm_interactive` | bool | false | Use srun inside an active salloc |
| `_slurm_project` | string | -- | SLURM account/project ID |
| `_slurm_partition` | string | `batch` | SLURM partition |
| `_slurm_walltime` | string | `00:30:00` | Walltime limit |
| `_slurm_poll_wait` | float | 5 | Seconds to poll sacct after submit |
| `_slurm_cores_per_task` | int | 1 | CPUs per srun task |
| `_slurm_exclusive_node` | bool | false | Pin each case to its own node |
| `_slurm_cores_per_node` | int | 64 | Cores per node (packed mode only) |
| `_slurm_pack_venvs` | list | [] | Venv paths to auto-tar and broadcast to NVMe |

**Node allocation:**  By default the sweeper packs multiple cases per
node (`tasks_per_node = cores_per_node // cores_per_task`).  Set
`_slurm_exclusive_node = true` to request one node per case instead.
When exclusive mode is on, `_slurm_cores_per_task` and
`_slurm_cores_per_node` are ignored.

**NVMe venv broadcast:**  For large-scale runs, set `_slurm_pack_venvs`
to a list of venv directories.  Before submission the sweeper tars each
venv (with a freshness check against `lib/` mtime), and the sbatch
script broadcasts the archives to each node's NVMe burst buffer via
`sbcast`.  The solver and postproc commands are automatically rewritten
to activate from NVMe instead of the shared filesystem.

```toml
_slurm_pack_venvs = ["./fvm_euler_1d_solver/.venv", "./.venv"]
```

#### Multi-stage sweeps

For pipelines where one stage feeds into the next, use `[stage.*]`
sections instead of top-level groups:

```toml
[global]
_output_dir = "~/q8020"
_slurm = true

[stage.solve]
_script = "python solver.py"
[stage.solve.case_a]
"-nelem" = 5

[stage.postprocess]
_script = "python analyze.py --input {{stages.solve.run_dir}}"
[stage.postprocess.case_a]
"-format" = "pdf"
```

Each stage runs sequentially.  SLURM stages are chained with
`--dependency=afterok:<job_id>`.  The `{{stages.<name>.<prop>}}`
syntax substitutes values from completed stages (`run_dir`,
`slurm_job_id`).

### Output structure

```
<_output_dir>/<date>/<_workflow_id>/
    q8020_sweep_meta<wf_id>.json    # overall sweep metadata
    q8020_expanded_cases.json       # all parameter combinations
    q8020_<config>.toml             # copy of input TOML
    <experiment_id>/                # one per case
        q8020_params_<exp_id>.json
        q8020_stdout_<exp_id>.txt
        q8020_stderr_<exp_id>.txt
        q8020_metadata_<exp_id>.json
        ...solver artifacts...
```

When `_trials > 1`, trial directories are nested:

```
<experiment_id>/
    trial_0_<trial_exp_id>/
    trial_1_<trial_exp_id>/
    ...
```

## Analysis tools

### q8020-metakeys

Report flattened metadata key coverage across cases.  Three output
modes:

```bash
# Full: every key with count and pct (JSON, default)
q8020-metakeys sweep/

# Full: plain-text table
q8020-metakeys sweep/ --table

# Names: one full dotted key per line, sorted
q8020-metakeys sweep/ --mode names

# Combined: strip numeric indices, deduplicate
q8020-metakeys sweep/ --mode combined

# Combined + JSON: canonical keys with grouped full-key detail
q8020-metakeys sweep/ --mode combined --json
```

Filters: `--skip PAT` (repeatable), `--section SEC`, `--common`
(100% coverage only).
