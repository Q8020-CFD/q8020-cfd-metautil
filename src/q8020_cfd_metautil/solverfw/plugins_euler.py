"""Euler-reachable plugin registrations (SPEC v2 §5, v2a §2a.1-B).

Registers the plugins the Euler-1D port can reach as ``PluginSpec``s with
typed ``KnobSpec``s into the module-level ``REGISTRY``. ``cls`` is left
``None`` for every plugin: this workstream declares the names / knobs /
constraints only; binding concrete classes is phase-3 work.

Explicit registration at import (SPEC v2 §11: explicit ``register`` calls,
not decorators). This module is NOT wired into ``solverfw/__init__`` -- the
sweeper imports it on demand so a classical, registry-free sweep stays
load-light.

One real constraint is declared to exercise the pruning path: the ``hhl``
linsolve plugin ``requires=("backend!=classical",)``. In the chain shape
``{slot: {"plugin": name, "knobs": {...}}}`` the backend slot's *plugin
name* IS the execution target, so the selector reads against the plugin
name (``_selector_holds`` treats a dotless LHS as ``slot -> plugin``).
"""

from __future__ import annotations

from q8020_cfd_metautil.solverfw.registry import (
    REGISTRY,
    KnobSpec,
    PluginSpec,
    Registry,
)

# Knob bundles shared across related plugins.
_EXPLICIT_KNOBS: tuple[KnobSpec, ...] = (
    KnobSpec("cfl", float, help="CFL number for the step size"),
    KnobSpec("localdt", bool, help="per-cell local time stepping"),
)

_IMPLICIT_KNOBS: tuple[KnobSpec, ...] = _EXPLICIT_KNOBS + (
    KnobSpec("max_inner_iters", int, help="max inner Newton iterations"),
    KnobSpec("res_tol", float, help="inner residual tolerance"),
)


def _specs() -> tuple[PluginSpec, ...]:
    """The full set of Euler-reachable plugin declarations."""
    return (
        # slot: solve -- explicit (Runge-Kutta) integrators.
        PluginSpec("rk1", "solve", cls=None, knobs=_EXPLICIT_KNOBS),
        PluginSpec("rk2", "solve", cls=None, knobs=_EXPLICIT_KNOBS),
        PluginSpec("rk3", "solve", cls=None, knobs=_EXPLICIT_KNOBS),
        # slot: solve -- implicit (backward-difference) integrators.
        PluginSpec("bdf1", "solve", cls=None, knobs=_IMPLICIT_KNOBS),
        PluginSpec("bdf2", "solve", cls=None, knobs=_IMPLICIT_KNOBS),
        PluginSpec("bdf2opt", "solve", cls=None, knobs=_IMPLICIT_KNOBS),
        # slot: spatial.
        PluginSpec("rusanov_fvm", "spatial", cls=None),
        # slot: linsolve.
        PluginSpec("lu", "linsolve", cls=None),
        PluginSpec(
            "gmres",
            "linsolve",
            cls=None,
            knobs=(KnobSpec("tol", float, help="GMRES residual tolerance"),),
        ),
        PluginSpec(
            "hhl",
            "linsolve",
            cls=None,
            knobs=(
                KnobSpec("shots", int, help="measurement shots (0 = exact)"),
                KnobSpec(
                    "hhl_scale_type",
                    str,
                    help="eigenvalue/scale mapping strategy",
                ),
            ),
            # HHL is a quantum solve; it cannot run on the classical target.
            requires=("backend!=classical",),
        ),
        # slot: convergence.
        PluginSpec("residual_vs_initial", "convergence", cls=None),
        # slot: backend -- the plugin NAME is the execution target.
        PluginSpec("classical", "backend", cls=None),
        PluginSpec(
            "statevector_sim",
            "backend",
            cls=None,
            knobs=(KnobSpec("shots", int, help="measurement shots"),),
        ),
        PluginSpec(
            "noisy_sim",
            "backend",
            cls=None,
            knobs=(KnobSpec("shots", int, help="measurement shots"),),
        ),
        PluginSpec(
            "fake",
            "backend",
            cls=None,
            knobs=(KnobSpec("shots", int, help="measurement shots"),),
        ),
        PluginSpec(
            "hardware",
            "backend",
            cls=None,
            knobs=(KnobSpec("shots", int, help="measurement shots"),),
        ),
    )


def register_all(registry: Registry = REGISTRY) -> Registry:
    """Register every Euler-reachable plugin into *registry*.

    Idempotent-safe: a plugin already present (``Registry.register`` raises
    ``ValueError`` on a duplicate ``(slot, name)``) is skipped, so importing
    this module twice or re-running does not raise.
    """
    for spec in _specs():
        try:
            registry.register(spec)
        except ValueError:
            # Already registered (double import / re-run) -- leave as is.
            pass
    return registry


# Register at import time (explicit, idempotent-safe).
register_all()
