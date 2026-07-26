"""LinearSystemSolver -- abstract Ax=b solver."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.backend import Backend
    from q8020_cfd_metautil.solverfw.encode import Encoder
    from q8020_cfd_metautil.solverfw.readout import Readout


@dataclass
class SolveContext:
    """Optional per-call context for solvers that need it (SPEC v2 §4.7).

    Classical solvers ignore it; quantum solvers use it for file naming,
    metrics attribution, and register sizing. Always passed by v2
    integrators, so isinstance-dispatch on solver type is unnecessary.
    """

    step: int = 0
    inner_iter: int = 0
    nelem: int | None = None
    x0: np.ndarray | None = None


class LinearSystemSolver(ABC):
    """Solve Ax = b. Used by implicit time integrators."""

    @abstractmethod
    def solve(
        self,
        A: np.ndarray,
        b: np.ndarray,
        ctx: SolveContext | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Return (solution_vector, metrics_dict)."""


class LUSolver(LinearSystemSolver):
    """Direct solve via scipy LU factorisation."""

    def solve(self, A, b, ctx=None):
        # Lazy import: defer scipy's import cost until a concrete solver is
        # actually used, keeping solverfw startup lean. scipy is a declared
        # dependency, so it is available when needed.
        import scipy.linalg
        # General dense solve. The original Euler solver attempted
        # assume_a='banded' but always fell back to a general dense solve for
        # its dense FD Jacobian, so a general solve is numerically equivalent
        # to the original (2a.1-C item 4: deliberate non-change).
        x = scipy.linalg.solve(A, b)
        return x, {}


class GMRESSolver(LinearSystemSolver):
    """Iterative GMRES solve."""

    def __init__(self, tol: float = 1e-6) -> None:
        self.tol = tol

    def solve(self, A, b, ctx=None):
        # Lazy import: see LUSolver.solve -- scipy is optional.
        import scipy.sparse.linalg as spla
        # Warm-start from ctx.x0 when supplied (Euler threads qold here to
        # reproduce the original's x0=qold behaviour). rtol/tol kwarg name
        # varies across scipy versions -- mirror the original robustness.
        x0 = ctx.x0 if ctx is not None and ctx.x0 is not None else None
        try:
            x, info = spla.gmres(A, b, x0=x0, rtol=self.tol)
        except TypeError:
            x, info = spla.gmres(A, b, x0=x0, tol=self.tol)
        return x, {"gmres_info": int(info)}


class NullLinearSystemSolver(LinearSystemSolver):
    """Placeholder when no linear solver is needed.

    Raises RuntimeError if accidentally called (explicit integrators
    should never need Ax=b).
    """

    def solve(self, A, b, ctx=None):
        raise RuntimeError(
            "NullLinearSystemSolver.solve() called -- this integrator "
            "does not require a linear system solver."
        )


@dataclass
class LinearSolveResult:
    """A quantum solve's native output (SPEC v2 §4.7).

    The circuit yields a normalized *direction*; the physical *scale* is
    recovered classically. x = direction * scale.
    """

    direction: np.ndarray
    scale: float
    metrics: dict[str, Any] = field(default_factory=dict)


class QuantumLinearSystemSolver(LinearSystemSolver):
    """Base for quantum linear solvers (HHL, SQLS-as-linsolve).

    Holds injected sub-plugins (Backend, Encoder, Readout) so they are
    recorded rather than smuggled through a config dict. Subclasses
    implement solve_quantum() returning a LinearSolveResult; the base
    solve() returns the scaled x = direction * scale, so classical callers
    (Euler's BDF integrator) see the same (x, metrics) contract as LU/GMRES
    and no isinstance-dispatch is needed.
    """

    def __init__(
        self,
        backend: Backend | None = None,
        encoder: Encoder | None = None,
        readout: Readout | None = None,
    ) -> None:
        self.backend = backend
        self.encoder = encoder
        self.readout = readout

    @abstractmethod
    def solve_quantum(
        self,
        A: np.ndarray,
        b: np.ndarray,
        ctx: SolveContext | None = None,
    ) -> LinearSolveResult:
        """Return the native (direction, scale, metrics) of the quantum solve."""

    def solve(self, A, b, ctx=None):
        result = self.solve_quantum(A, b, ctx)
        x = result.direction * result.scale
        return x, result.metrics
