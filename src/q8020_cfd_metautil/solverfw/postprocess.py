"""PostProcessor -- abstract observer for the main loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.config import SolverConfig
    from q8020_cfd_metautil.solverfw.state import State


class PostProcessor(ABC):
    """Observe the simulation as it runs. Write outputs at the end."""

    @abstractmethod
    def on_step(
        self,
        step: int,
        t: float,
        state: State,
        metrics: dict[str, Any],
    ) -> None:
        """Called after every time step."""

    @abstractmethod
    def finalize(
        self,
        config: SolverConfig,
        elapsed_s: float,
        solutions: list | None = None,
        grid: Any = None,
    ) -> None:
        """Called once after the loop finishes.

        v2 widens the hook: MainLoop passes the accumulated solutions
        and the grid so terminal I/O has the final state. v1 two-arg
        implementations keep working -- MainLoop falls back to the
        two-arg call on TypeError.
        """


class NullPostProcessor(PostProcessor):
    """No-op post-processor.  Used when no observer is needed."""

    def on_step(self, step, t, state, metrics):
        pass

    def finalize(self, config, elapsed_s, solutions=None, grid=None):
        pass
