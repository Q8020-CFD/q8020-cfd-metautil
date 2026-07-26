"""TimeIntegrator -- abstract time-advancement + ForwardEuler.

Also home to ContainerIntegrator (SPEC v2 §4.5): a solve plugin that owns
its own multi-step loop and named sub-plugins (encode / backend / readout /
linsolve). It promotes the v1 delegating-integrator idiom to a framework
type MainLoop detects and calls once, so the container's internals are
recorded rather than smuggled through sentinel metric keys.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.backend import Backend
    from q8020_cfd_metautil.solverfw.config import SolverConfig
    from q8020_cfd_metautil.solverfw.encode import Encoder
    from q8020_cfd_metautil.solverfw.grid import Grid
    from q8020_cfd_metautil.solverfw.linsys import LinearSystemSolver
    from q8020_cfd_metautil.solverfw.readout import Readout
    from q8020_cfd_metautil.solverfw.spatial import SpatialOperator
    from q8020_cfd_metautil.solverfw.state import State


class TimeIntegrator(ABC):
    """Advance the solution by one time step."""

    @abstractmethod
    def step(
        self,
        state: State,
        spatial_op: SpatialOperator,
        grid: Grid,
        config: SolverConfig,
        dt: float,
        t: float = 0.0,
    ) -> tuple[State, dict[str, Any]]:
        """Return (new_state, step_metrics)."""

    def compute_dt(
        self,
        state: State,
        spatial_op: SpatialOperator,
        grid: Grid,
        config: SolverConfig,
    ) -> float:
        """The integrator owns dt (v2). Default preserves v1 behavior:
        config.dt if set, else the spatial operator's CFL estimate.
        Local/adaptive time-stepping integrators override this and may
        ignore the loop's dt entirely inside step().
        """
        if config.dt is not None:
            return config.dt
        return spatial_op.compute_timestep(state, grid, config.cfl)


class ForwardEuler(TimeIntegrator):
    """Explicit forward-Euler: u_new = u + dt * rhs."""

    def step(self, state, spatial_op, grid, config, dt, t=0.0):
        from q8020_cfd_metautil.solverfw.state import DenseState

        u = state.to_dense()
        rhs = spatial_op.compute_rhs(state, grid, config, t=t)
        u_new = u + dt * rhs
        return DenseState(u_new), {}


@dataclass
class ContainerResult:
    """The output of a ContainerIntegrator.run_all (SPEC v2 §4.5).

    solutions includes the initial state; genuine_steps is an optional
    native-cadence remap (the LBM family runs coarser than the caller grid).
    """

    solutions: list[np.ndarray]
    step_metrics: list[dict[str, Any]] | None = None
    genuine_steps: list[int] | None = None


class ContainerIntegrator(TimeIntegrator):
    """A solve plugin that owns its own multi-step loop and sub-plugins.

    Subclasses implement run_all(). MainLoop detects a ContainerIntegrator
    and calls run_all() ONCE -- no sentinel keys, no app-side loop bypass.
    Sub-plugins (encoder / backend / readout / linsolve) are optional,
    constructor-injected, and recorded in the ledger's code.chain.
    """

    encoder: Encoder | None = None
    backend: Backend | None = None
    readout: Readout | None = None
    linsolve: LinearSystemSolver | None = None

    @abstractmethod
    def run_all(
        self,
        state: State,
        spatial_op: SpatialOperator,
        grid: Grid,
        config: SolverConfig,
    ) -> ContainerResult:
        """Run the entire multi-step simulation; return all snapshots."""

    def step(self, state, spatial_op, grid, config, dt, t=0.0):
        """Back-compat shim: a v1 caller that still invokes step() gets the
        old delegating sentinel-key shape so nothing breaks mid-migration.
        MainLoop uses run_all() directly and never hits this path."""
        from q8020_cfd_metautil.solverfw.state import DenseState

        result = self.run_all(state, spatial_op, grid, config)
        final = DenseState(result.solutions[-1])
        return final, {
            "_delegated_solutions": result.solutions,
            "_delegated_metrics": result.step_metrics,
            "_delegated_genuine_steps": result.genuine_steps,
        }
