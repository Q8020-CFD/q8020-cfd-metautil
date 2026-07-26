"""Encoder -- classical-vector-to-quantum-amplitude contract (SPEC v2 §4.3).

The universal quantum state-prep seam. Every quantum method loads a
classical vector into amplitudes before it can compute; this ABC names
that step so the choice (Mottonen vs MPS-staircase vs an SVD-encoded
solution like SQLS) is a recorded, swappable plugin.

Kept import-light: no qiskit at module level, so classical apps pay
nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.config import SolverConfig
    from q8020_cfd_metautil.solverfw.grid import Grid


@dataclass
class EncodeResult:
    """The output of an Encoder: a state-prep circuit plus the classical
    context a Readout needs to recover the physical answer.

    ``norm`` carries the classical scale of the encoded vector (the circuit
    holds only its normalized direction); Readout multiplies it back in.
    """

    circuit: Any  # state-prep circuit / fragment (qiskit QuantumCircuit)
    data_qubits: list[int]
    ancilla_qubits: list[int]
    norm: float
    meta: dict[str, Any] = field(default_factory=dict)


class Encoder(ABC):
    """Load a classical vector into quantum amplitudes.

    Simple loaders (Mottonen, MPS-staircase) take just the vector.
    Composite encoders that derive the loaded vector from an operator
    (e.g. an SVD-based quantum linear solver that encodes ``A^+ b``) take
    the operator via their constructor and receive ``b`` as ``vec``.
    """

    @abstractmethod
    def encode(
        self,
        vec: np.ndarray,
        *,
        grid: Grid | None = None,
        config: SolverConfig | None = None,
    ) -> EncodeResult:
        """Return an EncodeResult for loading *vec* into amplitudes."""
