"""
Optimization helpers for quantum variational algorithms.
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
