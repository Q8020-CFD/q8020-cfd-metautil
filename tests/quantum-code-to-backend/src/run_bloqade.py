"""
Thin runner around a Bloqade PyQrack simulator.

Given a Bloqade simulator backend (StackMemorySimulator or
DynamicMemorySimulator) and a Kirin kernel, run the kernel `shots` times
and return the raw per-shot outputs as a list. Whatever each shot returns —
a CRegister, None, or a custom kernel return value — is preserved verbatim.

Crashes on any failure mid-shot (no retries, no partial results).
"""

from __future__ import annotations

from typing import Any, List


def run_bloqade_simulation(
    backend,
    kernel,
    shots: int,
) -> List[Any]:
    """
    Run a Bloqade kernel on a simulator backend for the given number of shots.

    Parameters
    ----------
    backend
        A Bloqade PyQrack simulator (StackMemorySimulator or
        DynamicMemorySimulator). Typically `result.backend` from
        `optimize_qasm_bloqade` or `prepare_qasm_for_bloqade`.
    kernel
        A Kirin Method kernel — e.g., the result of `qasm2.loadfile`,
        `prepare_qasm_for_bloqade(...).kernel`, or a hand-authored
        @qasm2.main / @qasm2.extended function.
    shots
        Number of independent shot executions. Each `backend.run(kernel)`
        call is a single shot; Bloqade's PyQrack backend has no batched
        shots API, so cost grows linearly with this value. Practically
        usable up to ~10k for small circuits; beyond that, plan on
        seconds-to-minutes of wall time.

    Returns
    -------
    list
        One entry per shot, holding whatever the kernel returns. For QASM2
        kernels loaded via `qasm2.loadfile`, this is `None` per shot (the
        loader does not synthesize a `return c;`). For kernels that return
        a CRegister (e.g. our Qiskit-bridge generated source), each entry
        is a CRegister whose elements are MeasurementResultValue enums.

    Raises
    ------
    Anything backend.run raises — propagated verbatim.
    """
    return [backend.run(kernel) for _ in range(shots)]
