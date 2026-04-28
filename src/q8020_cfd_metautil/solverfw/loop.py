"""MainLoop -- the framework's time-marching driver."""

from __future__ import annotations

import sys
import time
from typing import Any

import numpy as np

from q8020_cfd_metautil.solverfw.config import SolverConfig
from q8020_cfd_metautil.solverfw.grid import Grid
from q8020_cfd_metautil.solverfw.postprocess import PostProcessor
from q8020_cfd_metautil.solverfw.spatial import SpatialOperator
from q8020_cfd_metautil.solverfw.state import State
from q8020_cfd_metautil.solverfw.time_integrator import TimeIntegrator


class MainLoop:
    """Time-marching driver.

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
    ) -> tuple[list[np.ndarray], list[dict[str, Any]] | None]:
        """Execute the time-stepping loop.

        Returns (solutions, step_metrics_list).
        solutions[0] = initial state; solutions[k] = state after step k.
        step_metrics_list is a list of per-step dicts (may be empty).
        """
        n_steps = config.n_steps
        if n_steps is None:
            raise ValueError(
                "MainLoop requires config.n_steps; convergence-driven "
                "loops should set n_steps to a large upper bound."
            )

        dt = config.dt
        if dt is None:
            dt = spatial_op.compute_timestep(state, grid, config.cfl)

        solutions: list[np.ndarray] = [state.to_dense().copy()]
        all_metrics: list[dict[str, Any]] = []
        t = 0.0

        wall_start = time.perf_counter()

        for step in range(n_steps):
            state, metrics = integrator.step(
                state, spatial_op, grid, config, dt, t=t,
            )
            t += dt

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

            # Convergence check (residual-based solvers).
            if self._converged(metrics, config):
                # Pad solutions to expected length.
                remaining = n_steps - (step + 1)
                if remaining > 0:
                    solutions.extend([u_arr.copy()] * remaining)
                break

        elapsed = time.perf_counter() - wall_start
        if post is not None:
            post.finalize(config, elapsed)

        return solutions, all_metrics or None

    @staticmethod
    def _converged(
        metrics: dict[str, Any],
        config: SolverConfig,
    ) -> bool:
        """Check if the solution has converged.

        Override or extend for custom convergence criteria.
        Default: check for a 'residual_ratio' key in metrics.
        """
        rr = metrics.get("residual_ratio")
        if rr is not None and rr < config.conv_tol:
            return True
        return False
