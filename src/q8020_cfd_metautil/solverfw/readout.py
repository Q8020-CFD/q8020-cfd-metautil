"""Readout -- quantum-result-to-dense-array contract (SPEC v2 §4.3).

The decode half of the quantum seam. A Backend returns a raw result
(statevector, counts dict, or job handle, uninterpreted); Readout turns
it back into a dense numpy array, owning ancilla projection /
marginalization, post-selection, sign recovery, and scale restoration.

Backend does NOT own readout (SPEC §11-Q2): the same circuit on the same
backend can be read out several ways. Kept import-light.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.encode import EncodeResult


@dataclass
class ReadoutContext:
    """Classical context a Readout needs to recover the physical answer.

    ``encode`` carries the ancilla layout and norm; ``scale`` is the
    physical magnitude to restore (e.g. ``||final_b||`` for SQLS);
    ``sign_ref`` is a classical sign reference used to restore signs lost
    in counts-mode measurement.
    """

    encode: EncodeResult | None = None
    scale: float | None = None
    sign_ref: np.ndarray | None = None


class Readout(ABC):
    """Turn a Backend's raw result back into a dense array."""

    @abstractmethod
    def decode(
        self,
        raw: Any,
        ctx: ReadoutContext,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Return (dense_array, metrics). Metrics (success probability,
        shots used, discarded fraction) flow to the ledger."""
