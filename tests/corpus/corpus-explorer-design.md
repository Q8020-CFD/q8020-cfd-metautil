# Corpus Explorer: Design Notes

## The data

1337 metadata JSON files across two roots:
- `q8020-cfd-experiments/results/` — FVM Euler, Frontier QLSA, modular HHL
- `~/q8020/` — Burgers MPS/circuit sweeps, FVM solver runs

Each file follows the q8020 metadata schema with sections:
`case`, `code`, `backend`, `experiment`, `results`, `analysis`,
`artifacts`, `exec_stats`.  Schemas vary by experiment type but share
a common envelope.

### The three axes (Cases x Codes x Backends)

| Axis     | Key fields                                              | Count |
|----------|---------------------------------------------------------|-------|
| Case     | case.name, case.params (nelem, nx, ny, q, cfl, bc...) | ~30 distinct case families |
| Code     | code.algorithm (HHL, VQE, hhl), code.entry_point       | ~5 algorithms |
| Backend  | backend.name, backend.type, backend.noise, backend.nshots | ~17 backends |

### Fidelity / quality metrics (vary by experiment)

- `analysis.fidelity` — state fidelity (0..1)
- `analysis.l2_error_abs`, `analysis.l2_error_rel`
- `analysis.converged` — boolean
- `analysis.residual` — final residual norm
- `analysis.final_error_epsilon` — Burgers L2 error
- `analysis.sv_fidelity` — statevector fidelity


## Tool 1: Cube visualization

### Goal

A high-level interactive view of the experiment corpus organized as a
3D grid: **Case x Code x Backend**.  Each cell shows:
- Whether that combination exists (dot/bubble)
- Sizing metric: problem size (nelem, N=2^q, matrix_size)
- Color metric: fidelity or error relative to the 80/20 rule

### Approach

1. **Ingest**: Walk both result roots, parse all `q8020_metadata_*.json`
   into a flat DataFrame with normalized columns:
   - `case_family` — coarsened case name (strip trial/run suffixes)
   - `algorithm` — from code section
   - `backend` — from backend section
   - `problem_size` — nelem, N, matrix_size (whatever is available)
   - `fidelity` — best available quality metric, normalized to [0,1]
   - `wall_time_s` — from exec_stats
   - `date` — from experiment.timestamp
   - `path` — source file for drill-down

2. **Coarsen**: Group trials into (case_family, algorithm, backend)
   triples.  Compute per-group: count, mean fidelity, mean size,
   min/max fidelity.

3. **Visualize** (matplotlib first, Plotly later):
   - Bubble chart: x=backend, y=case_family, color=fidelity,
     size=problem_size.  One subplot per algorithm.
   - Or: 3D scatter with Plotly for rotation.
   - Annotate the 80/20 line: fidelity >= 0.8 AND
     problem_size >= 0.2 * industrial_reference.

4. **80/20 rule overlay**: Mark a threshold region.  For now,
   industrial reference sizes are:
   - FVM Euler: nelem=1000+ (our runs: 2-5)
   - Burgers: N=8192 (q=13) (our runs: 8-64)
   - Linear system: 1000+ dimension (our runs: 2-64)
   So "20% of industrial" would be ~200 elem / ~1600 grid / ~200 dim.
   All our runs are well below 20%, which is itself an important
   finding to visualize.


## Tool 2: Fuzzy keyword search

### Goal

Query the corpus by keywords that may not exactly match field names
or values across different experiment types.

### Use cases

- "which runs used noise?" — match backend.noise=True, backend names
  containing "fake_", or any calibration fields
- "condition number > 50" — search across case.condition_number,
  case.max_condition_number, artifacts.linear_system.condition_number
- "ibm hardware" — match backend.type="hardware" AND vendor="ibm",
  or backend.name containing "ibm_"
- "converged at nelem=5" — match analysis.converged=True AND
  case.nelem=5

### Approach

1. **Flatten**: Each metadata file becomes a bag of (path, value) pairs:
   `("case.condition_number_hermitian", 1.0)`,
   `("backend.name", "ibm_torino")`, etc.

2. **Index**: Build an inverted index:
   - Exact match on values (string, number, bool)
   - Trigram index on string values for fuzzy text match
   - Alias table for known synonyms:
     `condition_number` = `condition_number_hermitian`
     = `max_condition_number` = `kappa`

3. **Query language** (simple, not SQL):
   ```
   noise:true backend:ibm*
   condition_number:>50
   converged:true nelem:5
   fidelity:<0.5
   ```
   Each term is AND'd.  Glob and comparison operators supported.
   Fuzzy matching (edit distance <= 2) on field names when exact
   match fails.

4. **Output**: Table of matching cases with key columns, sortable.
   Link back to metadata path for drill-down.

### Alias / synonym alignment

The hardest part.  Different experiments use different names for
similar concepts:
- `nshots` vs `shots` — same thing
- `fidelity` vs `sv_fidelity` vs `fidelity_shots` — related but
  different (SV vs measurement-based)
- `l2_error_abs` vs `final_error_epsilon` — same concept, different
  experiments
- `backend.t1` vs `backend.gate_error` — different calibration metrics
  but both relate to noise quality

Strategy: maintain a YAML alias file that maps canonical names to
all known variants.  Start small, grow as we encounter new experiments.


## Implementation order

1. **Ingest script** (`corpus_ingest.py`): walk roots, parse all
   metadata into a single Parquet/CSV DataFrame.  This is the
   foundation for both tools.

2. **Cube plot** (`corpus_cube.py`): matplotlib bubble chart from
   the DataFrame.  Static PNG first, Plotly HTML later.

3. **Search CLI** (`corpus_search.py`): command-line query tool
   against the DataFrame.  Fuzzy matching via rapidfuzz or
   simple edit distance.

4. **Alias file** (`corpus_aliases.yaml`): canonical field mapping.

All in `q8020-cfd-experiments/analysis/corpus/`.
