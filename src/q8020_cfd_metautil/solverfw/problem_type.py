"""ProblemType -- Dedalus-style problem taxonomy (SPEC v2 §4.1)."""

from __future__ import annotations

from enum import Enum


class ProblemType(Enum):
    """How the driver loop's index is to be interpreted.

    IVP   -- initial-value: index is a time step; n_steps many.
    LBVP  -- linear boundary-value: one steady solve; n_steps = 1
             (iter 0 = unsolved initial, iter 1 = solved).
    NLBVP -- nonlinear steady via Newton (reserved; no driver yet).
    EVP   -- eigenvalue (reserved; no driver yet).
    """

    IVP = "ivp"
    LBVP = "lbvp"
    NLBVP = "nlbvp"
    EVP = "evp"
