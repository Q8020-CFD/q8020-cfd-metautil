# SPEC — solverfw

The solver framework inside `q8020-cfd-metautil`. A lightweight set
of abstract base classes and one driver loop that captures the
patterns repeated across the CFD solvers in this project (Burgers,
Euler-1D, etc.) so application packages focus on the algorithm, not
the scaffolding.

## 1. What problem solverfw solves

Sample PDE solvers in the q8020 collection end up doing the same
plumbing:

- Build a 1-D (sometimes 2-D) grid with a chosen boundary condition.
- Hold a state vector that may be dense, MPS, or a quantum circuit
  amplitude register.
- Compute a spatial RHS at the current state.
- Advance one time step (explicit, implicit, or operator-specific).
- Optionally solve a linear system inside that step.
- Write snapshots, log metrics, detect divergence, stop on
  convergence.
- Loop.

Solving also involves common quantum operations against common objects - e.g. using the IBM Aer simulator. These are also reusable utilities.  

`solverfw` extracts each of the major constructs into a small ABC, plus one driver
(`MainLoop`) that owns the time-marching skeleton and delegates the
physics. Application packages plug in their own pieces.

## 2. The piece parts

Module layout under `q8020_cfd_metautil.solverfw`:

| Module | Public surface | Role |
|---|---|---|
| `config` | `SolverConfig` (ABC) | dataclass base for run-time config (cfl, dt, n_steps, conv_tol, bc, method, output_dir, save_every, free-form `extra` dict). Subclasses add equation-specific fields. |
| `grid` | `Grid` (ABC), `Grid1D` (concrete) | spatial discretisation. `Grid1D.from_qubits(q)` builds an N=2^q grid for quantum solvers; `Grid1D.uniform(nelem)` builds an arbitrary uniform grid. |
| `state` | `State` (Protocol), `DenseState` | minimal contract: `to_dense()`, `copy()`, `shape`. Default impl wraps a numpy array; alternate states (MPS, statevector) implement the protocol. |
| `spatial` | `SpatialOperator` (ABC) | `compute_rhs(state, grid, config, t) -> np.ndarray` — the spatial discretisation (FD, FV, spectral). Optional `compute_timestep(state, grid, cfl)` for CFL-driven dt. |
| `time_integrator` | `TimeIntegrator` (ABC), `ForwardEuler` | `step(state, spatial_op, grid, config, dt, t) -> (new_state, metrics)`. `ForwardEuler` is the only built-in concrete integrator; subclass for RK/implicit/operator-specific schemes. |
| `linsys` | `LinearSystemSolver` (ABC), `LUSolver`, `GMRESSolver`, `NullLinearSystemSolver` | `solve(A, b) -> (x, metrics)`. Used by implicit integrators. The `Null` variant raises if called — useful as a "this integrator should never need this" assertion. |
| `postprocess` | `PostProcessor` (ABC), `NullPostProcessor` | observer pattern: `on_step(step, t, state, metrics)` after every step + `finalize(config, elapsed_s)` once at the end. |
| `loop` | `MainLoop` | the driver. Owns the time loop. Delegates everything physical. |

All public names are re-exported from `q8020_cfd_metautil.solverfw`.
Import path:

```python
from q8020_cfd_metautil.solverfw import (
    SolverConfig, Grid1D, DenseState, SpatialOperator,
    TimeIntegrator, ForwardEuler, MainLoop,
    LinearSystemSolver, LUSolver, GMRESSolver,
    PostProcessor, NullPostProcessor,
)
```

## 3. How they fit together

Data flow at run-time:

```
                        ┌──────────────┐
                        │ SolverConfig │  (cfl, dt, n_steps, method, ...)
                        └──────┬───────┘
                               │ read by
                               ▼
┌─────────┐     ┌───────────────────┐     ┌────────────────┐
│  Grid   │────▶│  SpatialOperator  │◀───▶│ State          │
└─────────┘     │  .compute_rhs     │     │ (dense / mps / │
                └─────────┬─────────┘     │  amplitudes)   │
                          │ rhs           └────────┬───────┘
                          ▼                        │
                ┌───────────────────┐              │
                │  TimeIntegrator   │              │
                │  .step()          │◀─ state in ──┘
                │   ↳ may call:     │
                │     LinearSystem  │
                │     Solver.solve  │
                └─────────┬─────────┘
                          │ new_state, metrics
                          ▼
                ┌───────────────────┐
                │  MainLoop.run()   │  (owns the for-loop)
                │   ↳ append snap   │
                │   ↳ post.on_step  │
                │   ↳ check NaN     │
                │   ↳ check converg │
                └─────────┬─────────┘
                          │ on finish
                          ▼
                ┌───────────────────┐
                │  PostProcessor    │
                │  .finalize()      │
                └───────────────────┘
```

`MainLoop.run()` ([loop.py:25](../src/q8020_cfd_metautil/solverfw/loop.py:25))
is the single source of truth for the time-stepping skeleton. It
calls `integrator.step()` `n_steps` times; after each step it fires the
post-processor's `on_step`, checks for `NaN` (divergence — pads with NaN
and breaks), checks for a `residual_ratio` key in metrics below
`config.conv_tol` (convergence — pads with the last good state and
breaks), and records the dense solution.

Returns `(solutions: list[np.ndarray], step_metrics: list[dict] | None)`.
`solutions` is length `n_steps + 1` (initial state + one per step) on a
clean run. `step_metrics` collects only the non-empty metrics dicts, so
it is at most `n_steps` long and is `None` when every step returns `{}`
(as `ForwardEuler` does — its metrics come from the post-processor, not
the integrator).

This is one "experiment" - running a case with a code on a backend. The q8020 sweeper can take a parameterized description of a set of runs, and expand them out into these individual atomic experiments. Each experiment is composed of a pluggable set of the above piece part components. 


## 4. Writing a solver — the recipe

Five steps, of which only steps 1, 2, and 3 are mandatory. Steps 4
and 5 are opt-in.

### 4.1 Subclass `SolverConfig`

Add the equation's free parameters as dataclass fields. Implement
the abstract `describe()` that returns a JSON-serialisable summary
dict.

```python
@dataclass
class MyEqnConfig(SolverConfig):
    nu: float = 1e-3
    ic: str = "sine"
    # ... whatever your physics needs

    def describe(self) -> dict[str, Any]:
        return {"equation": "my_eqn", "nu": self.nu, ...}
```

`SolverConfig` already gives you `bc`, `cfl`, `dt`, `n_steps`,
`conv_tol`, `method`, `output_dir`, `save_every`, and an `extra`
dict for free-form metadata. There is also a no-op `setup()` hook
for derived setup (e.g., compute inlet primitives from raw config
once at construction time).

Note: `MainLoop` records every step's solution and does not itself act
on `save_every` — that field is advisory for a `PostProcessor` to honor
when deciding a snapshot cadence.

### 4.2 Implement `SpatialOperator`

```python
class MyRHS(SpatialOperator):
    def compute_rhs(self, state, grid, config, t=0.0):
        u = state.to_dense()
        # ... compute the spatial RHS, return ndarray
        return rhs_array

    # Optional: override for non-default CFL
    def compute_timestep(self, state, grid, cfl):
        return cfl * grid.dx  # default is fine for scalar advection
```

The default `compute_timestep` is `cfl * grid.dx` — fine for scalar
advection / Burgers. Multi-variable systems (Euler) override it to
include wave speeds.

### 4.3 Implement (or reuse) a `TimeIntegrator`

Reuse `ForwardEuler` if your scheme is `u_new = u + dt * rhs`. For
anything else (RK4, implicit BE, operator-split, MPO-on-MPS,
quantum-circuit step) write your own:

```python
class MyIntegrator(TimeIntegrator):
    def step(self, state, spatial_op, grid, config, dt, t=0.0):
        # ... advance one timestep
        return DenseState(u_new), {"metric_a": ..., "metric_b": ...}
```

The metrics dict is opaque to `MainLoop` *except* for the special
key `"residual_ratio"` (used for convergence stopping). Anything
else is forwarded to the post-processor and to the returned
`step_metrics` list.

### 4.4 (Optional) Implement a `PostProcessor`

For observability while the loop runs (metric logging, mid-run
plotting, custom snapshot policy):

```python
class MyPost(PostProcessor):
    def on_step(self, step, t, state, metrics):
        # called after every step
        ...
    def finalize(self, config, elapsed_s):
        # called once at the end
        ...
```

If you don't need it, pass `NullPostProcessor()` or just `None`
to `MainLoop.run()`.

### 4.5 (Optional) Implement / pick a `LinearSystemSolver`

Only needed if your integrator does an implicit step. Pick `LUSolver`
(direct, dense), `GMRESSolver(tol=...)` (iterative), or write your
own. Pass it as a constructor arg to your integrator — `MainLoop`
itself never sees the linsys solver; it's an integrator-internal
dependency.

### 4.6 Wire it up

```python
config = MyEqnConfig(nu=1e-3, n_steps=1000, cfl=0.1, ...)
grid = Grid1D.uniform(nelem=512)
state = DenseState(initial_condition(grid.xc))
spatial = MyRHS()
integrator = MyIntegrator()
post = MyPost()  # or NullPostProcessor() or None

solutions, metrics = MainLoop().run(
    config, grid, state, spatial, integrator, post,
)
```

That's the whole recipe.

## 5. The "delegating integrator" escape hatch

Some algorithms don't fit the per-step model — they need state that
is awkward to round-trip through `to_dense()` between steps (an MPS
that would have to be re-built each call), or they pre-build a
propagator once and apply it many times, or they own their own
chunked / shotted readout schedule.

Those use a delegating-integrator pattern: subclass `TimeIntegrator`
but in `step()` run the *entire* multi-step simulation internally
and return all snapshots in the returned metrics dict under
sentinel keys. The application's wrapper around `MainLoop.run()`
detects the delegated case and pulls the solution list out of
metrics rather than letting `MainLoop` accumulate per-step
snapshots.

This isn't a framework feature — it's an idiom that the application
implements. See `q8020-mps-burgers/src/burgers_fw.py::_DelegatingIntegrator`
for the canonical example. The framework cooperates by accepting
arbitrary metric dicts and not interpreting unknown keys.

When to reach for this:

- **Yes**: TEBD, MPS-with-MPO, pre-built quantum propagator, chunked
  shot-based readout, anything where rebuilding state between
  framework steps is the bottleneck.
- **No**: any per-step scheme where the state can be a numpy array
  cheaply. Use the normal pattern; the framework is doing useful
  work for you.

## 6. Contracts and invariants

Worth pinning down for implementers:

- **State shape is opaque to MainLoop.** Only `state.to_dense()` is
  called. Solutions list contains the dense arrays returned by that
  method. If your physics carries a 2-D `(nvar, N)` shape (Euler),
  that's what shows up in `solutions`.
- **`n_steps` is required.** `MainLoop` raises if `config.n_steps`
  is `None`. Convergence-driven runs should pass a generous upper
  bound and stop early via `residual_ratio`.
- **`dt` is decided once, before the loop.** Either set
  `config.dt` explicitly or let `MainLoop` call
  `spatial_op.compute_timestep(state, grid, cfl)` once at the
  start. There's no per-step adaptive `dt` in the framework today.
- **Divergence is contagious.** Any non-finite element in the
  state at the end of a step pads the rest of `solutions` with
  `NaN` and breaks. Downstream code (post-processors, plotting)
  must handle NaN-trailing solutions.
- **Convergence sentinel.** A step-metrics dict containing
  `"residual_ratio": r` with `r < config.conv_tol` triggers a
  clean stop; the loop pads `solutions` with the last good state
  to reach length `n_steps + 1`. No other keys are interpreted by
  the framework.
- **Output writes are the post-processor's job.** `MainLoop` does
  not write files. It returns in-memory lists; if you want
  on-disk output, do it from `PostProcessor.finalize()`.

## 7. What solverfw deliberately does NOT do

- **No CFD physics.** No fluxes, no Riemann solvers, no shock
  detection. Those live in application packages.
- **No mesh adaptation, no refinement, no AMR.** Grids are
  static. `Grid1D` is uniform.
- **No I/O format.** No HDF5 / VTK / Plot writers. Application
  packages own their output formats via `PostProcessor`.
- **No parameter sweeping.** That's `q8020-cfd-metautil`'s sweeper
  module — sibling to solverfw, not part of it.
- **No multi-physics coupling.** One state, one operator, one
  integrator, one loop. Coupled problems compose externally.

## 8. Versioning and stability

`solverfw` is internal infrastructure for the q8020 project. The
ABC contracts in §4 are intended to be stable; concrete classes
(`ForwardEuler`, `LUSolver`, `MainLoop`) may grow features but
should remain backwards-compatible by keyword-only kwargs with
defaults. Application packages (`q8020-mps-burgers`,
`q8020-fvm-euler-1d`, ...) pin against this package's version in
their own `pyproject.toml`.

Breaking changes — when needed — bump the framework's minor
version and require a coordinated update of every application
package. Do not break in patch releases.

## 9. Reference applications

| Package | Equation | Methods using framework |
|---|---|---|
| `q8020-mps-burgers` | 1-D Burgers | 8 methods (4 classical-style + 4 quantum-style); see [OVERVIEW-burgers-solver.md](../../q8020-mps-burgers/docs/OVERVIEW-burgers-solver.md) |
| `q8020-fvm-euler-1d` | 1-D Euler | finite-volume |

Both follow the §4 recipe; `q8020-mps-burgers` additionally uses the
delegating-integrator idiom from §5 for its three multi-step
methods.
