"""
Simulated Annealing for minimizing the 2D Easom function.
ECE457A - Assignment 2, Problem 1

f(x1, x2) = -cos(x1)*cos(x2)*exp(-((x1-pi)^2 + (x2-pi)^2)),  x in [-100, 100]^2
Global minimum: f(pi, pi) = -1
"""

import numpy as np
import time

PI = np.pi
LB, UB = -100.0, 100.0


def easom(x):
    """Easom cost function. x is a length-2 array [x1, x2]."""
    x1, x2 = x[0], x[1]
    return -np.cos(x1) * np.cos(x2) * np.exp(-((x1 - PI) ** 2 + (x2 - PI) ** 2))


def clip(x):
    return np.clip(x, LB, UB)


def cooling_schedule(T0, k, schedule="geometric", alpha=0.95):
    """
    Returns the temperature at iteration k (k = 1, 2, 3, ...).
    schedule in {"geometric", "linear", "logarithmic", "fast"}.
    """
    if schedule == "geometric":          # T_k = T0 * alpha^k          (alpha < 1)
        return T0 * (alpha ** k)
    elif schedule == "linear":           # T_k = T0 - alpha*k
        return max(T0 - alpha * k, 1e-10)
    elif schedule == "logarithmic":      # T_k = T0 / log(1+k)         (classical, slow)
        return T0 / np.log(1 + k + 1e-9)
    elif schedule == "fast":             # T_k = T0 / (1+k)            (Cauchy / fast SA)
        return T0 / (1 + k)
    else:
        raise ValueError(f"Unknown schedule {schedule}")


def neighbor(x, T, step_coeff, min_scale=0.5):
    """
    Temperature-dependent Gaussian perturbation: scale = step_coeff * sqrt(T).
    Unlike a T/T0-normalized scale, this makes the ABSOLUTE value of T (and
    hence T0) directly control how far SA is willing to jump, which is what
    lets Experiment 2 (varying T0) actually show different behaviour.
    """
    scale = max(min_scale, step_coeff * np.sqrt(T))
    x_new = x + np.random.normal(0.0, scale, size=2)
    return clip(x_new)


def simulated_annealing(x0, T0=2000.0, alpha=0.95, schedule="geometric",
                         step_coeff=1.0, max_iter=4000, seed=None):
    """
    Runs SA starting at x0. Returns dict with best_x, best_cost,
    and a history of the *best-so-far* cost at every iteration
    (this is the "solution profile" we plot).
    """
    if seed is not None:
        np.random.seed(seed)

    x = np.array(x0, dtype=float)
    cost = easom(x)
    best_x, best_cost = x.copy(), cost
    history = np.empty(max_iter + 1)
    history[0] = best_cost

    t_start = time.perf_counter()
    for k in range(1, max_iter + 1):
        T = cooling_schedule(T0, k, schedule, alpha)
        x_new = neighbor(x, T, step_coeff)
        cost_new = easom(x_new)
        delta = cost_new - cost

        if delta < 0 or np.random.rand() < np.exp(-delta / max(T, 1e-12)):
            x, cost = x_new, cost_new
            if cost < best_cost:
                best_x, best_cost = x.copy(), cost

        history[k] = best_cost
    elapsed = time.perf_counter() - t_start

    return {
        "best_x": best_x, "best_cost": best_cost,
        "history": history, "elapsed": elapsed,
        "T0": T0, "alpha": alpha, "schedule": schedule,
        "step_coeff": step_coeff, "x0": np.array(x0),
    }
