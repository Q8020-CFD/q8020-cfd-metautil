"""SolverConfig -- base configuration for all solverfw applications."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SolverConfig(ABC):
    """Base configuration shared by every solver application.

    Subclasses add equation-specific fields (e.g. viscosity, gamma).
    """

    # --- domain / grid ---
    bc: str = "periodic"

    # --- time stepping ---
    cfl: float = 0.1
    dt: float | None = None  # if set, overrides CFL-derived dt
    n_steps: int | None = None  # None = run to convergence
    conv_tol: float = 1e-10

    # --- method selection ---
    method: str = "shift"

    # --- output ---
    output_dir: Path = field(default_factory=lambda: Path.cwd())
    save_every: int = 0  # 0 = only initial + final

    # --- extensible metadata ---
    extra: dict[str, Any] = field(default_factory=dict)

    # Hook for derived setup (e.g. computing inlet primitives).
    def setup(self) -> None:
        """Post-init hook. Override in subclasses if needed."""

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Return a serialisable dict summarising this config."""
