"""ProblemTransform -- change-of-variables stage (SPEC v2 §4.4).

Some methods march an easier variable than the physical one: Cole-Hopf
(u<->phi<->psi), the LBM moment map (u<->f_i), Carleman, Schroedingerisation.
The transform brackets the whole solve -- forward once before, inverse on
every recorded snapshot after -- so the *recorded* solutions are always in
u-space regardless of what the method marched internally.

The default IdentityTransform is a no-op; a v1/v2a run passes no transform
and behaves exactly as before.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.config import SolverConfig
    from q8020_cfd_metautil.solverfw.grid import Grid


class ProblemTransform(ABC):
    """u -> v before the solve, v -> u after. Carries its own metadata."""

    @abstractmethod
    def forward(
        self, u: np.ndarray, grid: Grid, config: SolverConfig,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Return (v, carry). carry holds what inverse needs (norm, ...)."""

    @abstractmethod
    def inverse(
        self, v: np.ndarray, carry: dict[str, Any], grid: Grid,
        config: SolverConfig,
    ) -> np.ndarray:
        """Map a v-space array back to u-space using carry."""


class IdentityTransform(ProblemTransform):
    """The default: no change of variables."""

    def forward(self, u, grid, config):
        return u, {}

    def inverse(self, v, carry, grid, config):
        return v
