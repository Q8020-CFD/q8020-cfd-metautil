"""Plugin registry -- slot / plugin / knob vocabulary (SPEC v2 §5).

solverfw owns the registry; metautil's ledger and the sweeper import it.
Selector syntax for requires/conflicts: "slot=plugin", "slot.knob=value",
or negated "slot.knob!=value".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SLOTS = (
    "grid",
    "transform",
    "spatial",
    "solve",
    "encode",
    "backend",
    "readout",
    "linsolve",
    "convergence",
    "validate",
)


@dataclass(frozen=True)
class ForeignInfo:
    """Provenance for adapted foreign code (SPEC v2 §8)."""

    lib: str
    version: str
    symbols: tuple[str, ...] = ()


class ForeignPlugin:
    """Mixin marking an adapter that wraps a foreign code (SPEC v2 §8).

    Carries machine-readable provenance so a run self-describes as foreign
    and its adaptation rung lands in the ledger's code section. The rung is
    a property of the adapter plugin, not the run (decision B.6-Q1).
    """

    foreign: ForeignInfo
    rung: int = 2

    def foreign_meta(self) -> dict[str, object]:
        """Serialisable {lib, version, symbols} for the code section."""
        return {
            "lib": self.foreign.lib,
            "version": self.foreign.version,
            "symbols": list(self.foreign.symbols),
        }


@dataclass(frozen=True)
class KnobSpec:
    name: str
    type: type
    default: Any = None
    help: str = ""


@dataclass(frozen=True)
class PluginSpec:
    name: str
    slot: str
    cls: type | None = None
    knobs: tuple[KnobSpec, ...] = ()
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    origin: str = "native"  # "native" | "foreign"
    rung: int = 4  # adaptation rung 0-4
    foreign: ForeignInfo | None = None

    def __post_init__(self) -> None:
        if self.slot not in SLOTS:
            raise ValueError(
                f"unknown slot {self.slot!r}; valid slots: {SLOTS}"
            )


@dataclass
class Registry:
    """Registry of plugins keyed by (slot, name)."""

    _plugins: dict[tuple[str, str], PluginSpec] = field(default_factory=dict)

    def register(self, spec: PluginSpec) -> PluginSpec:
        key = (spec.slot, spec.name)
        if key in self._plugins:
            raise ValueError(
                f"plugin {spec.name!r} already registered for slot "
                f"{spec.slot!r}"
            )
        self._plugins[key] = spec
        return spec

    def get(self, slot: str, name: str) -> PluginSpec:
        try:
            return self._plugins[(slot, name)]
        except KeyError:
            raise KeyError(
                f"no plugin {name!r} in slot {slot!r}; "
                f"known: {self.names(slot)}"
            ) from None

    def names(self, slot: str) -> list[str]:
        return sorted(n for s, n in self._plugins if s == slot)

    def specs(self, slot: str | None = None) -> list[PluginSpec]:
        if slot is None:
            return list(self._plugins.values())
        return [v for (s, _), v in self._plugins.items() if s == slot]

    # ------------------------------------------------------------------
    # Compatibility checking (sweeper pruning hook)
    # ------------------------------------------------------------------

    def check_cell(self, chain: dict[str, dict[str, Any]]) -> list[str]:
        """Check one expanded case cell against declared constraints.

        chain maps slot -> {"plugin": name, "knobs": {...}}. Returns a
        list of human-readable violation strings; empty means the cell
        is valid. Unknown plugins are reported as violations rather than
        raising, so pruning can log-and-drop.
        """
        violations: list[str] = []
        for slot, fill in chain.items():
            name = fill.get("plugin")
            if name is None:
                continue
            try:
                spec = self.get(slot, name)
            except KeyError as exc:
                violations.append(str(exc))
                continue
            for sel in spec.requires:
                if not _selector_holds(sel, chain):
                    violations.append(
                        f"{slot}={name} requires {sel!r}"
                    )
            for sel in spec.conflicts:
                if _selector_holds(sel, chain):
                    violations.append(
                        f"{slot}={name} conflicts with {sel!r}"
                    )
        return violations


def _selector_holds(sel: str, chain: dict[str, dict[str, Any]]) -> bool:
    """Evaluate one selector against a chain.

    Forms: "slot=plugin", "slot.knob=value", "slot.knob!=value".
    A selector on an absent slot/knob evaluates to False ("=") or
    True ("!=").
    """
    negate = "!=" in sel
    lhs, _, rhs = sel.partition("!=" if negate else "=")
    lhs = lhs.strip()
    rhs = rhs.strip()
    if "." in lhs:
        slot, knob = lhs.split(".", 1)
        actual = chain.get(slot, {}).get("knobs", {}).get(knob)
    else:
        slot = lhs
        actual = chain.get(slot, {}).get("plugin")
    holds = actual is not None and str(actual) == rhs
    return not holds if negate else holds


REGISTRY = Registry()
