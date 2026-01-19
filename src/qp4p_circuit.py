"""
Helper functions for running quantum circuits.
"""
import math
import json
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.visualization import plot_histogram, plot_bloch_multivector
from qiskit.quantum_info import Statevector
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, thermal_relaxation_error
from qiskit.primitives import StatevectorEstimator
from qiskit_aer.primitives import EstimatorV2
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit.transpiler import CouplingMap
from qiskit_ibm_runtime import fake_provider
import matplotlib.pyplot as plt

# Basis gates supported by Aer simulator
BASIS_GATES = ['cx', 'id', 'rz', 'sx', 'x']


def get_fake_backend(name: str):
    """
    Get a fake backend by name.
    
    Args:
        name: Backend name (e.g., 'manila', 'Jakarta', 'BROOKLYN').
              Case-insensitive. Will be normalized to 'Fake<Name>V2'.
    
    Returns:
        Fake backend instance.
    
    Available backends (depends on qiskit-ibm-runtime version):
        1 qubit: armonk
        5 qubits: athens, belem, bogota, casablanca, essex, lima, london, manila,
                  ourense, quito, rome, santiago, valencia, vigo, yorktown
        7 qubits: jakarta, lagos, nairobi
        14 qubits: melbourne
        15 qubits: guadalupe
        16 qubits: almaden, singapore
        20 qubits: boeblingen, johannesburg, poughkeepsie
        27 qubits: cairo, cambridge, hanoi, kolkata, montreal, mumbai, paris, sydney, toronto
        53 qubits: rochester
        65 qubits: brooklyn, manhattan, washington
    """
    # Normalize: first letter uppercase, rest lowercase
    normalized = name.strip().capitalize()
    class_name = f"Fake{normalized}V2"
    
    try:
        
        # Try to get the class dynamically
        if hasattr(fake_provider, class_name):
            backend_class = getattr(fake_provider, class_name)
            return backend_class()
        
        # List available backends for error message
        available = [attr.replace("Fake", "").replace("V2", "").lower() 
                     for attr in dir(fake_provider) 
                     if attr.startswith("Fake") and attr.endswith("V2")]
        raise ValueError(f"Unknown backend '{name}'. Available: {available}")
        
    except ImportError as e:
        raise ImportError(
            f"Could not import fake backend. Install qiskit-ibm-runtime: {e}"
        ) from e


def get_backend_info(backend) -> dict:
    """
    Extract detailed information from a backend.
    
    Args:
        backend: A Qiskit backend instance.
    
    Returns:
        Dict with backend details: name, num_qubits, basis_gates, coupling_map, 
        and per-qubit error statistics.
    """
    if backend is None:
        return None
    
    info = {
        "name": backend.name,
        "num_qubits": backend.num_qubits,
        "basis_gates": list(backend.operation_names),
    }
    
    # Get coupling map
    coupling_map = backend.coupling_map
    if coupling_map is not None:
        info["coupling_map"] = list(coupling_map.get_edges())
    
    # Get error statistics from target
    target = backend.target
    if target is not None:
        qubit_errors = {}
        gate_errors = {}
        
        # Per-qubit T1/T2 if available
        for qubit in range(backend.num_qubits):
            props = {}
            try:
                if hasattr(target, 'qubit_properties') and target.qubit_properties:
                    qp = target.qubit_properties[qubit]
                    if qp:
                        if hasattr(qp, 't1') and qp.t1:
                            props["t1_us"] = round(qp.t1 * 1e6, 2)
                        if hasattr(qp, 't2') and qp.t2:
                            props["t2_us"] = round(qp.t2 * 1e6, 2)
            except (IndexError, AttributeError):
                pass
            if props:
                qubit_errors[qubit] = props
        
        # Per-gate error rates
        for gate_name in backend.operation_names:
            try:
                gate_props = target[gate_name]
                if gate_props:
                    errors = []
                    for qargs, props in gate_props.items():
                        if props and props.error is not None:
                            errors.append({
                                "qubits": list(qargs) if qargs else [],
                                "error": round(props.error, 6)
                            })
                    if errors:
                        gate_errors[gate_name] = errors
            except (KeyError, AttributeError):
                pass
        
        if qubit_errors:
            info["qubit_properties"] = qubit_errors
        if gate_errors:
            info["gate_errors"] = gate_errors
    
    return info


def build_noise_model(t1: float = None, t2: float = None, 
                      backend: str = None, coupling_map: str = "default") -> tuple:
    """
    Build a noise model from T1/T2 parameters and/or a fake backend.
    
    Args:
        t1: T1 relaxation time in microseconds (energy decay). None = no noise.
        t2: T2 dephasing time in microseconds. Must be <= 2*T1.
        backend: Name of fake backend (e.g., 'manila', 'jakarta'). Case-insensitive.
        coupling_map: Coupling map to use. Options:
            - "default": Use backend's coupling map (if backend specified)
            - "all-to-all": Full connectivity (all qubits connected)
    
    Returns:
        Tuple of (noise_model, fake_backend, coupling_map_obj) where any may be None.
        If both backend and t1/t2 are provided, t1/t2 override the backend's values.
    
    Typical values for superconducting qubits: T1 ~ 50-150 µs, T2 ~ 50-120 µs.
    """
    fake_backend = None
    noise_model = None
    coupling_map_obj = None
    
    # Get fake backend if specified
    if backend is not None:
        fake_backend = get_fake_backend(backend)
        noise_model = NoiseModel.from_backend(fake_backend)
        
        # Handle coupling map
        if coupling_map == "default":
            # Use backend's native coupling map
            coupling_map_obj = fake_backend.coupling_map
        elif coupling_map == "all-to-all":
            # Create all-to-all coupling for backend's num_qubits
            num_qubits = fake_backend.num_qubits
            coupling_map_obj = CouplingMap.from_full(num_qubits)
    
    # If t1/t2 provided, build custom noise model (overrides backend)
    if t1 is not None and t1 <= 0:
        t1 = None
    if t2 is not None and t2 <= 0:
        t2 = None
    
    if t1 is not None and t2 is not None:
        # Build custom thermal relaxation noise model
        noise_model = NoiseModel()
        
        # Gate times in microseconds (typical for superconducting qubits)
        gate_time_1q = 0.05  # 50 ns for single-qubit gates
        gate_time_2q = 0.3   # 300 ns for two-qubit gates
        
        # Clamp T2 to valid range: T2 must be <= 2*T1
        if t2 > 2 * t1:
            t2 = 2 * t1
        
        error_1q = thermal_relaxation_error(t1, t2, gate_time_1q)
        error_2q = thermal_relaxation_error(t1, t2, gate_time_2q).expand(
            thermal_relaxation_error(t1, t2, gate_time_2q))
        
        noise_model.add_all_qubit_quantum_error(error_1q, ['h', 'x', 'y', 'z', 'ry', 'rz', 'rx'])
        noise_model.add_all_qubit_quantum_error(error_2q, ['cx'])
    
    return noise_model, fake_backend, coupling_map_obj


def transpile_circuit(qc: QuantumCircuit, args=None, t1: float = None, t2: float = None,
                      backend: str = None, coupling_map: str = "default", 
                      optimization_level: int = 1) -> tuple:
    """
    Transpile a circuit for a specific backend and noise model.
    
    Args:
        qc: The quantum circuit to transpile.
        args: Optional argparse Namespace with t1, t2, backend, coupling_map, optimization_level attributes.
              If provided, these override the individual parameters.
        t1: T1 relaxation time in microseconds (energy decay). None = no noise.
        t2: T2 dephasing time in microseconds (dephasing). Must be <= 2*T1.
        backend: Name of fake backend (e.g., 'manila', 'jakarta'). Case-insensitive.
                 If provided with t1/t2, the t1/t2 values override backend noise.
        coupling_map: "default" (backend's coupling) or "all-to-all" (full connectivity).
        optimization_level: Transpilation optimization level (0-3).
    
    Returns:
        Dict with keys:
            transpiled_circuit: The transpiled quantum circuit
            backend: The AerSimulator backend instance
            noise_model: The noise model (if any)
            backend_info: Backend details dict (includes transpile_time)
    """
    import time
    
    # Extract from args if provided
    if args is not None:
        t1 = getattr(args, 't1', t1)
        t2 = getattr(args, 't2', t2)
        backend = getattr(args, 'backend', backend)
        coupling_map = getattr(args, 'coupling_map', coupling_map)
        optimization_level = getattr(args, 'optimization_level', optimization_level)
    
    noise_model, fake_backend, coupling_map_obj = build_noise_model(t1, t2, backend, coupling_map)
    
    # Capture transpilation timing
    transpile_start = time.time()
    
    # If using a fake backend, transpile to its basis gates and coupling map
    if fake_backend is not None:
        # Only pass coupling_map if it's explicitly overriding the backend's default
        if coupling_map == "all-to-all":
            qc_transpiled = transpile(qc, backend=fake_backend, coupling_map=coupling_map_obj, 
                                     optimization_level=optimization_level)
        else:
            # Use backend's native coupling map (don't pass coupling_map parameter)
            qc_transpiled = transpile(qc, backend=fake_backend, 
                                     optimization_level=optimization_level)
        backend_simulator = AerSimulator.from_backend(fake_backend)
    else:
        qc_transpiled = transpile(qc, optimization_level=optimization_level)
        backend_simulator = AerSimulator()
    
    transpile_end = time.time()
    
    backend_info = get_backend_info(fake_backend)
    
    # Initialize backend_info if None
    if backend_info is None:
        backend_info = {}
    
    # Add transpilation timing to backend_info
    backend_info["transpile_time"] = {
        "start": transpile_start,
        "end": transpile_end,
        "cumulative_seconds": transpile_end - transpile_start
    }
    
    # If t1/t2 override was used, replace qubit_properties with uniform t1/t2
    if t1 is not None and t2 is not None:
        num_qubits = backend_info.get("num_qubits", 0)
        if num_qubits == 0 and fake_backend is None:
            # For AerSimulator without fake backend, get qubit count from circuit
            num_qubits = qc_transpiled.num_qubits
        backend_info["qubit_properties"] = {
            i: {"t1_us": t1, "t2_us": t2} for i in range(num_qubits)
        }
    
    return {
        "transpiled_circuit": qc_transpiled,
        "backend": backend_simulator,
        "noise_model": noise_model,
        "backend_info": backend_info
    }


def execute_circuit(qc_transpiled: QuantumCircuit, backend, noise_model=None, 
                    shots: int = 1024, args=None) -> dict:
    """
    Execute a transpiled circuit on a backend.
    
    Args:
        qc_transpiled: The transpiled quantum circuit to execute.
        backend: The AerSimulator backend instance to use.
        noise_model: Optional noise model to apply.
        shots: Number of shots.
        args: Optional argparse Namespace with shots attribute.
              If provided, overrides the shots parameter.
    
    Returns:
        Dict with counts and execution timing.
    """
    import time
    
    # Extract from args if provided
    if args is not None:
        shots = getattr(args, 'shots', shots)
    
    # Capture execution timing
    execute_start = time.time()
    
    if noise_model is not None:
        result = backend.run(qc_transpiled, shots=shots, noise_model=noise_model).result()
    else:
        result = backend.run(qc_transpiled, shots=shots).result()
    
    execute_end = time.time()
    
    return {
        "counts": result.get_counts(),
        "execute_time": {
            "start": execute_start,
            "end": execute_end,
            "cumulative_seconds": execute_end - execute_start
        }
    }


def run_circuit(qc: QuantumCircuit, shots: int = 1024, 
                t1: float = None, t2: float = None,
                backend: str = None, coupling_map: str = "default") -> dict:
    """
    Run a circuit on the Aer simulator and return results dict.
    
    Convenience function that combines transpile_circuit and execute_circuit.
    
    Args:
        qc: The quantum circuit to run.
        shots: Number of shots.
        t1: T1 relaxation time in microseconds (energy decay). None = no noise.
        t2: T2 dephasing time in microseconds (dephasing). Must be <= 2*T1.
        backend: Name of fake backend (e.g., 'manila', 'jakarta'). Case-insensitive.
                 If provided with t1/t2, the t1/t2 values override backend noise.
        coupling_map: "default" (backend's coupling) or "all-to-all" (full connectivity).
    
    Returns:
        Dict with keys:
            counts: Measurement counts
            transpiled_stats: Circuit stats after transpilation (depth, gate counts)
            backend_info: Backend details if backend specified, else None
    
    Typical values for superconducting qubits: T1 ~ 50-150 µs, T2 ~ 50-120 µs.
    Longer is better.
    """
    # Transpile the circuit
    transpile_result = transpile_circuit(
        qc, t1=t1, t2=t2, backend=backend, coupling_map=coupling_map
    )
    qc_transpiled = transpile_result["transpiled_circuit"]
    backend_simulator = transpile_result["backend"]
    noise_model = transpile_result["noise_model"]
    backend_info = transpile_result["backend_info"]
    
    # Execute the transpiled circuit
    exec_result = execute_circuit(qc_transpiled, backend_simulator, noise_model=noise_model, shots=shots)
    
    # Combine results
    return {
        "counts": exec_result["counts"],
        "transpiled_stats": {
            "depth": qc_transpiled.depth(),
            "gate_counts": dict(qc_transpiled.count_ops()),
            "num_qubits": qc_transpiled.num_qubits
        },
        "backend_info": backend_info
    }


def run_estimator(circuit, observable, shots: int = 1024,
                  t1: float = None, t2: float = None,
                  backend: str = None, coupling_map: str = "default") -> float:
    """
    Estimate expectation value <ψ|O|ψ> using shots-based simulation.
    
    Args:
        circuit: QuantumCircuit (no measurements) to prepare the state.
        observable: SparsePauliOp observable to measure.
        shots: Number of shots for estimation.
        t1: T1 relaxation time in microseconds. None = no noise.
        t2: T2 dephasing time in microseconds. None = no noise.
        backend: Name of fake backend (e.g., 'manila'). Case-insensitive.
        coupling_map: "default" (backend's coupling) or "all-to-all" (full connectivity).
    
    Returns:
        Expectation value as float.
    """
    noise_model, fake_backend, coupling_map_obj = build_noise_model(t1, t2, backend, coupling_map)
    
    if fake_backend is not None:
        # Only pass coupling_map if it's explicitly overriding the backend's default
        if coupling_map == "all-to-all":
            circuit = transpile(circuit, backend=fake_backend, coupling_map=coupling_map_obj)
        else:
            # Use backend's native coupling map (don't pass coupling_map parameter)
            circuit = transpile(circuit, backend=fake_backend)
        simulator = AerSimulator.from_backend(fake_backend)
        if noise_model is not None:
            simulator = AerSimulator.from_backend(fake_backend, noise_model=noise_model)
    elif noise_model is not None:
        simulator = AerSimulator(noise_model=noise_model)
    else:
        simulator = AerSimulator()
    
    estimator = EstimatorV2.from_backend(simulator)
    job = estimator.run([(circuit, observable)], precision=1.0 / np.sqrt(shots))
    result = job.result()
    return float(result[0].data.evs)


def run_circuit_display(circ: QuantumCircuit, args=None, t1: float = None, t2: float = None,
    shots: int = 1024, display: bool = True, backend: str = None, 
    coupling_map: str = "default", optimization_level: int = 1) -> dict:
    """Run a circuit, optionally display graphical views, return all data for output_json.
    
    Args:
        circ: The quantum circuit to run.
        args: Optional argparse Namespace with parameters (takes precedence over individual params).
        t1: T1 relaxation time in microseconds.
        t2: T2 relaxation time in microseconds.
        shots: Number of shots.
        display: Whether to display circuit diagram and histogram (graphical).
        backend: Name of fake backend (e.g., 'manila'). Case-insensitive.
        coupling_map: "default" or "all-to-all".
        optimization_level: Transpilation optimization level (0-3).
    
    Returns:
        Dict with transpile_result tuple and execution results for output_json.
    """
    # Extract parameters from args if provided
    if args is not None:
        t1 = getattr(args, 't1', t1)
        t2 = getattr(args, 't2', t2)
        shots = getattr(args, 'shots', shots)
        backend = getattr(args, 'backend', backend)
        coupling_map = getattr(args, 'coupling_map', coupling_map)
        optimization_level = getattr(args, 'optimization_level', optimization_level)
    
    # Transpile the circuit
    transpile_result = transpile_circuit(
        circ, t1=t1, t2=t2, backend=backend, coupling_map=coupling_map, 
        optimization_level=optimization_level
    )
    
    # Execute the transpiled circuit
    exec_result = execute_circuit(
        transpile_result["transpiled_circuit"], 
        transpile_result["backend"], 
        noise_model=transpile_result["noise_model"], 
        shots=shots
    )
    
    # Only show graphical display if requested
    if display:
        # Create main figure with circuit and histogram
        fig, axes = plt.subplots(1, 2, figsize=(12, 4))
        circ.draw("mpl", ax=axes[0])
        plot_histogram(exec_result["counts"], ax=axes[1])
        plt.tight_layout()
        
        # Create separate figure for Bloch sphere
        circ_no_meas = circ.remove_final_measurements(inplace=False)
        sv = Statevector.from_instruction(circ_no_meas)
        plot_bloch_multivector(sv)
        
        plt.show()
    
    # Return both transpile_result and execution results for output_json
    return {
        "transpile_result": transpile_result,
        "results_data": exec_result
    }


def estimate_shots(num_pixels: int, relative_error: float = 0.1, confidence: float = 0.95) -> int:
    """
    Estimate number of shots needed for image reconstruction.
    
    Using normal approximation: shots ≈ (1 / (2 * ε²)) * ln(2 / δ)
    where ε = relative_error * (1/num_pixels) and δ = 1 - confidence.
    
    For 10% relative error at 95% confidence: shots ≈ 185 * num_pixels²
    """
    delta = 1 - confidence
    epsilon = relative_error / num_pixels
    shots = int((1 / (2 * epsilon**2)) * math.log(2 / delta))
    return shots
