"""Metadata fragment system for quantum CFD experiments.

Provides:
    ID generation: generate_experiment_id, generate_workflow_id
    User/env capture: make_user_meta, make_library_meta
    Dict builders: make_experiment_meta, make_case_meta, make_code_meta,
                   make_backend_meta, make_axequalsb_case
    Fragment I/O: write_fragment, write_experiment, ..., read_fragments
    Constants: VALID_SECTIONS, SINGLETON_SECTIONS, MULTI_SECTIONS

Usage:
    from q8020_cfd_metautil.meta_fragment import (
        make_experiment_meta, write_experiment
    )
    data = make_experiment_meta(name="my_run")
    write_experiment(outdir, data)
"""

import getpass
import importlib.metadata
import json
import platform
import re
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_SECTIONS = frozenset([
    "experiment",
    "case",
    "code",
    "backend",
    "artifacts",
    "exec_stats",
    "results",
    "analysis",
])

SINGLETON_SECTIONS = frozenset(["experiment", "case", "code", "backend"])
MULTI_SECTIONS = frozenset(["artifacts", "exec_stats", "results", "analysis"])

# Pattern: q8020_{section}_{index}.json or q8020_{section}_{exp_id}_{index}.json
# Also: q8020_sweep_{section}_{index}.json or q8020_sweep_{section}_{exp_id}_{index}.json
FRAGMENT_PATTERN = re.compile(r"^q8020_([a-z_]+)_(?:([a-f0-9]{8})_)?(\d+)\.json$")
SWEEP_FRAGMENT_PATTERN = re.compile(r"^q8020_sweep_([a-z_]+)_(?:([a-f0-9]{8})_)?(\d+)\.json$")


# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------

def _generate_id() -> str:
    """Generate a unique ID (8 hex chars = 32 bits)."""
    return uuid.uuid4().hex[:8]


def generate_experiment_id() -> str:
    """Generate a unique experiment ID (bare 8-char hex)."""
    return _generate_id()


def generate_workflow_id(experiment_id: str | None = None) -> str:
    """
    Generate a workflow ID with _ prefix.

    For sweeps: generates a new workflow ID.
    For single runs: uses the experiment_id so _<exp_id>/<exp_id> structure.

    Args:
        experiment_id: If provided, workflow_id = _<experiment_id>
    """
    if experiment_id:
        return f"_{experiment_id}"
    return f"_{_generate_id()}"


# ---------------------------------------------------------------------------
# Environment capture
# ---------------------------------------------------------------------------

def make_user_meta() -> dict[str, str]:
    """Capture username and hostname."""
    try:
        username = getpass.getuser()
    except Exception:  # pylint: disable=broad-exception-caught
        username = "unknown"

    try:
        hostname = socket.gethostname()
    except Exception:  # pylint: disable=broad-exception-caught
        hostname = "unknown"

    return {"username": username, "hostname": hostname}


def make_library_meta() -> dict[str, str]:
    """Capture versions of all installed packages plus Python version."""
    versions = {}
    for dist in importlib.metadata.distributions():
        versions[dist.name] = dist.version
    versions["python"] = platform.python_version()
    return dict(sorted(versions.items()))


# ---------------------------------------------------------------------------
# Dict builders
# ---------------------------------------------------------------------------

def make_experiment_meta(
    name: str,
    experiment_id: str | None = None,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """
    Build full experiment metadata dict.

    Args:
        name: User-provided display name for the experiment
        experiment_id: Optional experiment ID; auto-generated if None
        workflow_id: Optional workflow ID; auto-generated if None

    Returns:
        Full experiment dict ready for write_experiment
    """
    exp_id = experiment_id or generate_experiment_id()
    wf_id = workflow_id or exp_id  # When standalone, wf_id = exp_id
    return {
        "name": name,
        "experiment_id": exp_id,
        "workflow_id": wf_id,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "user": make_user_meta(),
    }


def make_case_meta(name: str, **extras: Any) -> dict[str, Any]:
    """
    Build case dict describing the problem being solved.

    Args:
        name: Case type name (e.g., "ax_equals_b", "vqe_molecule")
        **extras: Case-specific fields (from make_axequalsb_case, etc.)

    Returns:
        Case dict ready for write_case
    """
    return {**extras, "name": name}


def make_axequalsb_case(
    name: str,
    matrix: list,
    rhs: list,
    dimension: int,
    condition_number: float,
    **extras: Any,
) -> dict[str, Any]:
    """
    Build case dict for Ax=b linear system problems.

    Args:
        name: Case name identifier
        matrix: The matrix A as nested list
        rhs: The right-hand side vector b as list
        dimension: Problem dimension
        condition_number: Condition number of the matrix
        **extras: Additional fields (eigenvalues, scale_factor, etc.)

    Returns:
        Case dict ready for write_case
    """
    case = {
        "name": name,
        "matrix": matrix,
        "rhs": rhs,
        "dimension": dimension,
        "condition_number": condition_number,
    }
    case.update(extras)
    return case


def make_code_meta(
    algorithm: str,
    entry_point: str,
    run_args: dict[str, Any] | None = None,
    **extras: Any,
) -> dict[str, Any]:
    """
    Build code dict describing the implementation.

    Args:
        algorithm: Algorithm token name (e.g., "hhl", "vqls", "cks")
        entry_point: Entry point script (e.g., "ax_equals_b_hhl.py")
        run_args: Algorithm-specific configuration. Use vars(args) to convert
                  argparse Namespace to dict, or pass a dict directly.
        **extras: Additional code-specific fields

    Returns:
        Code dict ready for write_code
    """
    code: dict[str, Any] = {
        "algorithm": algorithm,
        "entry_point": entry_point,
    }
    if run_args is not None:
        code["run_args"] = run_args
    code.update(extras)
    return code


def _make_ibm_backend_meta(backend: Any) -> dict[str, Any]:
    """
    Build backend dict for IBM/Qiskit AerSimulator backends.

    Extracts noise model details (T1, T2, gate errors, readout
    errors) when the simulator was constructed from a fake or
    real backend via AerSimulator.from_backend().
    """
    info: dict[str, Any] = {
        "name": backend.name,
        "vendor": "ibm",
        "type": "simulator",
    }

    options = backend.options
    has_noise = (
        hasattr(options, "noise_model")
        and options.noise_model is not None
    )
    info["noise"] = has_noise

    # Num qubits
    if hasattr(backend, "num_qubits"):
        info["num_qubits"] = backend.num_qubits

    # Coupling map: try configuration() first, then options
    try:
        cfg = backend.configuration()
        cm = getattr(cfg, "coupling_map", None)
        if cm is not None:
            info["coupling_map"] = [list(e) for e in cm]
    except Exception:
        pass
    if "coupling_map" not in info:
        cm = getattr(options, "coupling_map", None)
        if cm is not None:
            if hasattr(cm, "get_edges"):
                info["coupling_map"] = [
                    list(e) for e in cm.get_edges()
                ]
            else:
                info["coupling_map"] = cm

    # Basis gates: prefer noise model (real hw gates) over
    # configuration (includes all simulator builtins).
    if has_noise:
        noise_model = options.noise_model
        bg = getattr(noise_model, "basis_gates", None)
        if bg:
            info["basis_gates"] = list(bg)
    if "basis_gates" not in info:
        bg = getattr(options, "basis_gates", None)
        if bg is not None:
            info["basis_gates"] = list(bg)
    if "basis_gates" not in info:
        try:
            cfg = backend.configuration()
            bg = getattr(cfg, "basis_gates", None)
            if bg is not None:
                info["basis_gates"] = list(bg)
        except Exception:
            pass

    # Extract noise model details (errors per qubit/gate)
    if has_noise:
        _extract_noise_model_details(info, noise_model)

    # Extract per-qubit T1/T2 and dt from target (available when
    # simulator was built via AerSimulator.from_backend()).
    target = getattr(backend, "target", None)
    if target is not None:
        qubit_props = getattr(target, "qubit_properties", None)
        if qubit_props:
            t1: dict[int, float] = {}
            t2: dict[int, float] = {}
            for qubit in range(target.num_qubits):
                if qubit < len(qubit_props) and qubit_props[qubit]:
                    props = qubit_props[qubit]
                    if hasattr(props, "t1") and props.t1 is not None:
                        t1[qubit] = props.t1 * 1e6  # seconds -> µs
                    if hasattr(props, "t2") and props.t2 is not None:
                        t2[qubit] = props.t2 * 1e6
            if t1:
                info["t1"] = t1
            if t2:
                info["t2"] = t2
        dt = getattr(target, "dt", None)
        if dt is not None:
            info["dt"] = dt

    return info


def _extract_noise_model_details(
    info: dict[str, Any],
    noise_model: Any,
) -> None:
    """Extract T1, T2, gate errors, readout errors from noise model."""
    try:
        nm_dict = noise_model.to_dict()
        readout_errors: dict[int, list[float]] = {}
        gate_error_summary: dict[str, dict[str, float]] = {}

        for entry in nm_dict.get("errors", []):
            etype = entry.get("type", "")
            gate_qubits = entry.get("gate_qubits", [])
            qubits = gate_qubits[0] if gate_qubits else ()
            ops = entry.get("operations", [])
            gate = ops[0] if ops else ""

            if etype == "roerror" and len(qubits) == 1:
                probs = entry.get("probabilities", [])
                if len(probs) >= 2:
                    readout_errors[qubits[0]] = [
                        float(probs[0][1])
                        if len(probs[0]) > 1 else 0.0,
                        float(probs[1][0])
                        if len(probs[1]) > 0 else 0.0,
                    ]

            if etype == "qerror" and gate:
                qargs_key = ",".join(
                    str(q) for q in qubits
                )
                if gate not in gate_error_summary:
                    gate_error_summary[gate] = {}
                probs = entry.get("probabilities", [])
                if probs and len(probs) > 1:
                    gate_error_summary[gate][
                        qargs_key
                    ] = round(1.0 - float(probs[0]), 8)

        if readout_errors:
            info["readout_error"] = readout_errors
        if gate_error_summary:
            info["gate_error"] = gate_error_summary
    except Exception:
        # Noise model extraction is best-effort
        pass


def _make_ibm_runtime_backend_meta(backend: Any) -> dict[str, Any]:
    """
    Build backend dict for IBM Runtime backends (real hardware).

    Args:
        backend: IBMBackend instance from qiskit_ibm_runtime

    Returns:
        Backend dict ready for write_backend
    """
    target = backend.target

    # Coupling map as list of edges
    coupling_map = None
    cm = target.build_coupling_map()
    if cm is not None:
        coupling_map = [list(edge) for edge in cm.get_edges()]

    # Per-qubit properties
    t1: dict[int, float] = {}
    t2: dict[int, float] = {}
    readout_error: dict[int, float] = {}

    for qubit in range(target.num_qubits):
        qubit_props = target.qubit_properties
        if (
            qubit_props
            and qubit < len(qubit_props)
            and qubit_props[qubit]
        ):
            props = qubit_props[qubit]
            if hasattr(props, "t1") and props.t1 is not None:
                t1[qubit] = props.t1 * 1e6
            if hasattr(props, "t2") and props.t2 is not None:
                t2[qubit] = props.t2 * 1e6

        if "measure" in target.operation_names:
            measure_props = target.get("measure", (qubit,))
            if measure_props and measure_props.error is not None:
                readout_error[qubit] = measure_props.error

    # Per-gate errors
    gate_error: dict[str, dict[str, float]] = {}
    for gate_name in target.operation_names:
        if gate_name == "measure":
            continue
        gate_error[gate_name] = {}
        for qargs in target.qargs_for_operation_name(gate_name):
            props = target.get(gate_name, qargs)
            if props and props.error is not None:
                qargs_key = ",".join(str(q) for q in qargs)
                gate_error[gate_name][qargs_key] = props.error

    return {
        "name": backend.name,
        "vendor": "ibm",
        "type": "hardware",
        "noise": True,
        "num_qubits": target.num_qubits,
        "coupling_map": coupling_map,
        "basis_gates": list(target.operation_names),
        "t1": t1 or None,
        "t2": t2 or None,
        "readout_error": readout_error or None,
        "gate_error": gate_error or None,
        "dt": target.dt,
    }


def make_backend_meta(backend: Any, **extras: Any) -> dict[str, Any]:
    """
    Build backend dict for any supported backend.

    Supports AerSimulator (qiskit_aer) and IBMBackend
    (qiskit_ibm_runtime). Imports are lazy so neither
    package is a hard dependency of metautil.

    Args:
        backend: A backend instance (AerSimulator or IBMBackend)
        **extras: Additional backend-specific fields to include

    Returns:
        Backend dict ready for write_backend

    Raises:
        TypeError: If backend type is not supported
    """
    # Try AerSimulator
    try:
        from qiskit_aer import AerSimulator
        if isinstance(backend, AerSimulator):
            info = _make_ibm_backend_meta(backend)
            info.update(extras)
            return info
    except ImportError:
        pass

    # Try IBM Runtime backend
    try:
        from qiskit_ibm_runtime import IBMBackend
        if isinstance(backend, IBMBackend):
            info = _make_ibm_runtime_backend_meta(backend)
            info.update(extras)
            return info
    except ImportError:
        pass

    raise TypeError(
        f"Unsupported backend type: {type(backend).__name__}. "
        f"Install qiskit_aer or qiskit_ibm_runtime."
    )


# ---------------------------------------------------------------------------
# Fragment I/O
# ---------------------------------------------------------------------------

def write_fragment(
    outdir: Path,
    section: str,
    data: dict[str, Any],
    index: int = 0,
    prefix: str = "q8020",
    experiment_id: str | None = None,
) -> Path:
    """
    Write a metadata section fragment to a JSON file.

    Args:
        outdir: Directory to write the fragment file
        section: Section name (one of the 8 valid section names)
        data: Dict data to write
        index: Zero-based index for multiple fragments (default 0)
        prefix: File prefix (default "q8020", sweeper uses "q8020_sweep")
        experiment_id: Optional experiment ID to include in filename

    Returns:
        Path to the written file

    Raises:
        ValueError: If section name is not valid
    """
    if section not in VALID_SECTIONS:
        raise ValueError(
            f"Unknown section '{section}'. "
            f"Valid sections: {sorted(VALID_SECTIONS)}"
        )

    if "_source" not in data:
        data["_source"] = "solver"

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if experiment_id:
        filename = f"{prefix}_{section}_{experiment_id}_{index}.json"
    else:
        filename = f"{prefix}_{section}_{index}.json"
    filepath = outdir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return filepath


def write_experiment(outdir: Path, data: dict[str, Any], index: int = 0, prefix: str = "q8020", experiment_id: str | None = None) -> Path:
    """Write experiment section fragment."""
    return write_fragment(outdir, "experiment", data, index, prefix, experiment_id)


def write_case(outdir: Path, data: dict[str, Any], index: int = 0, prefix: str = "q8020", experiment_id: str | None = None) -> Path:
    """Write case section fragment."""
    return write_fragment(outdir, "case", data, index, prefix, experiment_id)


def write_code(outdir: Path, data: dict[str, Any], index: int = 0, prefix: str = "q8020", experiment_id: str | None = None) -> Path:
    """Write code section fragment."""
    return write_fragment(outdir, "code", data, index, prefix, experiment_id)


def write_backend(outdir: Path, data: dict[str, Any], index: int = 0, prefix: str = "q8020", experiment_id: str | None = None) -> Path:
    """Write backend section fragment."""
    return write_fragment(outdir, "backend", data, index, prefix, experiment_id)


def write_artifacts(outdir: Path, data: dict[str, Any], index: int = 0, prefix: str = "q8020", experiment_id: str | None = None) -> Path:
    """Write artifacts section fragment."""
    return write_fragment(outdir, "artifacts", data, index, prefix, experiment_id)


def write_exec_stats(outdir: Path, data: dict[str, Any], index: int = 0, prefix: str = "q8020", experiment_id: str | None = None) -> Path:
    """Write exec_stats section fragment."""
    return write_fragment(outdir, "exec_stats", data, index, prefix, experiment_id)


def write_results(outdir: Path, data: dict[str, Any], index: int = 0, prefix: str = "q8020", experiment_id: str | None = None) -> Path:
    """Write results section fragment."""
    return write_fragment(outdir, "results", data, index, prefix, experiment_id)


def write_analysis(outdir: Path, data: dict[str, Any], index: int = 0, prefix: str = "q8020", experiment_id: str | None = None) -> Path:
    """Write analysis section fragment."""
    return write_fragment(outdir, "analysis", data, index, prefix, experiment_id)


def read_fragments(outdir: Path) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    """
    Read all fragment files from a directory.

    Args:
        outdir: Directory containing fragment files

    Returns:
        Dict keyed by section name, value is list of (index, data) tuples
        sorted by index. Only includes valid section names.
    """
    outdir = Path(outdir)
    if not outdir.exists():
        return {}

    fragments: dict[str, list[tuple[int, dict[str, Any]]]] = {
        section: [] for section in VALID_SECTIONS
    }

    for filepath in outdir.iterdir():
        if not filepath.is_file():
            continue

        # Skip metadata.json (assembled output)
        if filepath.name == "metadata.json" or filepath.name == "q8020_metadata.json":
            continue

        # Try sweep pattern first (more specific), then general pattern
        match = SWEEP_FRAGMENT_PATTERN.match(filepath.name)
        if not match:
            match = FRAGMENT_PATTERN.match(filepath.name)
        if not match:
            continue

        section = match.group(1)
        # Group 2 is optional exp_id, group 3 is index
        index = int(match.group(3))

        if section not in VALID_SECTIONS:
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            fragments[section].append((index, data))
        except (json.JSONDecodeError, OSError):
            # Skip malformed or unreadable files
            continue

    # Sort each section's fragments by index
    for section in fragments:
        fragments[section].sort(key=lambda x: x[0])

    return fragments
