# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`q8020-cfd-metautil` is metadata capture, harvesting, and analysis tooling for
quantum CFD experiments. It has no single "application" — it is a toolbox of a
pure metadata core, a TOML-driven experiment sweeper, a lightweight solver
framework, and analysis/grooming utilities. Read `README.md` first; it is the
canonical, detailed reference and this file only summarizes the big picture and
the non-obvious commands.

## Commands

Editable install (uses `src/` layout; entry points defined in `pyproject.toml`):

```bash
pip install -e .
```

Run tests (pytest is a declared dependency). The only conventional pytest
module is `tests/test_slurm_postproc.py` — the rest of `tests/` is example
configs, corpus data, and standalone scripts, not a pytest suite:

```bash
pytest tests/test_slurm_postproc.py
pytest tests/test_slurm_postproc.py::<test_name>   # single test
```

Console scripts installed by `pip install -e .` (see `pyproject.toml
[project.scripts]`):

- `q8020-sweep` / `q8020-sweeper` → `sweep:main`
- `q8020-harvest` → `harvest:main`

Note: `compare`, `metakeys`, and the corpus tools have `main()` entry points but
are **not** installed as console scripts (commented out in `pyproject.toml`).
Run them as modules, e.g. `python -m q8020_cfd_metautil.metakeys <dir>`.
`setup.cfg` lists a stale `q8020-metakeys` script — `pyproject.toml` is the
source of truth.

Lint config lives in `pyproject.toml`: pyright expects a `.venv`; pylint runs
with many checks disabled (invalid-name, line-length, missing-docstring, etc.).

## Architecture

### The metadata model (the core idea)

Metadata is JSON with **eight top-level sections** (`experiment`, `case`,
`code`, `backend`, `artifacts`, `exec_stats`, `results`, `analysis`), each a
list of dicts. Flattened keys follow `section.index.field` (e.g.
`backend.0.name`). The schema is deliberately loose: very few fields are
required, and every builder accepts `**extras` so section content stays open to
algorithm- and backend-specific data. Full field reference:
`docs/experiment_metadata_schema.md`.

Metadata flows through a **four-stage lifecycle, where each stage writes new
files and never overwrites a prior stage's**:

1. **Capture** — per-section fragment files `q8020_<section>_N.json` are written.
2. **Assemble** — `q8020-harvest` rolls all fragments in a case dir into one
   `q8020_metadata_<id>.json`.
3. **Analyze** — `metakeys` / `compare` report key coverage and diffs across
   assembled cases.
4. **Groom** — later fixes are added as **metapatches** (dated
   `metapatches/<date>/` deltas composed over the base at read time), never as
   edits. Motto: "record the raw tracks, fix it in the mix." See
   `docs/metapatches.md`.

### Two instrumentation models

- **Open box** — you control the solver source and call `meta_fragment` builders
  (`make_code_meta`, `write_backend`, …) directly on live objects.
- **Closed box** — the solver is opaque; a `--harvester` module reconstructs
  fragments from outputs. A harvester exposes
  `generate_metadata(outdir, experiment_id, write_dir)`. `experiment`/`case`/
  `code` cannot be inferred from outputs alone — an orchestrator or harvester
  must write them.

### `meta_fragment.py` — the pure core

Defines section constants (`VALID_SECTIONS`, `SINGLETON_SECTIONS`,
`MULTI_SECTIONS`), ID generation, dict builders, fragment I/O, and the
**`BackendMeta`** TypedDict contract. Crucially, metautil is a **pure core**: it
defines and reads/writes the backend-metadata shape but does **not** know how to
extract it from any vendor's backend object. That keeps Qiskit and other vendor
SDKs out of this package's dependencies — vendor extraction lives in sibling
packages (e.g. `q8020_backend_utils.ibm`). Preserve this boundary: do not add
vendor SDK imports to the core.

### `sweep.py` — the sweeper (largest, most complex module, ~3300 lines)

Reads a TOML config and expands it into cases. Key conventions to know before
touching it:

- TOML keys starting with `-` are passed as CLI args to the solver; keys
  starting with `_` are **sweeper directives** and never reach the solver.
- List-valued parameters expand into a cross-product of cases.
- `${VAR}` template tokens resolve from `[global]`; `--set KEY=VALUE` overrides
  globals before expansion.
- `[stage.*]` sections define multi-stage pipelines chained via
  `{{stages.<name>.<prop>}}` substitution and (under SLURM)
  `--dependency=afterok`.
- SLURM support generates and submits an sbatch script (`--dry-run` to generate
  only), with per-node case packing and optional NVMe venv broadcast via
  `sbcast`.

Supporting modules: `sweep_worker.py` (runs a single case, used for parallel
execution) and `sweep_postproc.py` (the dependent SLURM post-processing job).
The full directive tables and output directory layout are in `README.md`.

### `solverfw/` — pluggable solver framework

Small abstract base classes factoring out the repeated scaffolding of
time-marching PDE solvers (`SolverConfig`, `Grid`/`Grid1D`, `State`/
`DenseState`, `SpatialOperator`, `TimeIntegrator`/`ForwardEuler`,
`LinearSystemSolver` with LU/GMRES/Null, `MainLoop`, `PostProcessor`). It has
**no metadata dependency** — capture is layered on via the `PostProcessor`
observer hook that `MainLoop` calls each step. An application supplies its own
`SpatialOperator`. Spec: `docs/SPEC-solverfw.md`.

### Analysis & corpus tooling

- `metakeys.py` — reports flattened key coverage across many assembled cases
  (modes: full/names/combined, JSON or `--table`).
- `compare.py` — diffs metadata across cases.
- `tests/corpus/` — `ingest.py` walks `<root>/<date>/<wf>/<case>/q8020_metadata_*.json`
  into a `corpus_index.json`; `viz.py` renders a Case×Algorithm×Backend cube.
  `tests/corpus/corpus_index.json` is a real ~1000-record index used as data.

## Non-obvious layout notes

- `Z-KEEP/` and `tests/metapatches/` hold **internal, deliberately un-shipped**
  scratch scripts (analysis one-offs, grooming utilities). They are not
  user-facing CLI and are kept out of the package on purpose — don't promote
  them to console scripts.
- `tests/examples/*.toml` are runnable sweep configs used as references/smoke
  tests; `tests/examples/smoke_test_solver.py` is a minimal solver stub.
- `tests/quantum-code-to-backend/` contains Jupyter notebooks and transpilation
  experiments (Qiskit/Cirq/Bloqade), separate from the main package.
