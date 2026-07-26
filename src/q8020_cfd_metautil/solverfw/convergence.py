"""ConvergencePredicate -- pluggable stopping rule (SPEC v2 §4.6).

A predicate is an object so it can hold cross-step context (e.g. the
initial residual) -- the limitation that forced applications to override
MainLoop.run().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.config import SolverConfig
    from q8020_cfd_metautil.solverfw.state import State


class ConvergencePredicate(ABC):
    """Decide when the loop stops early."""

    def start(self, state0: State, config: SolverConfig) -> None:
        """Called once before the loop; capture initial context here."""

    @abstractmethod
    def converged(
        self,
        step: int,
        state: State,
        prev: State,
        metrics: dict[str, Any],
        config: SolverConfig,
    ) -> bool:
        """Called after every step with that step's metrics."""


class ResidualRatio(ConvergencePredicate):
    """v1 default: metrics['residual_ratio'] < config.conv_tol."""

    def converged(self, step, state, prev, metrics, config):
        rr = metrics.get("residual_ratio")
        return rr is not None and rr < config.conv_tol


class ResidualVsInitial(ConvergencePredicate):
    """Residual ratio against the step-0 residual, plus a
    solution-change floor (the Euler-1D criterion).

    Reads metrics['residual'] (a norm); stops when
    residual / residual_initial < config.conv_tol, or when the
    solution change norm falls to dq_floor everywhere.
    """

    def __init__(self, dq_floor: float = 1e-10) -> None:
        self.dq_floor = dq_floor
        self._res_init: float | None = None

    def start(self, state0, config):
        self._res_init = None

    def converged(self, step, state, prev, metrics, config):
        res = metrics.get("residual")
        if res is not None:
            if self._res_init is None:
                self._res_init = float(res)
            elif self._res_init > 0.0:
                if float(res) / self._res_init < config.conv_tol:
                    return True
        dq = state.to_dense() - prev.to_dense()
        axis = tuple(range(1, dq.ndim))
        dqnorm = np.sqrt(np.sum(dq * dq, axis=axis)) if dq.ndim > 1 \
            else np.abs(dq)
        return bool(np.all(dqnorm <= self.dq_floor))
