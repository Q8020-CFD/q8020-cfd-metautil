"""
QASM <-> Cirq conversion helpers.

Thin wrappers around Cirq's native QASM support so users can cross the
framework boundary without caring about Cirq's API surface.

Notes
-----
- Cirq's `circuit_from_qasm` supports OpenQASM 2.0 only. OpenQASM 3 files
  will raise a clear error pointing at this limitation.
- `cirq.Circuit.to_qasm()` emits OpenQASM 2.0.
- Round-trip (QASM -> Cirq -> QASM) is NOT guaranteed to be textually
  identical: Cirq may decompose some gates into equivalent forms. Count
  preservation is also not guaranteed for this reason -- compare via
  circuit equivalence or state fidelity rather than raw gate counts.
"""

from __future__ import annotations

import os
from typing import Optional

import cirq
from cirq.contrib.qasm_import import circuit_from_qasm


class QasmVersionError(ValueError):
    """Raised when the QASM source declares a version Cirq does not support."""


def _detect_qasm_version(source: str) -> str:
    # Skip blank lines and comments; Cirq emits a `// Generated from ...`
    # banner before the OPENQASM header.
    first = ""
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        first = stripped
        break
    if first.startswith("OPENQASM 3"):
        return "3"
    if first.startswith("OPENQASM 2"):
        return "2"
    return "unknown"


def qasm_str_to_cirq(qasm_text: str) -> cirq.Circuit:
    """Convert an OpenQASM 2.0 source string into a `cirq.Circuit`."""
    version = _detect_qasm_version(qasm_text)
    if version == "3":
        raise QasmVersionError(
            "Cirq's QASM importer supports OpenQASM 2.0 only; "
            "got OpenQASM 3. Re-export your source with qasm2.dump / "
            "qasm2.dumps instead of qasm3."
        )
    if version == "unknown":
        raise QasmVersionError(
            "Could not detect OpenQASM version header; expected the first "
            "non-empty line to start with 'OPENQASM 2.0'."
        )
    return circuit_from_qasm(qasm_text)


def qasm_to_cirq(qasm_path: str) -> cirq.Circuit:
    """Read a `.qasm` file (OpenQASM 2.0) and return a `cirq.Circuit`."""
    if not os.path.isfile(qasm_path):
        raise FileNotFoundError(f"QASM file not found: {qasm_path}")
    with open(qasm_path, "r", encoding="utf-8") as fd:
        source = fd.read()
    return qasm_str_to_cirq(source)


def cirq_to_qasm_str(circuit: cirq.Circuit) -> str:
    """Serialize a `cirq.Circuit` to OpenQASM 2.0 source."""
    return cirq.Circuit(circuit).to_qasm()


def cirq_to_qasm(
    circuit: cirq.Circuit,
    out_path: Optional[str] = None,
) -> str:
    """
    Serialize a `cirq.Circuit` to OpenQASM 2.0, optionally writing to disk.

    Returns the QASM source as a string either way.
    """
    source = cirq_to_qasm_str(circuit)
    if out_path is not None:
        with open(out_path, "w", encoding="utf-8") as fd:
            fd.write(source)
    return source
