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
q8020-sweep sweep_config.toml ideal shots     # only these groups
q8020-sweep sweep_config.toml _smoke          # _-prefixed group, by name
q8020-sweep sweep_config.toml --set _output_dir=/tmp/test
```

```
usage: q8020-sweep [-h] [--dry-run] [--dry-run-materialize]
                   [--set KEY=VALUE] toml_file [groups ...]
```

**Where it is installed.**  `q8020-sweep` is a console script of this
package and is usually not on `PATH`.  Call the copy in this checkout's
venv, `<checkout>/.venv/bin/q8020-sweep`, by its path.  With an editable
install it runs whatever branch the checkout is on.  Run the fvm sweeps
from the checkout's parent directory (the one that holds the solver,
experiments and metautil repos side by side), because their TOMLs use
`./`-relative paths.  Example, on one laptop (`<checkout>` =
`/Users/agallojr/proj/src/q8020/q8020-cfd-metautil`):

```bash
cd /Users/agallojr/proj/src/q8020
./q8020-cfd-metautil/.venv/bin/q8020-sweep \
  q8020-cfd-experiments/aux/fvm_euler_1d_nozzle/fvm_cfl_sweeps.toml \
  --dry-run --set _postproc_venv=./q8020-cfd-metautil/.venv
```

**Groups** are positional arguments after the TOML (there is no
`--groups` flag).  With no group named, every group runs except those
whose name starts with `_`; a `_`-prefixed group (e.g. `_smoke`) runs
only when named explicitly.  An unknown group name is an error.

**`--set KEY=VALUE`** overrides or adds a `[global]` key before
`${VAR}` template expansion.  Repeatable (`--set _output_dir=/tmp
--set _slurm=false`).  Coercion: `true`/`false` (any case) become bool,
then int is tried, then float, otherwise the value stays a string.
An overridden key wins over group-level and per-case values of the same
key (a list-valued group key still expands to the same number of cases,
all carrying the override).  Combined with `${VAR}` templates, `--set`
lets you parameterize a TOML without editing it.

Solver keys and dashes: a bare key (`--set shots=0`) is matched to a
dashed variant (`"-shots"` / `"--shots"`) only if that variant exists in
`[global]`.  Otherwise the bare key becomes `--shots`, and the group's
`"-shots"` is dropped from the command.  In the fvm TOMLs `"-shots"` is
group-level, so `--set shots=0` renders `--shots 0`, which is wrong for
a single-dash solver.  For a group-level dashed key, give the dashed
name in the `=` form (`--set=-shots=0`; `--set -shots=0` is rejected by
argparse because the value starts with `-`), which renders `-shots 0` in
every case.

**`--dry-run`** prints every case's solver command and its pre/postproc
commands (sequential and parallel mode alike; in SLURM mode also the
venvs it would pack and the `#SBATCH` header) and writes nothing under
`_output_dir`.  With `_slurm_pack_venvs`, the commands are printed after
the NVMe venv rewrite (`/mnt/bb/$USER/venv_<i>`), i.e. as the compute
node runs them and as `--dry-run-materialize` writes them.  The
`Would launch: ... pipeline_args.json` lines name a file that a plain
dry run does not write; they are marked as such.  `_env_exports` are
listed too (`Env export:` lines locally, the sbatch `export` lines
under SLURM).
**`--dry-run-materialize`** (implies `--dry-run`)
additionally writes the run dir, the case dirs with `pipeline_args.json`,
the `q8020_sweep.sbatch` file (SLURM mode, with NVMe-rewritten commands)
and `q8020_sweep_invocation.json`, for inspecting what a real launch
would read; it still never runs the solver, a worker, `sbatch`, or packs
a venv.  (Behavior change 2026-10-15: before, a parallel or SLURM
`--dry-run` always wrote case dirs, `pipeline_args.json` and the sbatch
file.)

**Command-line provenance.**  Every sweep records how it was launched in
`<run_dir>/q8020_sweep_invocation.json` (written when the run dir is
created, so it survives a crash) and under the `invocation` key of
`q8020_sweep_meta<wf_id>.json`.  A multi-stage sweep writes it once,
in the base run dir, and embeds it once in the combined meta; each
stage's results carry an `invocation_ref` path instead.  Keys:
`command_line` (`sys.argv`,
shell-quoted), `argv`, `python_executable`, `toml_path` (absolute) and
`toml_path_resolved` (symlinks resolved), `cwd`, `groups`, `overrides`
(the raw `--set` strings, before coercion), `start_time_utc` (ISO 8601),
`user` (username, hostname) and `metautil`: `version`, and `commit`,
`branch`, `dirty` with `commit_source: "git"` only when the running code
is the checkout itself (an editable install per `direct_url.json`, or
the module lives under the repo's `src/`).  A copy installed into
site-packages records `commit: null, commit_source: "installed"`, even
if its venv sits inside the repo; `"unavailable"` means editable but no
git.

**`_postproc_venv` check.**  If the TOML sets `_postproc_venv` (the
venv a `_case_postproc` command activates, e.g. `source
${_postproc_venv}/bin/activate && ...`) to a directory that does not
exist, a **real run stops before writing or launching anything**, with
an error naming the configured path, its absolute form and the remedy
(`--set _postproc_venv=<path>`).  A **dry run** only warns and shows the
commands with the running interpreter's environment (`sys.prefix`, the
venv `q8020-sweep` was started from) so the expansion can still be
inspected.  The fvm TOMLs set `_postproc_venv = "./.venv"`, a Frontier
umbrella venv that does not exist on the laptop; there, pass
`--set _postproc_venv=./q8020-cfd-metautil/.venv` (example above).

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
it automatically.  Use `--dry-run` to print the `#SBATCH` header
without writing or submitting anything, or `--dry-run-materialize` to
also write the script (and the case dirs) without submitting.

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
    q8020_sweep_invocation.json     # command line, cwd, --set, commit
    q8020_code_provenance.json      # which solver code ran (see below)
    code/                           # only when needed: patch / source archive
    q8020_expanded_cases.json       # all parameter combinations
    q8020_<config>.toml             # copy of input TOML
    <experiment_id>/                # one per case
        q8020_params_<exp_id>.json
        q8020_stdout_<exp_id>.txt
        q8020_stderr_<exp_id>.txt
        q8020_metadata_<exp_id>.json
        ...solver artifacts...
```

The sweep meta `timestamp` field is UTC ISO 8601
(`2026-10-15T03:30:00Z`); before 2026-10-15 it was local time as
`%Y%m%d_%H%M%S`.

Known issue (not fixed): the sweep meta file name is
`q8020_sweep_meta{workflow_id}.json` and the workflow id carries a
leading `_`, so on disk it is `q8020_sweep_meta_<8hex>.json` (e.g.
`q8020_sweep_meta_52d087d1.json`).  That name matches the fragment-file
pattern (`FRAGMENT_PATTERN` in `meta_fragment.py`) when the 8-hex id
happens to be all decimal digits (about 2.3 % of sweeps).  `metakeys` /
`q8020-harvest` directory walks then treat the run dir as a case dir
and do not descend into the real case dirs.
`q8020_sweep_invocation.json` has no id in its name, so it cannot match.

`<date>` is the sweep start date in **UTC** (`yyyy-mm-dd`).  Behavior
change 2026-10-15: earlier sweeps used the local date, so e.g. the
Frontier run submitted 2026-04-05T00:10Z landed under `2026-04-04/`.

When `_trials > 1`, trial directories are nested:

```
<experiment_id>/
    trial_0_<trial_exp_id>/
    trial_1_<trial_exp_id>/
    ...
```

#### Solver code provenance

Once per sweep (at launch, so a SLURM sweep records the code as
submitted), the sweeper identifies the solver code dir (the directory of
the `.py` file in `_script`) and writes `q8020_code_provenance.json`:

- **git checkout** (worktrees included): commit, branch, `describe`,
  remote URL with any `user:token@` stripped, and dirty state.  A clean
  tree stores nothing else.  A dirty tree also saves its tracked diff as
  `code/<commit>-<hash>.patch` and its untracked files as
  `code/untracked-<hash>.tar.gz`.  The commit is always recorded; only
  these extras are capped: a diff over 20 MB is hashed but not saved,
  more than 5000 untracked files are counted but not hashed, and over
  100 MB of untracked files are hashed but not archived.  The record
  keeps the untracked count and the first 50 names.
- **not a git checkout** (e.g. a Docker image built without `.git`): a
  sha256 over the sorted source files (`.venv`, `__pycache__`, `.git`,
  `output/`, `build/` and similar skipped; files over 5 MB listed, not
  hashed) and one `code/<hash>.tar.gz` source archive for the whole
  sweep (skipped above 100 MB of source).  Same code gives the same hash.
- either way, sha256 of `uv.lock`, `pyproject.toml`, `requirements*.txt`
  and similar dependency files.

Each case's `q8020_sweep_code_<exp_id>_0.json` (and so the `code`
section of its merged metadata) carries a compact `code_provenance`
reference (commit or content hash, patch/archive paths, dependency file
hashes), not a copy of the archive.  Provenance problems are recorded in
an `error` field and never fail the sweep.

`q8020_env_before/after_<exp_id>.json` snapshot the solver's packages:
`_env` if set, otherwise the venv the command activates
(`source <venv>/bin/activate`, marked `"inferred_from": "command"`),
otherwise the sweeper's own process.  Before 2026-10-28 a sweep without
`_env` always recorded the sweeper's process, not the solver's venv.

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
