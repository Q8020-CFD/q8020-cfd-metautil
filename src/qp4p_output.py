"""
Standardized JSON output structure for quantum experiments.

Provides utilities for creating consistent JSON output across all apps.
"""

import json
import sys
import importlib.metadata
from datetime import datetime
from typing import Optional
import platform
import inspect
import os
import getpass
import socket


def extract_circuit_stats(original_circuit=None, transpiled_circuit=None) -> dict:
    """
    Extract statistics from original and/or transpiled quantum circuits.
    
    Args:
        original_circuit: Optional original QuantumCircuit or list of QuantumCircuits
        transpiled_circuit: Optional transpiled QuantumCircuit or list of QuantumCircuits
    
    Returns:
        Dictionary with circuit statistics for original and/or transpiled circuits
    """
    stats = {}
    
    if original_circuit is not None:
        # Handle list of circuits
        if isinstance(original_circuit, list):
            stats["original_circuits"] = [
                {
                    "qubits": qc.num_qubits,
                    "depth": qc.depth(),
                    "gates": dict(qc.count_ops())
                }
                for qc in original_circuit
            ]
        else:
            # Single circuit
            stats["original_circuit"] = {
                "qubits": original_circuit.num_qubits,
                "depth": original_circuit.depth(),
                "gates": dict(original_circuit.count_ops())
            }
    
    if transpiled_circuit is not None:
        # Handle list of circuits
        if isinstance(transpiled_circuit, list):
            stats["transpiled_circuits"] = [
                {
                    "qubits": qc.num_qubits,
                    "depth": qc.depth(),
                    "gates": dict(qc.count_ops())
                }
                for qc in transpiled_circuit
            ]
        else:
            # Single circuit
            stats["transpiled_circuit"] = {
                "qubits": transpiled_circuit.num_qubits,
                "depth": transpiled_circuit.depth(),
                "gates": dict(transpiled_circuit.count_ops())
            }
    
    return stats


def get_user_info() -> dict:
    """
    Capture user information.
    
    Returns:
        Dictionary with username and hostname
    """
    try:
        username = getpass.getuser()
    except Exception:
        username = "unknown"
    
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "unknown"
    
    return {
        "username": username,
        "hostname": hostname
    }


def get_library_versions() -> dict:
    """
    Capture versions of all installed libraries in the current environment.
    
    Returns:
        Dictionary mapping package names to version strings, including Python version (sorted alphabetically)
    """
    versions = {}
    for dist in importlib.metadata.distributions():
        versions[dist.name] = dist.version
    
    # Add Python version and sort all keys alphabetically
    versions["python"] = platform.python_version()
    return dict(sorted(versions.items()))


def create_standardized_output(
    algorithm: str,
    problem: dict,
    config: Optional[dict] = None,
    script_name: Optional[str] = None,
    results: Optional[dict] = None,
    metrics: Optional[dict] = None,
    circuit_info: Optional[dict] = None,
    backend_info: Optional[dict] = None,
    visualization_data: Optional[dict] = None,
    error: Optional[str] = None,
    original_circuit=None,
    transpiled_circuit=None,
    results_data: Optional[dict] = None,
    config_args=None,
    transpile_result=None
) -> dict:
    """
    Create standardized JSON output structure.
    
    Args:
        algorithm: Algorithm name (e.g., "qpe", "vqe", "grover", "hhl", "cks")
        problem: Problem definition (matrix, molecule, image, etc.)
        config: Optional algorithm configuration parameters
        script_name: Optional script filename (auto-detected if not provided)
        results: Optional algorithm results dictionary (for additional custom results)
        metrics: Optional performance metrics (fidelity, error, etc.)
        circuit_info: Optional circuit statistics
        backend_info: Optional backend information
        visualization_data: Optional visualization data
        error: Optional error message if execution failed
        original_circuit: Optional original QuantumCircuit (stats will be auto-extracted)
        transpiled_circuit: Optional transpiled QuantumCircuit (stats will be auto-extracted)
        results_data: Optional results data (dict with counts, energies, solutions, etc. - will be merged into results)
        config_args: Optional argparse Namespace to extract config from (shots, t1, t2, backend, etc.)
        transpile_result: Optional tuple from transpile_circuit (qc_transpiled, noise_model, simulator, backend_info)
    
    Returns:
        Standardized dictionary ready for JSON serialization
    """
    # Extract from transpile_result if provided (handle single or list)
    if transpile_result is not None:
        # Check if it's a list of transpile results
        if isinstance(transpile_result, list):
            # Extract from first result for now (could aggregate later)
            if len(transpile_result) > 0 and len(transpile_result[0]) >= 4:
                if transpiled_circuit is None:
                    transpiled_circuit = [tr[0] for tr in transpile_result]
                if backend_info is None:
                    backend_info = transpile_result[0][3]  # Use first backend_info
        else:
            # Single transpile result
            if len(transpile_result) >= 4:
                if transpiled_circuit is None:
                    transpiled_circuit = transpile_result[0]
                if backend_info is None:
                    backend_info = transpile_result[3]
    
    # Auto-detect script_name if not provided
    if script_name is None:
        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_file = frame.f_back.f_code.co_filename
            script_name = os.path.basename(caller_file)
        else:
            script_name = "unknown.py"
    
    # Extract config from args if provided
    if config_args is not None:
        if config is None:
            config = {}
        # Extract common quantum execution parameters
        if hasattr(config_args, 'shots'):
            config['shots'] = config_args.shots
        if hasattr(config_args, 't1'):
            config['t1'] = config_args.t1
        if hasattr(config_args, 't2'):
            config['t2'] = config_args.t2
        if hasattr(config_args, 'backend'):
            config['backend'] = config_args.backend if config_args.backend else "AerSimulator"
        if hasattr(config_args, 'coupling_map'):
            config['coupling_map'] = config_args.coupling_map
        if hasattr(config_args, 'seed'):
            config['seed'] = config_args.seed
    
    library_versions = get_library_versions()
    user_info = get_user_info()
    
    # Initialize results if not provided
    if results is None:
        results = {}
    
    # Merge results_data if provided (can contain counts, energies, solutions, etc.)
    if results_data is not None:
        results = {**results, **results_data}
    
    # Auto-extract circuit stats if circuits are provided
    if original_circuit is not None or transpiled_circuit is not None:
        circuit_stats = extract_circuit_stats(original_circuit, transpiled_circuit)
        results = {**results, **circuit_stats}
    
    output = {
        "algorithm": algorithm,
        "script_name": script_name,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "user_info": user_info,
        "status": "error" if error else "success",
        "library_versions": library_versions
    }
    
    # Add backend_info early if present
    if backend_info is not None:
        output["backend_info"] = backend_info
    
    # Continue with rest of output
    output["problem"] = problem
    output["config"] = config
    output["results"] = results
    
    # Add metrics if provided
    if metrics:
        output["metrics"] = metrics
    
    if circuit_info is not None:
        output["circuit_info"] = circuit_info
    
    if visualization_data is not None:
        output["visualization_data"] = visualization_data
    
    if error is not None:
        output["error"] = error
        output["status"] = "error"
    else:
        output["status"] = "success"
    
    return output


def output_json(
    algorithm: str,
    problem: dict,
    config: Optional[dict] = None,
    script_name: Optional[str] = None,
    results: Optional[dict] = None,
    metrics: Optional[dict] = None,
    circuit_info: Optional[dict] = None,
    backend_info: Optional[dict] = None,
    visualization_data: Optional[dict] = None,
    error: Optional[str] = None,
    original_circuit=None,
    transpiled_circuit=None,
    results_data: Optional[dict] = None,
    config_args=None,
    transpile_result=None,
    file_handle=None,
    indent: int = 2
) -> None:
    """
    Create standardized JSON output and write to stdout or file.
    
    Args:
        algorithm: Algorithm name (e.g., "qpe", "vqe", "grover", "hhl", "cks")
        problem: Problem definition (matrix, molecule, image, etc.)
        config: Optional algorithm configuration parameters
        script_name: Optional script filename (auto-detected if not provided)
        results: Optional algorithm results dictionary (for additional custom results)
        metrics: Optional performance metrics (fidelity, error, etc.)
        circuit_info: Optional circuit statistics
        backend_info: Optional backend information
        visualization_data: Optional visualization data
        error: Optional error message if execution failed
        original_circuit: Optional original QuantumCircuit (stats will be auto-extracted)
        transpiled_circuit: Optional transpiled QuantumCircuit (stats will be auto-extracted)
        results_data: Optional results data (dict with counts, energies, solutions, etc. - will be merged into results)
        config_args: Optional argparse Namespace to extract config from (shots, t1, t2, backend, etc.)
        transpile_result: Optional tuple from transpile_circuit (qc_transpiled, noise_model, simulator, backend_info)
        file_handle: Optional file handle to write to (default: stdout)
        indent: Indentation level for JSON output
    """
    # Create the standardized output
    data = create_standardized_output(
        algorithm=algorithm,
        problem=problem,
        config=config,
        script_name=script_name,
        results=results,
        metrics=metrics,
        circuit_info=circuit_info,
        backend_info=backend_info,
        visualization_data=visualization_data,
        error=error,
        original_circuit=original_circuit,
        transpiled_circuit=transpiled_circuit,
        results_data=results_data,
        config_args=config_args,
        transpile_result=transpile_result
    )
    
    # Output the JSON
    _write_json(data, file_handle, indent)


def _write_json(data: dict, file_handle=None, indent: int = 2) -> None:
    """
    Output JSON to stdout or file with library_versions and backend_info as compact single-line JSON.
    
    Args:
        data: Dictionary to serialize
        file_handle: Optional file handle to write to (default: stdout)
        indent: Indentation level
    """
    data_copy = data.copy()
    replacements = {}
    
    # Extract and compact library_versions if present
    if "library_versions" in data:
        lib_versions_compact = json.dumps(data["library_versions"], separators=(',', ':'))
        data_copy["library_versions"] = "__LIBRARY_VERSIONS_PLACEHOLDER__"
        replacements['"__LIBRARY_VERSIONS_PLACEHOLDER__"'] = lib_versions_compact
    
    # Extract and compact backend_info if present
    if "backend_info" in data:
        backend_info_compact = json.dumps(data["backend_info"], separators=(',', ':'))
        data_copy["backend_info"] = "__BACKEND_INFO_PLACEHOLDER__"
        replacements['"__BACKEND_INFO_PLACEHOLDER__"'] = backend_info_compact
    
    # Pretty print the main structure
    output = json.dumps(data_copy, indent=indent)
    
    # Replace placeholders with compact versions
    for placeholder, compact_value in replacements.items():
        output = output.replace(placeholder, compact_value)
    
    # Write to file or stdout
    if file_handle is not None:
        file_handle.write(output + '\n')
    else:
        print(output)


def output_error(algorithm: str, script_name: str, error_message: str, partial_data: Optional[dict] = None) -> None:
    """
    Output standardized error JSON and exit.
    
    Args:
        algorithm: Algorithm name
        script_name: Name of the script file
        error_message: Error description
        partial_data: Optional partial results before error
    """
    output = {
        "algorithm": algorithm,
        "script_name": script_name,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "status": "error",
        "error": error_message,
        "library_versions": get_library_versions()
    }
    
    if partial_data:
        output.update(partial_data)
    
    _write_json(output)
    sys.exit(1)


def normalize_param_name(name: str) -> str:
    """
    Normalize parameter names by replacing hyphens with underscores.
    
    Args:
        name: Parameter name (may contain hyphens)
    
    Returns:
        Normalized name with underscores
    """
    return name.replace('-', '_')


def normalize_params(params: dict) -> dict:
    """
    Normalize all parameter names in a dictionary.
    
    Args:
        params: Dictionary with potentially hyphenated keys
    
    Returns:
        Dictionary with normalized keys
    """
    return {normalize_param_name(k): v for k, v in params.items()}
