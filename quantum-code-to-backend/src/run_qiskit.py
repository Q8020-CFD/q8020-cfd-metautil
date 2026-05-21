"""
Thin runner around qiskit_ibm_runtime.SamplerV2.

Given a backend (Aer, fake IBM, or real IBM Runtime), a transpiled
QuantumCircuit, and a shot count, submit the job via SamplerV2, block on
the result, and return it untouched. The caller does their own DataBin
unpacking and post-processing.

Crashes on any failure (transpile mismatch, queue error, runtime error).
No retries, no partial-result handling.
"""

from __future__ import annotations

from typing import List, Optional, Union

from qiskit import QuantumCircuit
from qiskit_ibm_runtime import SamplerV2 as Sampler

CircuitOrBatch = Union[QuantumCircuit, List[QuantumCircuit]]


def run_qiskit_simulation(
    backend,
    circuit: CircuitOrBatch,
    shots: int,
    *,
    seed_simulator: Optional[int] = None,
):
    """
    Submit one or more circuits to a Qiskit-compatible backend via SamplerV2.

    Parameters
    ----------
    backend
        A Qiskit Backend object (AerSimulator, FakeBackend, or real IBM
        Runtime backend). Typically `result.backend` from `optimize_qasm`.
    circuit
        A single QuantumCircuit or a list of them. Should already be
        transpiled for the backend (the wrapper does this; raw user circuits
        may fail on hardware backends with restrictive instruction sets).
    shots
        Number of shots per circuit. SamplerV2 applies this uniformly across
        all circuits in the batch.
    seed_simulator
        For Aer reproducibility. Threaded via Sampler options; ignored by
        non-Aer backends.

    Returns
    -------
    PrimitiveResult
        The raw SamplerV2 result. Index by circuit position to get a
        `SamplerPubResult`; access counts via `result[i].data.<creg_name>.get_counts()`.
        The classical-register name in your QuantumCircuit (e.g. `'c'` from
        `QuantumCircuit(2, 2)` or `'meas'` from `measure_all()`) determines
        which DataBin field holds the outcomes.

    Raises
    ------
    Anything Sampler.run or job.result raises — no error swallowing.
    """
    pubs = [circuit] if isinstance(circuit, QuantumCircuit) else list(circuit)

    sampler_kwargs = {}
    if seed_simulator is not None:
        sampler_kwargs["options"] = {"simulator": {"seed_simulator": seed_simulator}}

    sampler = Sampler(backend, **sampler_kwargs)
    job = sampler.run(pubs, shots=shots)
    return job.result()
