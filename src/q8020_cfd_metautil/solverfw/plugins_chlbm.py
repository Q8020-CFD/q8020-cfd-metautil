"""CH-LBM-reachable plugin registrations (SPEC v2 §5).

Declares the plugins the Burgers / Cole-Hopf / QALB / LBM code reaches, as
``PluginSpec``s with typed ``KnobSpec``s. ``cls`` is ``None``: this declares
the slot / plugin / knob vocabulary (so the ledger can name what varied and
the sweeper can prune) without binding classes -- binding the container
sub-plugins is phase-3 work (ContainerIntegrator / Encoder / Readout /
ProblemTransform do not exist yet).

Mirrors ``plugins_euler``. Explicit, idempotent-safe registration at import;
NOT wired into ``solverfw/__init__`` -- imported on demand.
"""

from __future__ import annotations

from q8020_cfd_metautil.solverfw.registry import (
    REGISTRY,
    ForeignInfo,
    KnobSpec,
    PluginSpec,
    Registry,
)


def _specs() -> tuple[PluginSpec, ...]:
    return (
        # slot: spatial -- shift-operator central-difference RHS.
        PluginSpec(
            "shift_fd",
            "spatial",
            cls=None,
            knobs=(KnobSpec("nu", float, help="viscosity"),),
        ),
        # slot: solve -- classical forward-Euler happy path.
        PluginSpec(
            "forward_euler",
            "solve",
            cls=None,
            knobs=(KnobSpec("cfl", float, help="CFL number"),),
        ),
        # slot: solve -- delegating (container) methods. kind=container is
        # advisory metadata until ContainerIntegrator lands (phase 3).
        PluginSpec(
            "lbm",
            "solve",
            cls=None,
            knobs=(
                KnobSpec("nu", float, help="viscosity -> relaxation time"),
            ),
        ),
        PluginSpec(
            "cole_hopf_circuit",
            "solve",
            cls=None,
            knobs=(
                KnobSpec("bond_dim", int, help="MPS state-prep bond dim"),
                KnobSpec("phi_modes", int, help="Cole-Hopf mode count"),
                KnobSpec("evolution_mode", str, help="single | measure_reprepare"),
                KnobSpec("segment_size", int, help="measure-reprepare segment"),
            ),
        ),
        PluginSpec(
            "qlbm_circuit",
            "solve",
            cls=None,
            knobs=(
                KnobSpec("fock_qubits", int, help="Fock-space qubits per site"),
                KnobSpec(
                    "qalb_collision_trotter_reps",
                    int,
                    help="collision Trotter repetitions",
                ),
                KnobSpec("trotter_order", int, help="Suzuki-Trotter order"),
            ),
        ),
        # slot: transform -- change-of-variables families (declared now;
        # the ProblemTransform ABC is phase-3, so cls stays None).
        PluginSpec("cole_hopf", "transform", cls=None),
        PluginSpec("lbm_moment_map", "transform", cls=None),
        # slot: encode -- MPS staircase state-prep (the pooling target that
        # sqls's truncated_mps also fills; see q8020-sqls-encode).
        PluginSpec(
            "mps_staircase",
            "encode",
            cls=None,
            knobs=(KnobSpec("bond_dim", int, help="MPS bond dimension"),),
        ),
        # slot: readout.
        PluginSpec("statevector_project", "readout", cls=None),
        PluginSpec(
            "counts_marginalize",
            "readout",
            cls=None,
            knobs=(KnobSpec("shots", int, help="measurement shots"),),
        ),
    )


def register_all(registry: Registry = REGISTRY) -> Registry:
    """Register every CH-LBM-reachable plugin. Idempotent-safe."""
    for spec in _specs():
        try:
            registry.register(spec)
        except ValueError:
            pass
    return registry


register_all()
