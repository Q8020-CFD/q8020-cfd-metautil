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
