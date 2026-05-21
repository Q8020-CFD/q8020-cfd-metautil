"""
QASM2 -> Bloqade simulator, the simple way.

Single purpose: take a path to an OpenQASM 2.0 file, return a Bloqade kernel
plus a PyQrack-backed simulator, ready to run. No gate-set rewriting, no
parallelization, no Qiskit bridge. The simulator executes every gate in the
bloqade.qasm2.* namespace natively, so there is nothing to compile away.

If you need parallelization or neutral-atom-native decomposition, see
`qasm_transpile_bloqade_qiskit_bridge.py` for the previous, heavier pipeline.

Pipeline
--------
1. QASM2 file  ->  Bloqade kernel via `qasm2.loadfile(..., returns=...)`.
2. Optionally apply `QASM2Fold` (constant-fold / dead-code elimination).
3. Resolve a simulator backend (StackMemorySimulator or DynamicMemorySimulator).
4. Return a result object with the kernel, the backend, and basic metadata.

Capturing measurement outcomes
------------------------------
Bloqade's `qasm2.loadfile` accepts a `returns=<creg_name>` keyword that makes
the loaded kernel return the named classical register. We forward that as a
top-level `returns` argument:
- `returns="auto"` (default) -> pick the QASM's first/only creg; None if none.
- `returns=<name>`           -> use the named creg explicitly.
- `returns=None`             -> match Bloqade's default (kernel returns None).

OpenQASM 2 only. Bloqade's loader does not support QASM 3.
No real-hardware submission path; targets classical simulators only.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Tuple

from bloqade import qasm2
from bloqade.qasm2.emit import QASM2
from bloqade.qasm2.passes import QASM2Fold

BackendType = Literal["stack", "dynamic"]

_CREG_RE = re.compile(r"^\s*creg\s+(\w+)\s*\[(\d+)\]\s*;\s*$")
_QREG_RE = re.compile(r"^\s*qreg\s+\w+\s*\[(\d+)\]\s*;\s*$")


@dataclass(frozen=True)
class BloqadeLoadResult:
    kernel: Any
    backend: Any
    backend_name: str
    backend_type: BackendType
    qasm_text: str
    num_qubits: int
    fold_applied: bool
    returns: Optional[str]
    extras: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"Backend: {self.backend_name} ({self.backend_type})\n"
            f"  qubits: {self.num_qubits}\n"
            f"  fold applied: {self.fold_applied}\n"
            f"  returns: {self.returns!r}"
        )


def _scan_qasm_registers(qasm_path: str) -> Tuple[int, List[str]]:
    """Return (total_qubits, [creg_name, ...]) by parsing qreg/creg lines."""
    total_qubits = 0
    cregs: List[str] = []
    with open(qasm_path, "r", encoding="utf-8") as fd:
        for line in fd:
            qmatch = _QREG_RE.match(line)
            if qmatch:
                total_qubits += int(qmatch.group(1))
                continue
            cmatch = _CREG_RE.match(line)
            if cmatch:
                cregs.append(cmatch.group(1))
    return total_qubits, cregs


def _resolve_returns_arg(returns: Optional[str], cregs: List[str]) -> Optional[str]:
    """
    Map the user-facing `returns` value to what `qasm2.loadfile` expects.

    - 'auto'  -> first creg name, or None if no cregs declared
    - None    -> None (Bloqade default; kernel returns None)
    - <name>  -> validate the name exists in the QASM
    """
    if returns is None:
        return None
    if returns == "auto":
        return cregs[0] if cregs else None
    if returns not in cregs:
        raise ValueError(
            f"returns={returns!r} not found in QASM cregs {cregs!r}. "
            f"Use returns='auto' to pick the first creg automatically."
        )
    return returns


def load_qasm_as_bloqade_kernel(
    qasm_path: str,
    *,
    returns: Optional[str] = "auto",
) -> Any:
    """
    Load a QASM2 file into a Bloqade `qasm2.main` kernel.

    Parameters
    ----------
    qasm_path
        Path to an OpenQASM 2.0 file.
    returns
        Which classical register to return from the kernel.
        - 'auto' (default) -> first creg in the QASM, or None if absent.
        - <name>           -> a specific creg name.
        - None             -> kernel returns None (sim.run(...) returns None).

    Raises
    ------
    FileNotFoundError
        If qasm_path does not exist.
    ValueError
        If the file declares OpenQASM 3, or if `returns` names a creg that
        is not in the QASM.
    """
    if not os.path.isfile(qasm_path):
        raise FileNotFoundError(f"QASM file not found: {qasm_path}")

    with open(qasm_path, "r", encoding="utf-8") as fd:
        header = next(
            (ln for ln in fd if ln.strip() and not ln.strip().startswith("//")),
            "",
        ).strip()
    if header.startswith("OPENQASM 3"):
        raise ValueError(
            "Bloqade's loader supports OpenQASM 2.0 only; got OpenQASM 3. "
            "Re-export your source with qasm2.dump / qasm2.dumps."
        )

    _, cregs = _scan_qasm_registers(qasm_path)
    resolved_returns = _resolve_returns_arg(returns, cregs)

    return qasm2.loadfile(qasm_path, returns=resolved_returns)


def resolve_backend_bloqade(
    backend_type: BackendType = "stack",
    *,
    min_qubits: int = 0,
) -> Any:
    """Return a Bloqade PyQrack simulator backend."""
    if backend_type == "stack":
        from bloqade.pyqrack import StackMemorySimulator
        return StackMemorySimulator(min_qubits=min_qubits)
    if backend_type == "dynamic":
        from bloqade.pyqrack import DynamicMemorySimulator
        return DynamicMemorySimulator()
    raise ValueError(
        f"Unknown backend_type: {backend_type!r}. "
        f"Expected one of: 'stack', 'dynamic'."
    )


def _count_qreg_qubits(qasm_text: str) -> int:
    total = 0
    for line in qasm_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("qreg"):
            try:
                inside = stripped.split("[", 1)[1].split("]", 1)[0]
                total += int(inside)
            except (IndexError, ValueError):
                continue
    return total


def prepare_qasm_for_bloqade(
    qasm_path: str,
    backend_type: BackendType = "stack",
    *,
    fold: bool = True,
    returns: Optional[str] = "auto",
    min_qubits: Optional[int] = None,
    verbose: bool = True,
) -> BloqadeLoadResult:
    """
    Load a QASM2 file and return a Bloqade kernel + simulator, ready to run.

    Parameters
    ----------
    qasm_path
        Path to an OpenQASM 2.0 file.
    backend_type
        'stack'   -> StackMemorySimulator (fixed qubit count).
        'dynamic' -> DynamicMemorySimulator (runtime qubit allocation).
    fold
        If True, apply QASM2Fold for constant-folding / dead-code elimination.
        Cheap and always safe.
    returns
        Which classical register to return from the kernel. With the default
        'auto', `sim.run(kernel)` returns a `CRegister` of measurement
        outcomes; pass `None` if you don't want a return value.
    min_qubits
        Simulator preallocation size ('stack' only). Inferred from the QASM's
        qreg declarations if None.
    verbose
        Print a one-line summary.
    """
    kernel = load_qasm_as_bloqade_kernel(qasm_path, returns=returns)

    qasm_text = QASM2().emit_str(kernel)
    num_qubits = _count_qreg_qubits(qasm_text)

    if fold:
        QASM2Fold(kernel.dialects)(kernel)

    resolved_min_qubits = min_qubits if min_qubits is not None else num_qubits
    backend = resolve_backend_bloqade(
        backend_type, min_qubits=resolved_min_qubits
    )

    # The actual creg name we asked Bloqade to return (post-resolution)
    _, cregs = _scan_qasm_registers(qasm_path)
    resolved_returns = _resolve_returns_arg(returns, cregs)

    result = BloqadeLoadResult(
        kernel=kernel,
        backend=backend,
        backend_name=type(backend).__name__,
        backend_type=backend_type,
        qasm_text=qasm_text,
        num_qubits=num_qubits,
        fold_applied=fold,
        returns=resolved_returns,
    )

    if verbose:
        print(result.summary())

    return result
