"""LinearSystemSolver -- abstract Ax=b solver."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class LinearSystemSolver(ABC):
    """Solve Ax = b. Used by implicit time integrators."""

    @abstractmethod
    def solve(
        self,
        A: np.ndarray,
        b: np.ndarray,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Return (solution_vector, metrics_dict)."""


class LUSolver(LinearSystemSolver):
    """Direct solve via scipy LU factorisation."""

    def solve(self, A, b):
        # Lazy import: defer scipy's import cost until a concrete solver is
        # actually used, keeping solverfw startup lean. scipy is a declared
        # dependency, so it is available when needed.
        import scipy.linalg
        x = scipy.linalg.solve(A, b)
        return x, {}


class GMRESSolver(LinearSystemSolver):
    """Iterative GMRES solve."""

    def __init__(self, tol: float = 1e-6) -> None:
        self.tol = tol

    def solve(self, A, b):
        # Lazy import: see LUSolver.solve -- scipy is optional.
        import scipy.sparse.linalg as spla
        x, info = spla.gmres(A, b, rtol=self.tol)
        return x, {"gmres_info": int(info)}


class NullLinearSystemSolver(LinearSystemSolver):
    """Placeholder when no linear solver is needed.

    Raises RuntimeError if accidentally called (explicit integrators
    should never need Ax=b).
    """

    def solve(self, A, b):
        raise RuntimeError(
            "NullLinearSystemSolver.solve() called -- this integrator "
            "does not require a linear system solver."
        )
