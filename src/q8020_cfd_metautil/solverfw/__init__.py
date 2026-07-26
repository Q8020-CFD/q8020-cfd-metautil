"""solverfw -- pluggable CFD solver framework.

Provides abstract base classes and shared implementations for PDE
solvers (classical and quantum), plus the v2 survey vocabulary:
problem types, execution-target declarations, pluggable convergence,
per-slot validators, and the plugin registry.

Usage from an application package::

    from q8020_cfd_metautil.solverfw import (
        SolverConfig, Grid, Grid1D, DenseState, SpatialOperator,
        TimeIntegrator, ForwardEuler, MainLoop,
    )
"""

from q8020_cfd_metautil.solverfw.backend import (
    Backend,
    ClassicalTarget,
    MitigationSpec,
    NoiseSpec,
    QiskitBackend,
    TranspileSpec,
)
from q8020_cfd_metautil.solverfw.config import SolverConfig
from q8020_cfd_metautil.solverfw.convergence import (
    ConvergencePredicate,
    ResidualRatio,
    ResidualVsInitial,
)
from q8020_cfd_metautil.solverfw.encode import EncodeResult, Encoder
from q8020_cfd_metautil.solverfw.grid import Grid, Grid1D
from q8020_cfd_metautil.solverfw.state import DenseState, State
from q8020_cfd_metautil.solverfw.spatial import SpatialOperator
from q8020_cfd_metautil.solverfw.time_integrator import (
    ContainerIntegrator,
    ContainerResult,
    ForwardEuler,
    TimeIntegrator,
)
from q8020_cfd_metautil.solverfw.transform import (
    IdentityTransform,
    ProblemTransform,
)
from q8020_cfd_metautil.solverfw.linsys import (
    GMRESSolver,
    LUSolver,
    LinearSystemSolver,
    NullLinearSystemSolver,
    SolveContext,
)
from q8020_cfd_metautil.solverfw.postprocess import (
    NullPostProcessor,
    PostProcessor,
)
from q8020_cfd_metautil.solverfw.problem_type import ProblemType
from q8020_cfd_metautil.solverfw.readout import Readout, ReadoutContext
from q8020_cfd_metautil.solverfw.registry import (
    REGISTRY,
    ForeignInfo,
    ForeignPlugin,
    KnobSpec,
    PluginSpec,
    Registry,
    SLOTS,
)
from q8020_cfd_metautil.solverfw.validate import (
    ValidationResult,
    Validator,
)
from q8020_cfd_metautil.solverfw.loop import MainLoop

__all__ = [
    "Backend",
    "ClassicalTarget",
    "ContainerIntegrator",
    "ContainerResult",
    "ConvergencePredicate",
    "DenseState",
    "EncodeResult",
    "Encoder",
    "ForeignInfo",
    "ForeignPlugin",
    "ForwardEuler",
    "IdentityTransform",
    "GMRESSolver",
    "Grid",
    "Grid1D",
    "KnobSpec",
    "LUSolver",
    "LinearSystemSolver",
    "MainLoop",
    "MitigationSpec",
    "NoiseSpec",
    "NullLinearSystemSolver",
    "NullPostProcessor",
    "PluginSpec",
    "PostProcessor",
    "ProblemTransform",
    "ProblemType",
    "QiskitBackend",
    "REGISTRY",
    "Readout",
    "ReadoutContext",
    "Registry",
    "ResidualRatio",
    "ResidualVsInitial",
    "SLOTS",
    "SolveContext",
    "SolverConfig",
    "SpatialOperator",
    "State",
    "TimeIntegrator",
    "TranspileSpec",
    "ValidationResult",
    "Validator",
]
