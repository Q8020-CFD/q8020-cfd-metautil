"""Validator -- optional per-slot checking (SPEC v2 §4.8).

A plugin does some work; a paired validator checks it. Optional
everywhere. A failed validator never aborts the run (the run's data is
still evidence); it marks the record.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from q8020_cfd_metautil.solverfw.config import SolverConfig


@dataclass
class ValidationResult:
    passed: bool | None  # None = advisory only, no threshold applied
    metrics: dict[str, Any] = field(default_factory=dict)


class Validator(ABC):
    """Check one slot's work. `slot` names which slot this validates."""

    slot: str = ""

    @abstractmethod
    def validate(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        config: SolverConfig,
    ) -> ValidationResult:
        """Return pass/fail (or advisory None) plus metrics."""


def run_validators(
    validators: dict[str, list[Validator]] | None,
    slot: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    config: SolverConfig,
) -> list[dict[str, Any]]:
    """Run all validators attached to *slot*; return ledger-ready records.

    Never raises out of a validator: an exception is recorded as a
    failed validation with the error message in metrics.
    """
    records: list[dict[str, Any]] = []
    for v in (validators or {}).get(slot, []):
        try:
            result = v.validate(inputs, outputs, config)
        except Exception as exc:  # noqa: BLE001 -- record, don't abort
            result = ValidationResult(
                passed=False, metrics={"error": repr(exc)},
            )
        records.append({
            "slot": slot,
            "validator": type(v).__name__,
            "passed": result.passed,
            "metrics": result.metrics,
        })
    return records
