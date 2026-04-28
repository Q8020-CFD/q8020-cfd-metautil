"""State representations for solver solutions."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class State(Protocol):
    """Minimal contract for a solver state vector."""

    def to_dense(self) -> np.ndarray:
        """Return the solution as a plain numpy array."""
        ...

    def copy(self) -> State:
        """Return an independent copy."""
        ...

    @property
    def shape(self) -> tuple[int, ...]:
        ...


class DenseState:
    """Concrete State backed by a numpy array.

    Works for both scalar fields (Burgers: shape (N,)) and
    multi-variable systems (Euler: shape (nvar, N)).
    """

    __slots__ = ("data",)

    def __init__(self, data: np.ndarray) -> None:
        self.data = np.asarray(data)

    def to_dense(self) -> np.ndarray:
        return self.data

    def copy(self) -> DenseState:
        return DenseState(self.data.copy())

    @property
    def shape(self) -> tuple[int, ...]:
        return self.data.shape

    # Convenience: allow numpy-style arithmetic so existing code
    # that does ``u + dt * rhs`` keeps working when u is DenseState.
    def __array__(self, dtype=None, copy=None) -> np.ndarray:
        if dtype is not None:
            return np.array(self.data, dtype=dtype)
        return self.data
