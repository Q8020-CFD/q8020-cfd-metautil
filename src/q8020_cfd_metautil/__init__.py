"""
q8020-cfd-metautil: Quantum Algorithm Experimentation Utilities

A comprehensive toolkit for running, analyzing, and comparing quantum algorithms
with standardized output, parameter sweeps, and reproducible experiments.

Module Organization:

Core Execution:
    q8020_circuit - Quantum circuit execution with Qiskit
        Backend management, noise modeling, transpilation, and execution
        
    q8020_output - Standardized JSON output schema
        Consistent output format for all quantum experiments
        
    q8020_args - Command-line argument parsing
        Reusable argument groups for quantum parameters

Algorithm Support:        
    q8020_opt - Classical optimization
        SPSA and other optimizers for variational algorithms

Experimentation:
    q8020_sweeper - Parameter sweep orchestration
        TOML-based configuration and automated experiment execution

Quality Assurance:
    analyze_json_structure - Output validation
        Verify standardized output across all experiments

Design Philosophy:
    1. Standardization: All scripts produce consistent JSON output
    2. Reproducibility: Capture all versions, parameters, and environment info
    3. Automation: Parameter sweeps with minimal boilerplate
    4. Modularity: Reusable components across different algorithms
    5. Quality: Validation tools to ensure output consistency

Typical Workflow:
    1. Write algorithm script using q8020_circuit, q8020_args, q8020_output
    2. Create TOML configuration for parameter sweep
    3. Run sweep with q8020_sweeper
    4. Validate outputs with analyze_json_structure
    5. View results with experiment_explorer

Example Algorithm Script:
    ```python
    import argparse
    from q8020_args import add_standard_quantum_args
    from q8020_circuit import run_circuit
    from q8020_output import output_json
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--molecule", required=True)
    add_standard_quantum_args(parser)
    args = parser.parse_args()
    
    # Build and run circuit
    qc = build_my_circuit(args.molecule)
    result = run_circuit(qc, shots=args.shots, backend=args.backend)
    
    # Output standardized JSON
    output_json(
        algorithm="my_algorithm",
        problem={"molecule": args.molecule},
        config=vars(args),
        results={"energy": result["expectation_value"]},
        original_circuit=qc,
        transpiled_circuit=result["transpiled_circuit"]
    )
    ```

Example Sweep Configuration:
    ```toml
    [global]
    shots = 8192
    backend = "manila"
    
    [group.molecule_study]
    molecule = ["H2", "LiH", "BeH2"]
    basis = ["sto-3g", "6-31g"]
    _script = "src/my_algorithm.py"
    _postproc = ["src/plot_energy.py"]
    ```

Version: 0.1.0
Python: >=3.12,<3.13
"""

__version__ = "0.1.0"

__all__ = [
    "q8020_circuit",
    "q8020_output", 
    "q8020_args",
    "q8020_opt",
    "q8020_sweeper",
    "analyze_json_structure",
]
