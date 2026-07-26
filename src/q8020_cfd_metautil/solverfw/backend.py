"""Backend -- declaration of the execution target (SPEC v2 §4.2).

The Backend element declares WHERE and HOW a solve or circuit executes.
q8020-backend-utils remains the mechanism; concrete quantum backends wrap
it. This element does not do SLURM -- experiment placement is the
sweeper's job.

Kept import-light: no qiskit / backend-utils imports at module level, so
purely classical applications pay nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TranspileSpec:
    optimization_level: int = 1
    coupling_map: str = "default"  # "default" | "all-to-all"
    initial_layout: list[int] | None = None
    seed_transpiler: int | None = None


@dataclass
class NoiseSpec:
    kind: str = "ideal"  # "ideal" | "from_backend" | "thermal"
    t1_us: float | None = None
    t2_us: float | None = None


@dataclass
class MitigationSpec:
    # Live in q8020-backend-utils:
    trex: bool = False
    dynamical_decoupling: bool = False
    dd_sequence: str = "XY4"
    # Reserved -- named, not implemented anywhere yet:
    zne: bool = False
    pec: bool = False
    error_correction: bool = False

    def __post_init__(self) -> None:
        for reserved in ("zne", "pec", "error_correction"):
            if getattr(self, reserved):
                raise NotImplementedError(
                    f"{reserved} is reserved in MitigationSpec; no "
                    "implementation exists in q8020-backend-utils"
                )


@dataclass
class Backend(ABC):
    """Declaration of execution intent. Sweepable; recorded whole."""

    target: str = "classical"
    shots: int = 0  # 0 = exact (statevector); >0 = sampled
    seed: int | None = None
    repeats: int = 1
    transpile: TranspileSpec | None = None
    noise: NoiseSpec | None = None
    mitigation: MitigationSpec | None = None
    use_session: bool = False

    @abstractmethod
    def execute(
        self, circuit: Any, *, shots: int | None = None
    ) -> Any:
        """Run a circuit; return the mechanism's raw result uninterpreted."""

    def describe(self) -> dict[str, Any]:
        """Serialisable summary for the ledger's backend section."""
        out: dict[str, Any] = {
            "target": self.target,
            "shots": self.shots,
            "seed": self.seed,
            "repeats": self.repeats,
            "use_session": self.use_session,
        }
        for key in ("transpile", "noise", "mitigation"):
            val = getattr(self, key)
            if val is not None:
                out[key] = asdict(val)
        return out


@dataclass
class ClassicalTarget(Backend):
    """The classical execution target -- not null (SPEC §11-Q7).

    A classical solve is an in-process call; execute() raises. The
    declaration exists so the sweeper can vary gpu/threads and the
    ledger can record them.
    """

    target: str = "classical"
    gpu: bool = False
    threads: int | None = None

    def execute(self, circuit: Any, *, shots: int | None = None) -> Any:
        raise RuntimeError(
            "ClassicalTarget.execute() called -- classical solves run "
            "in-process; this element only declares gpu/threads intent."
        )

    def describe(self) -> dict[str, Any]:
        out = super().describe()
        out["gpu"] = self.gpu
        out["threads"] = self.threads
        return out


@dataclass
class QiskitBackend(Backend):
    """Qiskit execution target wrapping q8020-backend-utils.

    target: "statevector_sim" | "noisy_sim" | "fake:<device>"
            | "hardware:<device>"

    Lazily imports q8020_backend_utils.ibm at first execute(), maps the
    declaration onto get_backend / transpile_circuit /
    execute_circuit_counts. Construction-time validation follows the
    hw-runner SPEC rules: mitigation "on" is only valid on SamplerV2
    (non-Aer) paths -- fail before spending shots.
    """

    target: str = "statevector_sim"
    _backend_obj: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        mit = self.mitigation
        is_sim = self.target in ("statevector_sim", "noisy_sim")
        if mit is not None and is_sim and (
            mit.trex or mit.dynamical_decoupling
        ):
            raise ValueError(
                "TREX / dynamical decoupling require the SamplerV2 "
                f"(fake/hardware) path; target={self.target!r} is Aer"
            )

    # -- mapping helpers -------------------------------------------------

    def resolve(self) -> Any:
        """Build (once) and return the mechanism backend object."""
        if self._backend_obj is not None:
            return self._backend_obj
        from q8020_backend_utils.ibm.backend import get_backend

        kind, _, device = self.target.partition(":")
        kwargs: dict[str, Any] = {}
        if self.transpile is not None:
            kwargs["coupling_map"] = self.transpile.coupling_map
        if self.noise is not None and self.noise.kind == "thermal":
            kwargs["t1"] = self.noise.t1_us
            kwargs["t2"] = self.noise.t2_us
        backend_type = {
            "statevector_sim": "sim",
            "noisy_sim": "sim",
            "fake": "fake",
            "hardware": "hardware",
        }[kind]
        self._backend_obj = get_backend(
            name=device or None,
            backend_type=backend_type,
            **kwargs,
        )
        return self._backend_obj

    def execute(self, circuit: Any, *, shots: int | None = None) -> Any:
        """Transpile + run; returns (counts, exec_info) from the mechanism."""
        from q8020_backend_utils.ibm.circuit import (
            build_sampler_options,
            execute_circuit_counts,
            transpile_circuit,
        )

        backend = self.resolve()
        n_shots = self.shots if shots is None else shots
        tsp = self.transpile or TranspileSpec()
        qc_t, _tinfo = transpile_circuit(
            circuit,
            backend,
            optimization_level=tsp.optimization_level,
            seed_transpiler=tsp.seed_transpiler,
            initial_layout=tsp.initial_layout,
        )
        sampler_options = None
        if self.mitigation is not None:
            sampler_options = build_sampler_options(
                self.mitigation.trex,
                self.mitigation.dynamical_decoupling,
                dd_sequence_type=self.mitigation.dd_sequence,
            )
        return execute_circuit_counts(
            qc_t,
            backend,
            shots=n_shots,
            seed=self.seed,
            sampler_options=sampler_options,
        )
