"""TimeIntegrator -- abstract time-advancement + ForwardEuler."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.config import SolverConfig
    from q8020_cfd_metautil.solverfw.grid import Grid
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


class ForwardEuler(TimeIntegrator):
    """Explicit forward-Euler: u_new = u + dt * rhs."""

    def step(self, state, spatial_op, grid, config, dt, t=0.0):
        from q8020_cfd_metautil.solverfw.state import DenseState

        u = state.to_dense()
        rhs = spatial_op.compute_rhs(state, grid, config, t=t)
        u_new = u + dt * rhs
        return DenseState(u_new), {}
