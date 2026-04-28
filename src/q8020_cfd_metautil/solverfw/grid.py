"""Grid -- abstract base, and Grid1D -- uniform 1-D computational grid."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


class Grid(ABC):
    """Abstract base for all computational grids.

    Subclasses must expose at least *nelem*, *bc*, and *ndim*.
    """

    nelem: int
    bc: str

    @property
    @abstractmethod
    def ndim(self) -> int:
        """Spatial dimensionality (1, 2, ...)."""


@dataclass
class Grid1D(Grid):
    """Uniform 1-D grid with optional qubit metadata.

    Attributes
    ----------
    nelem : number of interior cells / grid points
    xc    : cell-centre coordinates, shape (nelem,)
    dx    : uniform cell width
    bc    : boundary condition tag (informational)
    q     : log2(nelem) when nelem is a power of 2 (quantum solvers)
    """

    nelem: int
    xc: np.ndarray
    dx: float
    bc: str = "periodic"
    q: int | None = None

    @property
    def ndim(self) -> int:  # noqa: D102
        return 1

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_qubits(
        cls,
        q: int,
        bc: str = "periodic",
        x0: float = 0.0,
        x1: float = 1.0,
    ) -> Grid1D:
        """Build a grid for quantum solvers: N = 2^q points."""
        N = 1 << q
        if bc == "dirichlet":
            xc = np.linspace(x0, x1, N, endpoint=True)
        else:
            xc = np.linspace(x0, x1, N, endpoint=False)
        dx = float(xc[1] - xc[0])
        return cls(nelem=N, xc=xc, dx=dx, bc=bc, q=q)

    @classmethod
    def uniform(
        cls,
        nelem: int,
        x0: float = 0.0,
        x1: float = 1.0,
        bc: str = "periodic",
    ) -> Grid1D:
        """Build a uniform grid with *nelem* cells."""
        xc = np.linspace(x0, x1, nelem, endpoint=False)
        dx = float(xc[1] - xc[0])
        q = None
        if nelem > 0 and (nelem & (nelem - 1)) == 0:
            q = int(np.log2(nelem))
        return cls(nelem=nelem, xc=xc, dx=dx, bc=bc, q=q)
