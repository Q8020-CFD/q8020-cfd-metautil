"""SpatialOperator -- abstract spatial discretisation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.config import SolverConfig
    from q8020_cfd_metautil.solverfw.grid import Grid
    from q8020_cfd_metautil.solverfw.state import State


class SpatialOperator(ABC):
    """Contract: given a state and grid, produce the spatial RHS."""

    @abstractmethod
    def compute_rhs(
        self,
        state: State,
        grid: Grid,
        config: SolverConfig,
        t: float = 0.0,
    ) -> np.ndarray:
        """Return the spatial RHS contribution (same shape as state)."""

    def compute_timestep(
        self,
        state: State,
        grid: Grid,
        cfl: float,
    ) -> float:
        """CFL-based time-step estimate.

        Default: dt = cfl * dx (fine for scalar advection / Burgers).
        Override for multi-variable systems with wave speeds.
        """
        return cfl * grid.dx
