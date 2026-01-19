"""
Classical optimization utilities for variational quantum algorithms.

This module provides optimization routines suitable for noisy quantum cost functions,
particularly for VQE (Variational Quantum Eigensolver) and similar algorithms.

Function Categories:
    Optimizers:
        - spsa_optimize: Simultaneous Perturbation Stochastic Approximation optimizer
                        (gradient-free, robust to shot noise)

SPSA Algorithm:
    SPSA is particularly well-suited for quantum optimization because:
    - Gradient-free: Only requires 2 function evaluations per iteration
    - Noise-tolerant: Robust to shot noise in quantum measurements
    - Efficient: O(1) evaluations per iteration regardless of parameter dimension
    
    The algorithm estimates gradients using random perturbations and adapts
    step sizes according to power-law schedules.

Typical Usage:
    from q8020_opt import spsa_optimize
    
    def cost_function(params):
        # Execute quantum circuit with params
        return energy_estimate
    
    result = spsa_optimize(
        cost_fn=cost_function,
        x0=[0.1, 0.2, 0.3],
        maxiter=200,
        a=0.2,      # Initial step size
        c=0.1       # Perturbation size
    )
    
    optimal_params = result["x"]
    final_cost = result["fun"]
"""
import numpy as np


def spsa_optimize(cost_fn, x0, maxiter=200, a=0.2, c=0.1, alpha=0.602, gamma=0.101):
    """
    SPSA optimizer for noisy cost functions.
    
    Args:
        cost_fn: Function to minimize
        x0: Initial parameters
        maxiter: Maximum iterations
        a, c, alpha, gamma: SPSA hyperparameters
    
    Returns:
        dict with optimal params and final cost
    """
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
