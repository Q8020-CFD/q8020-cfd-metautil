"""
Classical optimizers for variational quantum algorithms.

SPSA (Simultaneous Perturbation Stochastic Approximation) is gradient-free,
noise-tolerant, and requires only 2 function evaluations per iteration.
"""
from typing import Callable
import numpy as np
from numpy.typing import ArrayLike, NDArray


def spsa_optimize(
    cost_fn: Callable[[NDArray], float],
    x0: ArrayLike,
    maxiter: int = 200,
    a: float = 0.2,
    c: float = 0.1,
    alpha: float = 0.602,
    gamma: float = 0.101
) -> dict[str, NDArray | float]:
    """SPSA optimizer for noisy cost functions."""
    x = np.array(x0, dtype=float)
    n = len(x)
    
    for k in range(maxiter):
        ak = a / (k + 1) ** alpha
        ck = c / (k + 1) ** gamma
        
        # Random perturbation direction (Bernoulli ±1)
        delta = 2 * np.random.randint(0, 2, n) - 1
        
        # Evaluate at perturbed points
        x_plus = x + ck * delta
        x_minus = x - ck * delta
        y_plus = cost_fn(x_plus)
        y_minus = cost_fn(x_minus)
        
        # Gradient estimate
        g = (y_plus - y_minus) / (2 * ck * delta)
        
        # Update
        x = x - ak * g
    
    # Return final position (not "best seen" which can be noise artifact)
    # Average multiple evaluations for more stable final cost
    final_costs = [cost_fn(x) for _ in range(5)]
    final_cost = np.mean(final_costs)
    
    return {"x": x, "fun": final_cost}
