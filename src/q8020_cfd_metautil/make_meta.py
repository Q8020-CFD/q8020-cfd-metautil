"""
Standardized JSON output for quantum CFD experiments.

Helpers: make_case_meta, make_axequalsb_case, make_code_meta,
         make_experiment_id_meta, make_experiment_meta, make_backend_meta
Main: make_meta
"""

import importlib.metadata
from datetime import datetime, timezone
from typing import Any, Optional
import platform
import getpass
import socket
import uuid

from qiskit_aer import AerSimulator


def _generate_id() -> str:
    """Generate a unique ID (8 hex chars = 32 bits)."""
    return uuid.uuid4().hex[:8]


def _generate_experiment_id() -> str:
    """Generate a unique experiment ID (bare 16-char hex)."""
    return _generate_id()


def _generate_workflow_id(experiment_id: Optional[str] = None) -> str:
    """
    Generate a workflow ID with wf_ prefix.
    
    For sweeps: generates a new workflow ID.
    For single runs: uses the experiment_id so wf_<exp_id>/<exp_id> structure.
    
    Args:
        experiment_id: If provided, workflow_id = wf_<experiment_id>
    """
    if experiment_id:
        return f"wf_{experiment_id}"
    return f"wf_{_generate_id()}"


def _make_user_meta() -> dict[str, str]:
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


def _make_library_meta() -> dict[str, str]:
    """Capture versions of all installed packages plus Python version."""
    versions = {}
    for dist in importlib.metadata.distributions():
        versions[dist.name] = dist.version
    versions["python"] = platform.python_version()
    return dict(sorted(versions.items()))


def make_case_meta(name: str, **extras: Any) -> dict[str, Any]:
    """
    Build case dict describing the problem being solved.

    Args:
        name: Case type name (e.g., "ax_equals_b", "vqe_molecule")
        **extras: Case-specific fields (from make_axequalsb_case, etc.)

    Returns:
        Case dict ready for create_experiment_meta
    """
    return {**extras, "name": name}


def make_axequalsb_case(
    name: str,
    matrix: list,
    rhs: list,
    dimension: int,
    condition_number: float,
    **extras: Any
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
        Case dict ready for create_experiment_meta
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
    run_args: Optional[dict[str, Any]] = None,
    **extras: Any
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
        Code dict ready for create_experiment_output
    """
    code = {
        "algorithm": algorithm,
        "entry_point": entry_point,
    }
    if run_args is not None:
        code["run_args"] = run_args
    code.update(extras)
    return code


def _make_ibm_backend_meta(backend: AerSimulator) -> dict[str, Any]:
    """
    Build backend dict for IBM/Qiskit backends.

    Args:
        backend: AerSimulator

    Returns:
        Backend dict ready for create_experiment_meta
    """
    info = {"name": backend.name, "vendor": "ibm"}
    
    options = backend.options
    info["noise"] = hasattr(options, 'noise_model') and options.noise_model is not None
    
    if hasattr(options, 'coupling_map') and options.coupling_map is not None:
        info["coupling_map"] = options.coupling_map
    
    return info


def make_backend_meta(backend: Any) -> dict[str, Any]:
    """
    Build backend dict for any supported backend.

    Args:
        backend: A backend instance (currently supports AerSimulator)

    Returns:
        Backend dict ready for create_experiment_meta

    Raises:
        TypeError: If backend type is not supported
    """
    if isinstance(backend, AerSimulator):
        return _make_ibm_backend_meta(backend)
    raise TypeError(f"Unsupported backend type: {type(backend).__name__}")


def make_experiment_meta(
    name: str,
    experiment_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build full experiment metadata dict.

    Args:
        name: User-provided display name for the experiment
        experiment_id: Optional experiment ID; auto-generated if None
        workflow_id: Optional workflow ID; auto-generated if None

    Returns:
        Full experiment dict ready for create_experiment_meta
    """
    exp_id = experiment_id or _generate_experiment_id()
    wf_id = workflow_id or exp_id  # When standalone, wf_id = exp_id
    return {
        "name": name,
        "experiment_id": exp_id,
        "workflow_id": wf_id,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "user": _make_user_meta(),
    }



# ---------------------------------------------------------------------------
# Main output function
# ---------------------------------------------------------------------------

def make_meta(
    experiment: dict[str, Any],
    case: dict[str, Any],
    code: dict[str, Any],
    backend: dict[str, Any],
    artifacts: Optional[dict[str, Any]] = None,
    exec_stats: Optional[dict[str, Any]] = None,
    results: Optional[dict[str, Any]] = None,
    analysis: Optional[dict[str, Any]] = None,
    error: Optional[str] = None
) -> dict[str, Any]:
    """
    Create standardized experiment output.

    Args:
        experiment: Full experiment metadata (from make_experiment_meta)
        case: Problem definition (from make_case or dict)
        code: Implementation details (from make_code or dict)
        backend: Execution environment (from make_backend or dict)
        artifacts: Experiment artifacts (circuits, intermediate data, etc.)
        exec_stats: Execution statistics dict
        results: Computed solutions dict
        analysis: Experiment-specific analysis (error measures, fidelity, etc.)
        error: Error message if experiment failed (added to experiment dict)

    Returns:
        Complete experiment output dict ready for JSON serialization
    """
    output = {
        "experiment": experiment,
        "case": case,
        "code": {
            **code,
            "library_versions": _make_library_meta(),
        },
        "backend": backend,
    }

    if artifacts is not None:
        output["artifacts"] = artifacts

    if exec_stats is not None:
        output["exec_stats"] = exec_stats

    if results is not None:
        output["results"] = results

    if analysis is not None:
        output["analysis"] = analysis

    if error is not None:
        output["experiment"]["error"] = error

    return output
