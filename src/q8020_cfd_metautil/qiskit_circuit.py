"""
Qiskit circuit execution with noise modeling and backend management.
"""
import time
from qiskit import QuantumCircuit, transpile
from qiskit.transpiler import CouplingMap
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, thermal_relaxation_error
from qiskit_ibm_runtime import fake_provider


def _get_fake_backend(name: str):
    """Get a fake backend by name (internal helper)."""
    normalized = name.strip().capitalize()
    class_name = f"Fake{normalized}V2"
    
    if hasattr(fake_provider, class_name):
        backend_class = getattr(fake_provider, class_name)
        return backend_class()
    
    available = [attr.replace("Fake", "").replace("V2", "").lower() 
                 for attr in dir(fake_provider) 
                 if attr.startswith("Fake") and attr.endswith("V2")]
    raise ValueError(f"Unknown backend '{name}'. Available: {available}")


def _build_thermal_noise(t1: float, t2: float) -> NoiseModel:
    """Build thermal relaxation noise model from T1/T2 (internal helper)."""
    # Clamp T2 to valid range: T2 must be <= 2*T1
    if t2 > 2 * t1:
        t2 = 2 * t1
    
    noise_model = NoiseModel()
    
    # Gate times in microseconds (typical for superconducting qubits)
    gate_time_1q = 0.05  # 50 ns for single-qubit gates
    gate_time_2q = 0.3   # 300 ns for two-qubit gates
    
    error_1q = thermal_relaxation_error(t1, t2, gate_time_1q)
    error_2q = thermal_relaxation_error(t1, t2, gate_time_2q).expand(
        thermal_relaxation_error(t1, t2, gate_time_2q))
    
    noise_model.add_all_qubit_quantum_error(error_1q, ['h', 'x', 'y', 'z', 'ry', 'rz', 'rx', 'sx'])
    noise_model.add_all_qubit_quantum_error(error_2q, ['cx'])
    
    return noise_model


def get_backend(
    name: str | None = None,
    t1: float | None = None,
    t2: float | None = None,
    coupling_map: str = "default"
) -> AerSimulator:
    """
    Get a configured AerSimulator, optionally based on a fake backend with custom noise.
    
    Args:
        name: Fake backend name (e.g., 'manila', 'jakarta'). Case-insensitive.
              If None, returns a basic AerSimulator.
        t1: T1 relaxation time in µs. Overrides backend's noise if provided.
        t2: T2 dephasing time in µs. Must be <= 2*T1.
        coupling_map: "default" (backend's native) or "all-to-all" (full connectivity).
    
    Returns:
        Configured AerSimulator ready for transpilation and execution.
    
    Examples:
        get_backend("manila")              # Manila topology + realistic noise
        get_backend("manila", t1=50, t2=70) # Manila topology + custom noise
        get_backend(t1=50, t2=70)          # No topology, custom noise only
        get_backend()                       # Ideal simulator, no noise
    """
    fake_backend = None
    noise_model = None
    coupling_map_obj = None
    
    # Get fake backend if name specified
    if name is not None:
        fake_backend = _get_fake_backend(name)
        
        # Handle coupling map
        if coupling_map == "all-to-all":
            coupling_map_obj = CouplingMap.from_full(fake_backend.num_qubits)
        else:
            coupling_map_obj = fake_backend.coupling_map
        
        # Use backend's noise unless t1/t2 override
        if t1 is None or t2 is None:
            noise_model = NoiseModel.from_backend(fake_backend)
    
    # Build custom noise model if t1/t2 provided
    if t1 is not None and t2 is not None and t1 > 0 and t2 > 0:
        noise_model = _build_thermal_noise(t1, t2)
    
    # Create simulator
    if fake_backend is not None:
        simulator = AerSimulator.from_backend(fake_backend)
        if coupling_map == "all-to-all":
            # Override coupling map for all-to-all
            simulator.set_options(coupling_map=list(coupling_map_obj.get_edges()))
    else:
        simulator = AerSimulator()
    
    # Attach noise model if any
    if noise_model is not None:
        simulator.set_options(noise_model=noise_model)
    
    return simulator


def get_backend_info(backend: AerSimulator) -> dict:
    """
    Extract backend information for JSON output.
    
    Args:
        backend: AerSimulator instance from get_backend().
    
    Returns:
        Dict with backend name, has_noise flag, and coupling_map if present.
    """
    info = {"name": backend.name}
    
    options = backend.options
    info["noise"] = hasattr(options, 'noise_model') and options.noise_model is not None
    
    if hasattr(options, 'coupling_map') and options.coupling_map is not None:
        info["coupling_map"] = options.coupling_map
    
    return info


def get_circuit_info(qc: QuantumCircuit) -> dict:
    """
    Extract circuit statistics.
    
    Args:
        qc: Any QuantumCircuit (original or transpiled).
    
    Returns:
        Dict with depth, num_qubits, gate_counts.
    """
    return {
        "depth": qc.depth(),
        "num_qubits": qc.num_qubits,
        "gate_counts": dict(qc.count_ops()),
    }


def transpile_circuit(
    qc: QuantumCircuit,
    backend: AerSimulator,
    optimization_level: int = 1
) -> tuple[QuantumCircuit, dict]:
    """
    Transpile a circuit for a backend.
    
    Args:
        qc: The quantum circuit to transpile.
        backend: AerSimulator from get_backend().
        optimization_level: Transpilation optimization level (0-3).
    
    Returns:
        Tuple of (transpiled_circuit, transpile_info_dict).
    """
    transpile_start = time.time()
    qc_transpiled = transpile(qc, backend=backend, optimization_level=optimization_level)
    transpile_time = time.time() - transpile_start
    
    transpile_info = {
        "wall_time": transpile_time,
        "optimization_level": optimization_level,
        "before": get_circuit_info(qc),
        "after": get_circuit_info(qc_transpiled),
    }
    
    return qc_transpiled, transpile_info


def execute_circuit_counts(
    qc_transpiled: QuantumCircuit,
    backend: AerSimulator,
    shots: int = 1024
) -> tuple[dict[str, int], dict]:
    """
    Execute a transpiled circuit on a backend.
    
    Args:
        qc_transpiled: The transpiled quantum circuit to execute.
        backend: AerSimulator from get_backend() (noise already configured).
        shots: Number of shots.
    
    Returns:
        Tuple of (counts_dict, execution_info_dict).
    """
    execute_start = time.time()
    result = backend.run(qc_transpiled, shots=shots).result()
    execute_time = time.time() - execute_start
    
    exec_info = {
        "wall_time": execute_time,
        "backend_time": result.results[0].time_taken,
        "shots_requested": shots,
        "shots_executed": result.results[0].shots,
        "status": result.results[0].status.name,
    }
    
    return result.get_counts(), exec_info


