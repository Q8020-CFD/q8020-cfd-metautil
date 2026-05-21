"""
Framework-agnostic QASM -> optimized Qiskit circuit pipeline.

Users author circuits in any framework (Qiskit, Bloqade, Cirq, Braket, ...),
export to OpenQASM 2 or 3, then call `optimize_qasm` with the target backend
of their choice. The function returns a transpiled QuantumCircuit ready for
any Qiskit-compatible runner (SamplerV2, backend.run, Statevector, etc.).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from qiskit import QuantumCircuit, qasm2, qasm3
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

BackendType = Literal["ideal", "fake", "real-ibm"]


@dataclass(frozen=True)
class OptimizeResult:
    circuit: QuantumCircuit
    backend: Any
    backend_name: str
    backend_type: BackendType
    original_depth: int
    original_gates: int
    original_qubits: int
    transpiled_depth: int
    transpiled_gates: int
    transpiled_qubits: int
    extras: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"Backend: {self.backend_name} ({self.backend_type})\n"
            f"  qubits: {self.original_qubits} -> {self.transpiled_qubits}\n"
            f"  depth:  {self.original_depth} -> {self.transpiled_depth}\n"
            f"  gates:  {self.original_gates} -> {self.transpiled_gates}"
        )


def load_qasm(qasm_path: str) -> QuantumCircuit:
    """Load a QASM file, auto-detecting OpenQASM 2 vs 3 from the header."""
    if not os.path.isfile(qasm_path):
        raise FileNotFoundError(f"QASM file not found: {qasm_path}")

    with open(qasm_path, "r", encoding="utf-8") as fd:
        source = fd.read()

    header = source.lstrip().splitlines()[0] if source.strip() else ""
    if header.startswith("OPENQASM 3"):
        return qasm3.loads(source)
    if header.startswith("OPENQASM 2"):
        return qasm2.loads(
            source,
            custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS,
        )
    raise ValueError(
        f"Could not detect OpenQASM version from header: {header!r}. "
        f"First line must start with 'OPENQASM 2' or 'OPENQASM 3'."
    )


def resolve_backend(
    backend_type: BackendType,
    backend_method: str,
    *,
    use_gpu: bool = False,
    ibm_token: Optional[str] = None,
    ibm_instance: Optional[str] = None,
    ibm_channel: str = "ibm_quantum_platform",
    min_num_qubits: Optional[int] = None,
) -> Any:
    """Return a Qiskit-compatible backend object based on the requested type."""
    if backend_type == "ideal":
        from qiskit_aer import AerSimulator

        kwargs = {"method": backend_method}
        if use_gpu:
            kwargs["device"] = "GPU"
        try:
            return AerSimulator(**kwargs)
        except Exception as exc:
            if use_gpu:
                print(f"[warn] GPU simulator unavailable ({exc}); falling back to CPU")
                return AerSimulator(method=backend_method)
            raise

    if backend_type == "fake":
        from qiskit_ibm_runtime.fake_provider import FakeProviderForBackendV2

        return FakeProviderForBackendV2().backend(backend_method)

    if backend_type == "real-ibm":
        from qiskit_ibm_runtime import QiskitRuntimeService

        service_kwargs = {"channel": ibm_channel}
        if ibm_token is not None:
            service_kwargs["token"] = ibm_token
        if ibm_instance is not None:
            service_kwargs["instance"] = ibm_instance
        service = QiskitRuntimeService(**service_kwargs)

        if backend_method == "least_busy":
            return service.least_busy(
                operational=True,
                simulator=False,
                min_num_qubits=min_num_qubits,
            )
        return service.backend(backend_method)

    raise ValueError(
        f"Unknown backend_type: {backend_type!r}. "
        f"Expected one of: 'ideal', 'fake', 'real-ibm'."
    )


def optimize_qasm(
    qasm_path: str,
    backend_type: BackendType = "ideal",
    backend_method: str = "statevector",
    *,
    optimization_level: int = 2,
    use_gpu: bool = False,
    ibm_token: Optional[str] = None,
    ibm_instance: Optional[str] = None,
    ibm_channel: str = "ibm_quantum_platform",
    min_num_qubits: Optional[int] = None,
    seed_transpiler: int = 42,
    approximation_degree: float = 1.0,
    verbose: bool = True,
) -> OptimizeResult:
    """
    Load a QASM file and transpile it for the chosen backend.

    Parameters
    ----------
    qasm_path
        Path to an OpenQASM 2 or 3 source file.
    backend_type
        'ideal'     -> local AerSimulator (noiseless).
        'fake'      -> local FakeProvider backend (noise model of a real device).
        'real-ibm'  -> real IBM Quantum hardware via QiskitRuntimeService.
    backend_method
        For 'ideal':    Aer method ('statevector', 'density_matrix', 'mps', ...).
        For 'fake':     fake device name ('fake_torino', 'fake_brisbane', ...).
        For 'real-ibm': IBM device name ('ibm_torino', ...) or 'least_busy'.
    optimization_level
        Qiskit preset optimization level (0-3). 2 is a good default.
    use_gpu
        Only for 'ideal'. Tries AerSimulator(device='GPU'), falls back to CPU.
    ibm_token, ibm_instance, ibm_channel
        Forwarded to QiskitRuntimeService when backend_type='real-ibm'. If the
        user has already run save_account() once, these can be omitted.
    min_num_qubits
        Only used when backend_method='least_busy' to filter eligible devices.
    seed_transpiler, approximation_degree
        Passed to generate_preset_pass_manager for reproducibility.
    verbose
        If True, prints a before/after summary.

    Returns
    -------
    OptimizeResult
        The transpiled circuit and metrics. Feed `result.circuit` to a
        SamplerV2, backend.run, or Statevector downstream.
    """
    circuit = load_qasm(qasm_path)

    backend = resolve_backend(
        backend_type,
        backend_method,
        use_gpu=use_gpu,
        ibm_token=ibm_token,
        ibm_instance=ibm_instance,
        ibm_channel=ibm_channel,
        min_num_qubits=min_num_qubits,
    )

    pm_kwargs = {
        "optimization_level": optimization_level,
        "seed_transpiler": seed_transpiler,
        "approximation_degree": approximation_degree,
    }
    if hasattr(backend, "target") and backend.target is not None:
        pm_kwargs["target"] = backend.target
    else:
        pm_kwargs["backend"] = backend

    pass_manager = generate_preset_pass_manager(**pm_kwargs)
    transpiled = pass_manager.run(circuit)

    backend_name = getattr(backend, "name", str(backend))
    if callable(backend_name):
        backend_name = backend_name()

    result = OptimizeResult(
        circuit=transpiled,
        backend=backend,
        backend_name=str(backend_name),
        backend_type=backend_type,
        original_depth=circuit.depth(),
        original_gates=sum(circuit.count_ops().values()),
        original_qubits=circuit.num_qubits,
        transpiled_depth=transpiled.depth(),
        transpiled_gates=sum(transpiled.count_ops().values()),
        transpiled_qubits=transpiled.num_qubits,
    )

    if verbose:
        print(result.summary())

    return result
