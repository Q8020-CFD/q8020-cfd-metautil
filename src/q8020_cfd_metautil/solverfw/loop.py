"""MainLoop -- the framework's iteration driver (SPEC v2 §4.6).

v2: the index is an iteration interpreted per config.problem_type (IVP:
time step; LBVP: one solve). dt is owned by the integrator via
compute_dt(). Convergence is a pluggable predicate. finalize() receives
the solutions and grid. All new parts are optional; a v1 call runs
unchanged.
"""

from __future__ import annotations

import sys
import time
from typing import Any

import numpy as np

from q8020_cfd_metautil.solverfw.config import SolverConfig
from q8020_cfd_metautil.solverfw.convergence import (
    ConvergencePredicate,
    ResidualRatio,
)
from q8020_cfd_metautil.solverfw.grid import Grid
from q8020_cfd_metautil.solverfw.postprocess import PostProcessor
from q8020_cfd_metautil.solverfw.problem_type import ProblemType
from q8020_cfd_metautil.solverfw.spatial import SpatialOperator
from q8020_cfd_metautil.solverfw.state import State
from q8020_cfd_metautil.solverfw.time_integrator import TimeIntegrator
from q8020_cfd_metautil.solverfw.validate import Validator, run_validators


class MainLoop:
    """Iteration driver.

    Owns the loop; delegates physics to pluggable components.
    """

    def run(
        self,
        config: SolverConfig,
        grid: Grid,
        state: State,
        spatial_op: SpatialOperator,
        integrator: TimeIntegrator,
        post: PostProcessor | None = None,
        convergence: ConvergencePredicate | None = None,
        validators: dict[str, list[Validator]] | None = None,
    ) -> tuple[list[np.ndarray], list[dict[str, Any]] | None]:
        """Execute the iteration loop.

        Returns (solutions, step_metrics_list).
        solutions[0] = initial state; solutions[k] = state after iter k.
        step_metrics_list is a list of per-step dicts (may be empty).
        Validation records (if any validators ran) are appended to the
        last step's metrics under the key "_validation".
        """
        n_steps = config.n_steps
        if config.problem_type is ProblemType.LBVP:
            if n_steps is None:
                n_steps = 1
            elif n_steps != 1:
                raise ValueError(
                    "LBVP is a one-shot solve; n_steps must be 1 "
                    f"(got {n_steps})"
                )
        elif config.problem_type is not ProblemType.IVP:
            raise NotImplementedError(
                f"no driver for problem_type={config.problem_type} yet"
            )
        if n_steps is None:
            raise ValueError(
                "MainLoop requires config.n_steps; convergence-driven "
                "loops should set n_steps to a large upper bound."
            )

        if convergence is None:
            convergence = ResidualRatio()
        convergence.start(state, config)

        # The integrator owns dt (v2). For LBVP dt is meaningless; pass 0.
        if config.problem_type is ProblemType.LBVP:
            dt = 0.0
        else:
            dt = integrator.compute_dt(state, spatial_op, grid, config)

        solutions: list[np.ndarray] = [state.to_dense().copy()]
        all_metrics: list[dict[str, Any]] = []
        validation_records: list[dict[str, Any]] = []
        t = 0.0

        wall_start = time.perf_counter()

        for step in range(n_steps):
            prev = state
            state, metrics = integrator.step(
                state, spatial_op, grid, config, dt, t=t,
            )
            t += dt

            if validators:
                validation_records += run_validators(
                    validators, "solve",
                    {"step": step, "prev": prev, "dt": dt},
                    {"state": state, "metrics": metrics},
                    config,
                )

            if metrics:
                all_metrics.append(metrics)
            if post is not None:
                post.on_step(step, t, state, metrics)

            u_arr = state.to_dense()
            if not np.all(np.isfinite(u_arr)):
                print(
                    f"[solverfw] diverged at step {step + 1}/{n_steps}; "
                    f"padding remaining with NaN",
                    file=sys.stderr, flush=True,
                )
                nan_fill = np.full_like(u_arr, np.nan)
                solutions.extend(
                    [nan_fill] * (n_steps - step)
                )
                break
            solutions.append(u_arr.copy())

            if convergence.converged(step, state, prev, metrics, config):
                # Pad solutions to expected length.
                remaining = n_steps - (step + 1)
                if remaining > 0:
                    solutions.extend([u_arr.copy()] * remaining)
                break

        if validation_records:
            if not all_metrics:
                all_metrics.append({})
            all_metrics[-1]["_validation"] = validation_records

        elapsed = time.perf_counter() - wall_start
        if post is not None:
            self._finalize(post, config, elapsed, solutions, grid)

        return solutions, all_metrics or None

    @staticmethod
    def _finalize(
        post: PostProcessor,
        config: SolverConfig,
        elapsed: float,
        solutions: list[np.ndarray],
        grid: Grid,
    ) -> None:
        """Call finalize with the v2 signature, falling back to v1."""
        try:
            post.finalize(
                config, elapsed, solutions=solutions, grid=grid,
            )
        except TypeError:
            post.finalize(config, elapsed)
