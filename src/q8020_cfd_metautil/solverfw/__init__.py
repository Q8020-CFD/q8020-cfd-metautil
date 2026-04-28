"""solverfw -- pluggable CFD solver framework.

Provides abstract base classes and shared implementations for
time-marching PDE solvers (classical and quantum).

Usage from an application package::

    from q8020_cfd_metautil.solverfw import (
        SolverConfig, Grid, Grid1D, DenseState, SpatialOperator,
        TimeIntegrator, ForwardEuler, MainLoop,
    )
"""

from q8020_cfd_metautil.solverfw.config import SolverConfig
from q8020_cfd_metautil.solverfw.grid import Grid, Grid1D
from q8020_cfd_metautil.solverfw.state import DenseState, State
from q8020_cfd_metautil.solverfw.spatial import SpatialOperator
from q8020_cfd_metautil.solverfw.time_integrator import (
    ForwardEuler,
    TimeIntegrator,
)
from q8020_cfd_metautil.solverfw.linsys import (
    GMRESSolver,
    LUSolver,
    LinearSystemSolver,
    NullLinearSystemSolver,
)
from q8020_cfd_metautil.solverfw.postprocess import (
    NullPostProcessor,
    PostProcessor,
)
from q8020_cfd_metautil.solverfw.loop import MainLoop

__all__ = [
    "DenseState",
    "ForwardEuler",
    "GMRESSolver",
    "Grid",
    "Grid1D",
    "LUSolver",
    "LinearSystemSolver",
    "MainLoop",
    "NullLinearSystemSolver",
    "NullPostProcessor",
    "PostProcessor",
    "SolverConfig",
    "SpatialOperator",
    "State",
    "TimeIntegrator",
]
