# """
# Framework-agnostic QASM -> optimized Bloqade kernel pipeline.

# User input contract: a path to an OpenQASM 2 file. Internally, the pipeline
# bridges through Qiskit to build a Bloqade kernel directly in the
# `qasm2.extended` dialect (rather than going through Bloqade's `qasm2.loads`,
# which produces `qasm2.main` kernels that can't be parallelized).

# Pipeline
# --------
# 1. QASM2 file -> Qiskit QuantumCircuit (via QuantumCircuit.from_qasm_file).
# 2. Walk qc.data and generate Python source for a @qasm2.extended kernel.
# 3. Write the generated source to a temp .py file, import it, extract the
#    kernel. (Bloqade decorators need `inspect.getsource`, which requires the
#    source to live in a real file; `exec()` does not work here.)
# 4. Apply Bloqade passes (QASM2Fold, UOpToParallel with native-gate decomp).
# 5. Return the rewritten kernel and a simulator backend, plus metadata.

# Notes
# -----
# - OpenQASM 2 only. Qiskit and Bloqade both lack a QASM 3 path.
# - QASM-loaded kernels used to return None; since this implementation generates
#   `return c` in the source, `sim.run(kernel)` now returns the CRegister.
# - Gate-count goes UP, not down, when native decomposition is on. A single
#   Clifford gate (h, x, s, cx) expands to ~3 Rydberg-native gates (u, rz, cz).
#   The benefit lives in the IR (fewer sequential blocks), not the gate tally.
# - There is no real-hardware submission path. Bloqade's neutral-atom hardware
#   (QuEra Aquila) is an analog device programmed via pulse schedules, not
#   gate circuits. This function targets simulators.
# """

# from __future__ import annotations

# import importlib.util
# import os
# import tempfile
# import uuid
# from dataclasses import dataclass, field
# from typing import Any, List, Literal, Optional

# from bloqade import qasm2
# from bloqade.qasm2.emit import QASM2
# from bloqade.qasm2.passes import QASM2Fold, UOpToParallel, UnrollIfs
# from qiskit import QuantumCircuit

# BackendType = Literal["stack", "dynamic"]


# @dataclass(frozen=True)
# class BloqadeOptimizeResult:
#     kernel: Any
#     backend: Any
#     backend_name: str
#     backend_type: BackendType
#     original_qasm: str
#     transpiled_qasm: str
#     original_gates: int
#     transpiled_gates: int
#     original_qubits: int
#     transpiled_qubits: int
#     passes_applied: tuple
#     generated_source: str = ""
#     extras: dict = field(default_factory=dict)

#     def summary(self) -> str:
#         return (
#             f"Backend: {self.backend_name} ({self.backend_type})\n"
#             f"  qubits: {self.original_qubits} -> {self.transpiled_qubits}\n"
#             f"  gates:  {self.original_gates} -> {self.transpiled_gates}\n"
#             f"  passes: {', '.join(self.passes_applied) if self.passes_applied else '(none)'}"
#         )


# # -----------------------------------------------------------------------------
# # Qiskit gate -> Bloqade source mapping
# # -----------------------------------------------------------------------------
# # Each entry: how many qubit args and how many params the Qiskit op takes.
# # The value is a callable that takes (qubit_indices, param_values) and returns
# # the Bloqade source line (without the leading "    " indentation).

# _GATE_MAP = {
#     # Single-qubit, no params
#     "h":       lambda qs, _: f"qasm2.h(q[{qs[0]}])",
#     "x":       lambda qs, _: f"qasm2.x(q[{qs[0]}])",
#     "y":       lambda qs, _: f"qasm2.y(q[{qs[0]}])",
#     "z":       lambda qs, _: f"qasm2.z(q[{qs[0]}])",
#     "s":       lambda qs, _: f"qasm2.s(q[{qs[0]}])",
#     "sdg":     lambda qs, _: f"qasm2.sdg(q[{qs[0]}])",
#     "t":       lambda qs, _: f"qasm2.t(q[{qs[0]}])",
#     "tdg":     lambda qs, _: f"qasm2.tdg(q[{qs[0]}])",
#     "sx":      lambda qs, _: f"qasm2.sx(q[{qs[0]}])",
#     "sxdg":    lambda qs, _: f"qasm2.sxdg(q[{qs[0]}])",
#     "id":      lambda qs, _: f"qasm2.id(q[{qs[0]}])",
#     # Single-qubit, one param
#     "rx":      lambda qs, p: f"qasm2.rx(q[{qs[0]}], {p[0]!r})",
#     "ry":      lambda qs, p: f"qasm2.ry(q[{qs[0]}], {p[0]!r})",
#     "rz":      lambda qs, p: f"qasm2.rz(q[{qs[0]}], {p[0]!r})",
#     "p":       lambda qs, p: f"qasm2.p(q[{qs[0]}], {p[0]!r})",
#     "u1":      lambda qs, p: f"qasm2.u1(q[{qs[0]}], {p[0]!r})",
#     # Single-qubit, two/three params
#     "u2":      lambda qs, p: f"qasm2.u2(q[{qs[0]}], {p[0]!r}, {p[1]!r})",
#     "u3":      lambda qs, p: f"qasm2.u3(q[{qs[0]}], {p[0]!r}, {p[1]!r}, {p[2]!r})",
#     "u":       lambda qs, p: f"qasm2.u(q[{qs[0]}], {p[0]!r}, {p[1]!r}, {p[2]!r})",
#     # Two-qubit
#     "cx":      lambda qs, _: f"qasm2.cx(q[{qs[0]}], q[{qs[1]}])",
#     "cy":      lambda qs, _: f"qasm2.cy(q[{qs[0]}], q[{qs[1]}])",
#     "cz":      lambda qs, _: f"qasm2.cz(q[{qs[0]}], q[{qs[1]}])",
#     "swap":    lambda qs, _: f"qasm2.swap(q[{qs[0]}], q[{qs[1]}])",
#     "ch":      lambda qs, _: f"qasm2.ch(q[{qs[0]}], q[{qs[1]}])",
#     "csx":     lambda qs, _: f"qasm2.csx(q[{qs[0]}], q[{qs[1]}])",
#     # Two-qubit, one param (Bloqade signature: ctrl, qarg, lam)
#     "cp":      lambda qs, p: f"qasm2.cp(q[{qs[0]}], q[{qs[1]}], {p[0]!r})",
#     "crx":     lambda qs, p: f"qasm2.crx(q[{qs[0]}], q[{qs[1]}], {p[0]!r})",
#     "cry":     lambda qs, p: f"qasm2.cry(q[{qs[0]}], q[{qs[1]}], {p[0]!r})",
#     "crz":     lambda qs, p: f"qasm2.crz(q[{qs[0]}], q[{qs[1]}], {p[0]!r})",
#     # Three-qubit
#     "ccx":     lambda qs, _: f"qasm2.ccx(q[{qs[0]}], q[{qs[1]}], q[{qs[2]}])",
#     "cswap":   lambda qs, _: f"qasm2.cswap(q[{qs[0]}], q[{qs[1]}], q[{qs[2]}])",
#     # Non-gate instructions (handled specially below)
#     "barrier": None,   # emitted as qasm2.barrier((q[i], ...))
#     "measure": None,   # emitted as qasm2.measure(q[i], c[j])
# }


# class UnsupportedGateError(ValueError):
#     """Raised when a Qiskit gate has no mapping to Bloqade's qasm2 API."""


# def _generate_bloqade_source(
#     qc: QuantumCircuit,
#     *,
#     kernel_name: str = "generated",
# ) -> str:
#     """Walk a Qiskit circuit and generate @qasm2.extended Python source.

#     Current layout: one flat kernel with all gates inline, `return c` at the
#     end. Works for the common case where all measurements are terminal.

#     If we ever need to support mid-circuit measurement or parameterized
#     sub-circuits, Bloqade's @qasm2.extended supports typed kernel arguments
#     and kernel-calling-kernel composition (see the QFT example at
#     https://bloqade.quera.com/dev/digital/dialects_and_kernels/qasm2/). We'd
#     emit a "gates" kernel taking a QReg argument and a separate "harness"
#     kernel that allocates registers, invokes gates(q), and handles
#     measurement capture. Leaving that refactor for a future need.
#     """
#     num_qubits = qc.num_qubits
#     num_clbits = qc.num_clbits

#     lines: List[str] = [
#         "from bloqade import qasm2",
#         "",
#         "@qasm2.extended",
#         f"def {kernel_name}():",
#         f"    q = qasm2.qreg({num_qubits})",
#     ]
#     has_creg = num_clbits > 0
#     if has_creg:
#         lines.append(f"    c = qasm2.creg({num_clbits})")

#     for instr in qc.data:
#         name = instr.operation.name
#         qs = [qc.find_bit(b).index for b in instr.qubits]
#         cs = [qc.find_bit(b).index for b in instr.clbits]
#         params = [float(p) for p in instr.operation.params] if instr.operation.params else []

#         if name == "measure":
#             if not cs:
#                 raise UnsupportedGateError(
#                     "measure without a classical bit target is not supported"
#                 )
#             lines.append(f"    qasm2.measure(q[{qs[0]}], c[{cs[0]}])")
#         elif name == "barrier":
#             qubit_tuple = ", ".join(f"q[{i}]" for i in qs)
#             lines.append(f"    qasm2.barrier(({qubit_tuple},))")
#         elif name in _GATE_MAP and _GATE_MAP[name] is not None:
#             lines.append("    " + _GATE_MAP[name](qs, params))
#         else:
#             raise UnsupportedGateError(
#                 f"Qiskit gate {name!r} has no Bloqade qasm2.* equivalent. "
#                 f"Supported gates: {sorted(k for k, v in _GATE_MAP.items() if v)}, "
#                 f"plus 'barrier' and 'measure'."
#             )

#     if has_creg:
#         lines.append("    return c")
#     else:
#         lines.append("    return None")

#     return "\n".join(lines) + "\n"


# def _import_kernel_from_source(source: str, kernel_name: str) -> Any:
#     """
#     Write source to a temp .py file and import it; return the decorated kernel.

#     This dance is required because Bloqade's decorator calls inspect.getsource
#     on the function, which only works if the source is physically on disk
#     (exec()-defined functions fail with "could not get source code").
#     """
#     module_uid = f"_bloq_gen_{uuid.uuid4().hex[:8]}"
#     tmpdir = tempfile.mkdtemp(prefix="bloq_bridge_")
#     path = os.path.join(tmpdir, f"{module_uid}.py")
#     with open(path, "w", encoding="utf-8") as fd:
#         fd.write(source)

#     spec = importlib.util.spec_from_file_location(module_uid, path)
#     if spec is None or spec.loader is None:
#         raise RuntimeError(f"Could not load generated module from {path}")
#     mod = importlib.util.module_from_spec(spec)
#     try:
#         spec.loader.exec_module(mod)
#     except Exception:
#         # Don't leak the temp file if loading fails.
#         try:
#             os.unlink(path)
#             os.rmdir(tmpdir)
#         except OSError:
#             pass
#         raise
#     return getattr(mod, kernel_name)


# def load_qasm_as_bloqade_kernel(qasm_path: str) -> Any:
#     """
#     Parse a QASM2 file (via Qiskit) and return a Bloqade qasm2.extended kernel.

#     Raises
#     ------
#     FileNotFoundError
#         If qasm_path does not exist.
#     ValueError
#         If the file declares OpenQASM 3 (unsupported by Qiskit's from_qasm_file).
#     UnsupportedGateError
#         If the circuit uses a gate that has no direct Bloqade mapping.
#     """
#     if not os.path.isfile(qasm_path):
#         raise FileNotFoundError(f"QASM file not found: {qasm_path}")

#     with open(qasm_path, "r", encoding="utf-8") as fd:
#         header = next(
#             (ln for ln in fd if ln.strip() and not ln.strip().startswith("//")),
#             "",
#         ).strip()
#     if header.startswith("OPENQASM 3"):
#         raise ValueError(
#             "This pipeline targets OpenQASM 2.0 only; got OpenQASM 3. "
#             "Re-export your source with qasm2.dump / qasm2.dumps."
#         )

#     qc = QuantumCircuit.from_qasm_file(qasm_path)
#     source = _generate_bloqade_source(qc)
#     return _import_kernel_from_source(source, "generated")


# def resolve_backend_bloqade(
#     backend_type: BackendType = "stack",
#     *,
#     min_qubits: int = 0,
# ) -> Any:
#     """Return a Bloqade simulator backend."""
#     if backend_type == "stack":
#         from bloqade.pyqrack import StackMemorySimulator
#         return StackMemorySimulator(min_qubits=min_qubits)
#     if backend_type == "dynamic":
#         from bloqade.pyqrack import DynamicMemorySimulator
#         return DynamicMemorySimulator()
#     raise ValueError(
#         f"Unknown backend_type: {backend_type!r}. "
#         f"Expected one of: 'stack', 'dynamic'."
#     )


# def _count_gate_stmts(kernel: Any) -> int:
#     """Count gate-producing statements in the kernel IR."""
#     gate_dialects = {"qasm2.uop", "qasm2.parallel", "qasm2.glob"}
#     count = 0
#     for block in kernel.callable_region.blocks:
#         for stmt in block.stmts:
#             dialect = getattr(stmt, "dialect", None)
#             name = getattr(dialect, "name", None) if dialect is not None else None
#             if name in gate_dialects:
#                 count += 1
#     return count


# def _count_qreg_qubits(qasm_text: str) -> int:
#     total = 0
#     for line in qasm_text.splitlines():
#         stripped = line.strip()
#         if stripped.startswith("qreg"):
#             try:
#                 inside = stripped.split("[", 1)[1].split("]", 1)[0]
#                 total += int(inside)
#             except (IndexError, ValueError):
#                 continue
#     return total


# def optimize_qasm_bloqade(
#     qasm_path: str,
#     backend_type: BackendType = "stack",
#     *,
#     parallelize: bool = True,
#     native_gates: bool = True,
#     fold: bool = True,
#     unroll_ifs: bool = False,
#     min_qubits: Optional[int] = None,
#     verbose: bool = True,
# ) -> BloqadeOptimizeResult:
#     """
#     Load a QASM2 file and optimize it for a Bloqade simulator.

#     Parameters
#     ----------
#     qasm_path
#         Path to an OpenQASM 2.0 file. Internally parsed by Qiskit and
#         re-authored as a native qasm2.extended Bloqade kernel.
#     backend_type
#         'stack'   -> StackMemorySimulator (fixed qubit count).
#         'dynamic' -> DynamicMemorySimulator (runtime qubit allocation).
#     parallelize
#         If True, apply UOpToParallel to fuse independent native gates into
#         parallel-gate blocks. These map to simultaneous pulses on real
#         neutral-atom hardware.
#     native_gates
#         If True, decompose non-native gates (h, x, cx, ...) into Rydberg-
#         native gates (u, rz, cz) before parallelizing. Needed for any real
#         fusion of Clifford-style circuits. Ignored when parallelize=False.
#     fold
#         If True, apply QASM2Fold (constant-fold / dead-code elimination).
#     unroll_ifs
#         If True, flatten conditional branches via UnrollIfs.
#     min_qubits
#         Simulator preallocation ('stack' backend). If None, inferred from the
#         transpiled QASM's qreg declarations.
#     verbose
#         Print a before/after summary.

#     Returns
#     -------
#     BloqadeOptimizeResult
#         Fields include `kernel`, `backend`, before/after QASM, passes applied,
#         and gate-count metrics.

#     Raises
#     ------
#     FileNotFoundError, ValueError, UnsupportedGateError
#         See `load_qasm_as_bloqade_kernel` for details.
#     """
#     kernel = load_qasm_as_bloqade_kernel(qasm_path)

#     original_qasm = QASM2().emit_str(kernel)
#     original_gates = _count_gate_stmts(kernel)
#     original_qubits = _count_qreg_qubits(original_qasm)

#     passes_applied: List[str] = []

#     if fold:
#         QASM2Fold(kernel.dialects)(kernel)
#         passes_applied.append("QASM2Fold")

#     if unroll_ifs:
#         UnrollIfs(kernel.dialects)(kernel)
#         passes_applied.append("UnrollIfs")

#     if parallelize:
#         UOpToParallel(
#             kernel.dialects,
#             rewrite_to_native_first=native_gates,
#         )(kernel)
#         tag = "UOpToParallel"
#         if native_gates:
#             tag += "(native_gates)"
#         passes_applied.append(tag)

#     transpiled_qasm = QASM2().emit_str(kernel)
#     transpiled_gates = _count_gate_stmts(kernel)
#     transpiled_qubits = _count_qreg_qubits(transpiled_qasm) or original_qubits

#     resolved_min_qubits = (
#         min_qubits if min_qubits is not None else transpiled_qubits
#     )
#     backend = resolve_backend_bloqade(
#         backend_type, min_qubits=resolved_min_qubits
#     )

#     result = BloqadeOptimizeResult(
#         kernel=kernel,
#         backend=backend,
#         backend_name=type(backend).__name__,
#         backend_type=backend_type,
#         original_qasm=original_qasm,
#         transpiled_qasm=transpiled_qasm,
#         original_gates=original_gates,
#         transpiled_gates=transpiled_gates,
#         original_qubits=original_qubits,
#         transpiled_qubits=transpiled_qubits,
#         passes_applied=tuple(passes_applied),
#     )

#     if verbose:
#         print(result.summary())

#     return result
