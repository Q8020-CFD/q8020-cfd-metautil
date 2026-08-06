# q8020-cfd-metautil


Metadata capture, harvesting, and analysis for quantum CFD experiments.

Here are helper methods to snap useful info from common objects, e.g. a quantum backend, a virtual environment, the running of a solver, and iteration therein.

An "open box" solver can call these metadata capture methods directly. A "closed box" solver can run on its own, and later we can "harvest" the metadata by inspection of the run's outputs. For obvious reasons a "harvester" is usually specific to a solver code.

The "sweeper" is a tool which reads a description of a solver run - in TOML format - and runs it. The description may expand out into a set of runs. It can have post-processing steps (e.g. make a results diagram), and readily utilize virtual environments. 

The solver framework "solverfw" is a recognition that in this study of CFD codes many show a repeated pattern of steps - e.g. looping to convergence, time stepping, circuit quantum state preparation (of various kinds). A lightweight framework can permit a focus on the important algorithms rather than repeated scaffolding. 

Other tools such as metadata search live here too (mostly WIP).


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

### Lifecycle

Metadata moves through four stages; each writes files, none overwrites a
prior stage's:

1. **Capture** -- fragments `q8020_<section>_N.json` are written, either
   by the solver itself (open box) or by an orchestrator/harvester around
   it (closed box).
2. **Assemble** -- `q8020-harvest` rolls all fragments in a case dir up
   into one `q8020_metadata_<id>.json`.
3. **Analyze** -- `metakeys` (and other WIP tools) report key coverage
   across many assembled cases for search and comparison.
4. **Groom** -- later fixes (reviews, alignments, closed-box gap-fills)
   are added as **metapatches**, never edits: dated `metapatches/<date>/`
   deltas that a consumer composes over the base at read time.

The scheme is deliberately loose: cases, codes, and backends vary, and
records may come from closed boxes or older tool versions.  Required
fields are few; `**extras` keeps every section open.  "Record the raw
tracks, fix it in the mix."

See also: [docs/experiment_metadata_schema.md](docs/experiment_metadata_schema.md)
(full field-by-field schema),
[docs/llm-metadata-review.md](docs/llm-metadata-review.md) (LLM-based
review), and [docs/metapatches.md](docs/metapatches.md) (write-once
grooming).

## Two instrumentation models

### Open box -- direct instrumentation

When you control the solver source, call metautil helpers directly to
capture metadata from live objects:

```python
from q8020_cfd_metautil.meta_fragment import (
    make_code_meta,
    write_backend,
    write_code,
    write_artifacts,
)

# Algorithm and environment captured together.
code_dict = make_code_meta("hhl", __file__, run_args=vars(args))
write_code(case_dir, code_dict)

# Circuit / transpile details ride along as free-form extras.
write_artifacts(case_dir, {"transpile_passes": transpile_info})
```

metautil is a **pure core**: it defines the `BackendMeta` shape (see
`meta_fragment.BackendMeta`) and reads/writes it, but does not extract it
from any vendor's backend object -- that keeps Qiskit and friends out of
the core's dependencies.  Vendor extraction lives in a sibling package:

```python
# One-line import for the IBM extractor; no Qiskit dep in metautil itself.
from q8020_backend_utils.ibm.backend_meta import make_backend_meta

backend_dict = make_backend_meta(backend, shots=1024)  # T1/T2, gate/
write_backend(case_dir, backend_dict)                  # readout error, etc.
```

The `**extras` catch-all on every builder lets algorithm- and
backend-specific fields ride along.

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

Expansion is **recursive**: a variable's value may itself contain
`${VAR}` tokens, which are resolved in turn (e.g.
`_script = "${_launch} python solver.py"` where
`_launch = "custom_tool -n ${_ranks}"`).  A reference cycle
(`a = "${b}"`, `b = "${a}"`) raises an error naming the offending keys.

#### Sweeper directives (`_` keys)

**Execution:**

| Key | Type | Default | Description |
|---|---|---|---|
| `_script` | string | -- | Shell command to run.  May include `&&`, pipes, etc. |
| `_env` | string | -- | Path to venv; auto-activated before the solver command and snapshotted before/after each case |
| `_env_exports` | table | -- | Environment variables for the solver (see below) |
| `_output_dir` | string | `./sweep_results` | Root output directory |
| `_run_mode` | string | `sequential` | `sequential` or `parallel` |
| `_case_timeout` | int/null | none | Per-case timeout in seconds |
| `_trials` | int | 1 | Replicate each case N times (nested dirs) |

**Environment variables (`_env_exports`):**  Declare env vars once in the
TOML instead of chaining `export ... &&` inside `_script`, so the same
config runs locally and under SLURM:

```toml
[global._env_exports]
MPICH_GPU_SUPPORT_ENABLED = "1"
LD_LIBRARY_PATH = "/opt/rocm-6.2.4/lib:$LD_LIBRARY_PATH"
```

Under SLURM the variables are emitted as `export` lines in the generated
sbatch (after the sweeper's default `OMP_NUM_THREADS=1` etc., so your
values win); `$VAR` references are expanded by bash at runtime on the
compute node.  For local and `_slurm_interactive` runs the variables are
injected into the solver's process environment, with `$VAR` references
expanded against the current environment (unknown vars left verbatim —
e.g. `$SLURM_CPUS_PER_TASK` stays literal outside a SLURM job).
`_env_exports` is a **global-level** directive: the whole sweep shares
one environment (the sbatch is generated once per job).

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
| `_slurm_tasks_per_case` | int | 1 | MPI ranks per case; >1 marks cases as distributed (see below) |
| `_slurm_tasks_per_node` | int | 8 | Ranks per node for distributed cases |
| `_slurm_launcher` | string | `auto` | `auto` / `srun` / `none` — synthesized launcher for distributed cases (see below) |
| `_slurm_gpus_per_task` | int | -- | Adds `--gpus-per-task=N` to the synthesized launcher |

**Node allocation:**  By default the sweeper packs multiple cases per
node (`tasks_per_node = cores_per_node // cores_per_task`).  Set
`_slurm_exclusive_node = true` to request one node per case instead.
When exclusive mode is on, `_slurm_cores_per_task` and
`_slurm_cores_per_node` are ignored.

**Distributed (MPI) cases:**  `_slurm_tasks_per_case > 1` marks each case
as a multi-rank run: the job is sized for ONE case
(`nodes = ceil(tasks_per_case / tasks_per_node)`) and cases run
sequentially, because concurrent multi-rank job steps race in Cray
MPICH's PMI/shared-memory init.  The sweeper **synthesizes the launcher**
for you — `_script` stays a bare `python solver.py`, and the solver
command becomes:

```
srun -N <nodes> -n <tasks_per_case> --ntasks-per-node=<tasks_per_node> \
    [--gpus-per-task=<g>] --exclusive <_script>
```

`_slurm_launcher` controls this: `auto` (default) synthesizes unless
`_script` already contains its own `srun`/`mpirun`/`mpiexec` token
(legacy configs keep working); `srun` always synthesizes; `none` never
does.  With `--set _slurm=false` the same TOML runs locally — no
launcher, no sbatch.

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

## Solver framework (solverfw)

Many CFD codes in this study repeat the same scaffolding -- a time-march
loop, a spatial discretization, a linear solve, state I/O.  `solverfw`
factors that out as small abstract bases so an application focuses on its
algorithm, not the plumbing.  It has no metadata dependency; capture is
layered on via the `PostProcessor` observer hook.

| Class | Role |
|---|---|
| `SolverConfig` | Base config for an application |
| `Grid` / `Grid1D` | Computational grid (uniform 1-D provided) |
| `State` / `DenseState` | Solution state representation |
| `SpatialOperator` | Spatial discretization (abstract) |
| `TimeIntegrator` / `ForwardEuler` | Time advancement |
| `LinearSystemSolver` | `Ax=b` solve (`LU`, `GMRES`, `Null` provided) |
| `MainLoop` | Time-marching driver tying the above together |
| `PostProcessor` | Observer invoked per step (metadata, plots, checks) |

```python
from q8020_cfd_metautil.solverfw import (
    SolverConfig, Grid1D, DenseState, ForwardEuler, MainLoop,
)
```

An application supplies its own `SpatialOperator` (and optionally state,
integrator, linear solver); `MainLoop` drives to completion and calls the
`PostProcessor` each step, where per-step metrics land in the `analysis`
section.  See [docs/SPEC-solverfw.md](docs/SPEC-solverfw.md).

## Analysis tools

### metakeys

Report flattened metadata key coverage across cases.  Run as a module
(`python -m q8020_cfd_metautil.metakeys`; the `q8020-metakeys` console
script is not currently installed).  Three output modes:

```bash
# Full: every key with count and pct (JSON, default)
python -m q8020_cfd_metautil.metakeys sweep/

# Full: plain-text table
python -m q8020_cfd_metautil.metakeys sweep/ --table

# Names: one full dotted key per line, sorted
python -m q8020_cfd_metautil.metakeys sweep/ --mode names

# Combined: strip numeric indices, deduplicate
python -m q8020_cfd_metautil.metakeys sweep/ --mode combined

# Combined + JSON: canonical keys with grouped full-key detail
python -m q8020_cfd_metautil.metakeys sweep/ --mode combined --json
```

Filters: `--skip PAT` (repeatable), `--section SEC`, `--common`
(100% coverage only).
