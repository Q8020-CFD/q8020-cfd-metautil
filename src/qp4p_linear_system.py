"""
Linear system generation utilities for Ax=b quantum algorithms.

Provides consistent matrix/vector generation across all Ax=b solvers:
- Random SPD (symmetric positive definite) matrices
- Tridiagonal matrices
- Explicit matrix/vector specification
- Hermitian conversion for algorithms that require it

Usage:
    from qp4p_linear_system import add_linear_system_args, get_linear_system
"""

import argparse
import json
import numpy as np


def next_power_of_2(n: int) -> int:
    """Return the next power of 2 >= n."""
    if n <= 0:
        return 1
    p = 1
    while p < n:
        p *= 2
    return p


def generate_random_spd(size: int, seed: int = None) -> tuple:
    """
    Generate a random symmetric positive definite (SPD) matrix.
    
    SPD matrices are always invertible and well-conditioned.
    Construction: A = R·R^T + n·I
    
    Args:
        size: Matrix dimension
        seed: Random seed for reproducibility
    
    Returns:
        Tuple of (A, b) where A is SPD and b is random vector
    """
    if seed is not None:
        np.random.seed(seed)
    
    R = np.random.randn(size, size)
    A = R @ R.T + size * np.eye(size)
    b = np.random.randn(size)
    
    return A, b


def generate_tridiagonal(size: int, diag: float = 2.0, off_diag: float = -1.0, 
                         seed: int = None) -> tuple:
    """
    Generate a tridiagonal matrix.
    
    Tridiagonal matrices are common in physics (e.g., discretized Laplacian).
    Default values create a symmetric positive definite matrix.
    
    Args:
        size: Matrix dimension
        diag: Main diagonal value (default: 2.0)
        off_diag: Off-diagonal value (default: -1.0)
        seed: Random seed for b vector
    
    Returns:
        Tuple of (A, b) where A is tridiagonal
    """
    if seed is not None:
        np.random.seed(seed)
    
    A = np.diag([diag] * size)
    if size > 1:
        A += np.diag([off_diag] * (size - 1), k=1)
        A += np.diag([off_diag] * (size - 1), k=-1)
    
    b = np.random.randn(size)
    
    return A, b


def make_hermitian(A: np.ndarray) -> np.ndarray:
    """
    Convert a matrix to Hermitian form.
    
    For real matrices, this makes it symmetric.
    For complex matrices, this makes it Hermitian (A = A†).
    
    Args:
        A: Input matrix
    
    Returns:
        Hermitian matrix (A + A†) / 2
    """
    return (A + A.conj().T) / 2


def parse_matrix(s: str) -> np.ndarray:
    """Parse a matrix from JSON string like '[[2,1],[1,2]]'."""
    return np.array(json.loads(s), dtype=float)


def parse_vector(s: str) -> np.ndarray:
    """Parse a vector from JSON string like '[1,0]'."""
    return np.array(json.loads(s), dtype=float)


def pad_to_power_of_2(A: np.ndarray, b: np.ndarray) -> tuple:
    """
    Pad matrix A and vector b to next power of 2 dimension.
    
    Required for quantum algorithms that need 2^n dimensions.
    
    Args:
        A: Square matrix
        b: Vector
    
    Returns:
        Tuple of (A_padded, b_padded, original_size)
    """
    n = A.shape[0]
    n_padded = next_power_of_2(n)
    
    if n_padded == n:
        return A, b, n
    
    A_padded = np.eye(n_padded, dtype=A.dtype)
    A_padded[:n, :n] = A
    
    b_padded = np.zeros(n_padded, dtype=b.dtype)
    b_padded[:n] = b
    
    return A_padded, b_padded, n


def add_linear_system_args(parser: argparse.ArgumentParser):
    """
    Add standard linear system arguments to an argument parser.
    
    Adds mutually compatible arguments for:
    - Random generation: --size, --seed
    - Tridiagonal: --tridiag (flag), --diag, --off-diag
    - Explicit: --matrix, --vector (or --a, --b)
    
    Args:
        parser: ArgumentParser to add arguments to
    """
    # Size-based generation
    parser.add_argument("--size", type=int, default=None,
                        help="Matrix dimension (generates random system)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    
    # Tridiagonal option
    parser.add_argument("--tridiag", action="store_true",
                        help="Generate tridiagonal matrix (use with --size)")
    parser.add_argument("--diag", type=float, default=2.0,
                        help="Main diagonal value for tridiagonal (default: 2.0)")
    parser.add_argument("--off-diag", type=float, default=-1.0,
                        help="Off-diagonal value for tridiagonal (default: -1.0)")
    
    # Explicit specification (support both naming conventions)
    parser.add_argument("--matrix", "--a", type=str, default=None,
                        help="Matrix A as JSON string, e.g., '[[2,1],[1,2]]'")
    parser.add_argument("--vector", "--b", type=str, default=None,
                        help="Vector b as JSON string, e.g., '[1,0]'")


def get_linear_system(args, require_hermitian: bool = False, 
                      require_power_of_2: bool = False) -> tuple:
    """
    Get linear system (A, b) from parsed arguments.
    
    Priority:
    1. Explicit --matrix/--vector if provided
    2. Tridiagonal if --tridiag flag set
    3. Random SPD if --size provided
    4. Error if none specified
    
    Args:
        args: Parsed argument namespace
        require_hermitian: If True, ensure A is Hermitian
        require_power_of_2: If True, pad to power of 2 dimension
    
    Returns:
        Tuple of (A, b, metadata) where metadata is a dict with generation info
    
    Raises:
        ValueError: If no valid specification provided or parsing fails
    """
    metadata = {}
    
    # Option 1: Explicit matrix/vector
    matrix_str = getattr(args, 'matrix', None) or getattr(args, 'a', None)
    vector_str = getattr(args, 'vector', None) or getattr(args, 'b', None)
    
    if matrix_str is not None and vector_str is not None:
        try:
            A = parse_matrix(matrix_str)
            b = parse_vector(vector_str)
            metadata['generation_method'] = 'explicit'
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Error parsing matrix or vector: {e}") from e
    
    # Option 2: Tridiagonal
    elif getattr(args, 'tridiag', False):
        if args.size is None:
            raise ValueError("--tridiag requires --size")
        A, b = generate_tridiagonal(
            args.size, 
            diag=getattr(args, 'diag', 2.0),
            off_diag=getattr(args, 'off_diag', -1.0),
            seed=args.seed
        )
        metadata['generation_method'] = 'tridiagonal'
        metadata['diag'] = getattr(args, 'diag', 2.0)
        metadata['off_diag'] = getattr(args, 'off_diag', -1.0)
    
    # Option 3: Random SPD
    elif args.size is not None:
        A, b = generate_random_spd(args.size, seed=args.seed)
        metadata['generation_method'] = 'random_spd'
    
    else:
        raise ValueError(
            "Must specify either --size (for random), --tridiag --size, "
            "or --matrix and --vector (for explicit)"
        )
    
    # Validate dimensions
    if A.ndim != 2 or A.shape[0] != A.shape[1]:
        raise ValueError(f"A must be square matrix, got shape {A.shape}")
    
    if len(b) != A.shape[0]:
        raise ValueError(f"b length ({len(b)}) must match A dimension ({A.shape[0]})")
    
    metadata['original_size'] = A.shape[0]
    metadata['seed'] = getattr(args, 'seed', None)
    
    # Make Hermitian if required
    if require_hermitian:
        A = make_hermitian(A)
        metadata['hermitian_conversion'] = True
    
    # Pad to power of 2 if required
    if require_power_of_2:
        n_orig = A.shape[0]
        A, b, _ = pad_to_power_of_2(A, b)
        metadata['padded_size'] = A.shape[0]
        if A.shape[0] != n_orig:
            metadata['was_padded'] = True
    
    # Compute condition number
    metadata['condition_number'] = float(np.linalg.cond(A))
    
    return A, b, metadata
