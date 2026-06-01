"""
Standardized command-line argument parsing for quantum experiments.

This module provides reusable argument parser functions that enforce consistent
command-line interfaces across all quantum algorithm scripts. Reduces boilerplate
and ensures uniform parameter naming conventions.
"""

import argparse


def add_noise_args(parser: argparse.ArgumentParser) -> None:
    """
    Add standard noise-related arguments to parser.
    
    Args:
        parser: ArgumentParser to add arguments to
    """
    parser.add_argument("--t1", type=float, default=None,
                        help="T1 relaxation time in µs (default: None = no noise)")
    parser.add_argument("--t2", type=float, default=None,
                        help="T2 dephasing time in µs (default: None = no noise)")


def add_backend_args(parser: argparse.ArgumentParser) -> None:
    """
    Add standard backend and coupling map arguments to parser.
    
    Args:
        parser: ArgumentParser to add arguments to
    """
    parser.add_argument("--backend", type=str, default=None,
                        help="Fake backend name (e.g., 'manila', 'jakarta')")
    parser.add_argument("--coupling-map", type=str, default="default",
                        choices=["default", "all-to-all"],
                        help="Coupling map: default (backend's native) or all-to-all (full connectivity) (default: default)")
    parser.add_argument("--backend-type", type=str, default="sim",
                        choices=["sim", "fake", "hardware"],
                        help="Backend execution mode (default: sim)")


def add_execution_args(parser: argparse.ArgumentParser) -> None:
    """
    Add standard execution arguments to parser.
    
    Args:
        parser: ArgumentParser to add arguments to
    """
    parser.add_argument("--shots", type=int, default=0,
                        help="Number of shots; 0 = statevector mode (Aer "
                             "exact, no sampling).  Use --shots > 0 to "
                             "exercise the real circuit + measurement "
                             "path on the configured backend.")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed (default: None)")


def add_output_args(parser: argparse.ArgumentParser) -> None:
    """
    Add output-related arguments to parser.
    
    Args:
        parser: ArgumentParser to add arguments to
    """
    #parser.add_argument("--file", type=str, default=None,
    #                    help="Write output to file (default: stdout only)")
    parser.add_argument("--outdir", type=str, default=None,
                        help="Write outputs this dir")


def add_id_args(parser: argparse.ArgumentParser) -> None:
    """
    Add experiment and workflow ID arguments to parser.
    
    These are typically passed by q8020-run to ensure the script's output
    matches the workflow folder structure.
    
    Args:
        parser: ArgumentParser to add arguments to
    """
    parser.add_argument("--experiment-id", type=str, default=None,
                        help="Experiment ID (auto-generated if not provided)")
    parser.add_argument("--workflow-id", type=str, default=None,
                        help="Workflow ID (auto-generated if not provided)")


def add_standard_quantum_args(parser: argparse.ArgumentParser) -> None:
    """
    Add all standard quantum execution arguments to parser.
    
    Includes: experiment-id, workflow-id, file, t1, t2, backend, coupling-map, 
              shots (1024), seed, optimization-level (1)
    
    Args:
        parser: ArgumentParser to add arguments to
    """
    add_id_args(parser)
    add_output_args(parser)
    add_noise_args(parser)
    add_backend_args(parser)
    add_execution_args(parser)
    
    # Add optimization level
    parser.add_argument("--optimization-level", type=int, default=1,
                        choices=[0, 1, 2, 3],
                        help="Transpilation optimization level 0-3 (default: 1)")
