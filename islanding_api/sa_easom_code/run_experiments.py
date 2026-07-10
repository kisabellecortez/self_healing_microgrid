"""
Runs Experiments 1, 2, 3 required by Assignment 2 - Problem 1, and saves
plots + a text summary of results.
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sa_easom import simulated_annealing, easom, LB, UB, PI

np.random.seed(0)
MAX_ITER = 6000

# ---------- Fixed defaults used unless an experiment varies that param ----------
DEFAULT_T0 = 300.0
DEFAULT_SCHEDULE = "geometric"
DEFAULT_ALPHA = 0.95
DEFAULT_STEP = 1.0          # step_coeff: neighbor scale = step_coeff * sqrt(T)
DEMO_X0 = np.array([20.0, 20.0])   # representative start point for Exp 2 & 3


def summarize(results, label):
    costs = [r["best_cost"] for r in results]
    print(f"\n--- {label} ---")
    for i, r in enumerate(results):
        tag = r.get("tag", f"run {i}")
        print(f"  {tag:>22s} | best_cost = {r['best_cost']:.6f} | "
              f"best_x = ({r['best_x'][0]:.3f}, {r['best_x'][1]:.3f}) | "
              f"time = {r['elapsed']*1000:.1f} ms")
    print(f"  >> Best of {label}: {min(costs):.6f}  | "
          f"Mean: {np.mean(costs):.6f} | Std: {np.std(costs):.6f}")
    return costs


def plot_profiles(results, title, fname, legend_fmt):
    plt.figure(figsize=(8, 5))
    for r in results:
        plt.plot(r["history"], linewidth=1.0, alpha=0.85,
                  label=legend_fmt(r))
    plt.axhline(-1.0, color="black", linestyle="--", linewidth=1,
                label="Global optimum (-1)")
    plt.xlabel("Iteration (time step)")
    plt.ylabel("Best-so-far cost  f(x)")
    plt.title(title)
    plt.legend(fontsize=7, loc="upper right", ncol=1)
    plt.tight_layout()
    plt.savefig(fname, dpi=140)
    plt.close()
    print(f"Saved {fname}")


# ============================================================
# EXPERIMENT 1: 10 different random initial points
# ============================================================
exp1_results = []
init_points = np.random.uniform(LB, UB, size=(10, 2))
for i, x0 in enumerate(init_points):
    r = simulated_annealing(x0, T0=DEFAULT_T0, alpha=DEFAULT_ALPHA,
                             schedule=DEFAULT_SCHEDULE, step_coeff=DEFAULT_STEP,
                             max_iter=MAX_ITER, seed=100 + i)
    r["tag"] = f"x0=({x0[0]:.0f},{x0[1]:.0f})"
    exp1_results.append(r)

costs1 = summarize(exp1_results, "Experiment 1 (initial points)")
plot_profiles(exp1_results, "Experiment 1: Varying Initial Point (10 trials)",
              "exp1_initial_points.png",
              lambda r: f"x0=({r['x0'][0]:.0f},{r['x0'][1]:.0f})")

# ============================================================
# EXPERIMENT 2: 10 different initial temperatures
# ============================================================
exp2_results = []
T0_values = np.logspace(0, 4, 10)        # 1 ... 10000
fixed_x0 = DEMO_X0
for i, T0 in enumerate(T0_values):
    r = simulated_annealing(fixed_x0, T0=T0, alpha=DEFAULT_ALPHA,
                             schedule=DEFAULT_SCHEDULE, step_coeff=DEFAULT_STEP,
                             max_iter=MAX_ITER, seed=200 + i)
    r["tag"] = f"T0={T0:.1f}"
    exp2_results.append(r)

costs2 = summarize(exp2_results, "Experiment 2 (initial temperatures)")
plot_profiles(exp2_results, "Experiment 2: Varying Initial Temperature (10 trials)",
              "exp2_initial_temps.png",
              lambda r: f"T0={r['T0']:.0f}")

# ============================================================
# EXPERIMENT 3: 10 different annealing schedules
#   - 6 geometric cooling rates + 4 different schedule families
# ============================================================
exp3_results = []
geo_alphas = [0.80, 0.85, 0.90, 0.93, 0.96, 0.99]
schedules_extra = [("linear", 0.05), ("linear", 0.2),
                    ("logarithmic", None), ("fast", None)]

for i, a in enumerate(geo_alphas):
    r = simulated_annealing(fixed_x0, T0=DEFAULT_T0, alpha=a,
                             schedule="geometric", step_coeff=DEFAULT_STEP,
                             max_iter=MAX_ITER, seed=300 + i)
    r["tag"] = f"geometric, a={a}"
    exp3_results.append(r)

for i, (sched, a) in enumerate(schedules_extra):
    a_use = a if a is not None else 0.0
    r = simulated_annealing(fixed_x0, T0=DEFAULT_T0, alpha=a_use,
                             schedule=sched, step_coeff=DEFAULT_STEP,
                             max_iter=MAX_ITER, seed=400 + i)
    r["tag"] = f"{sched}" + (f", a={a}" if a else "")
    exp3_results.append(r)

costs3 = summarize(exp3_results, "Experiment 3 (annealing schedules)")
plot_profiles(exp3_results, "Experiment 3: Varying Annealing Schedule (10 trials)",
              "exp3_schedules.png",
              lambda r: r["tag"])

# ============================================================
# Overall best across all 30 runs
# ============================================================
all_results = exp1_results + exp2_results + exp3_results
best_overall = min(all_results, key=lambda r: r["best_cost"])
print("\n================ OVERALL BEST ================")
print(f"Setting       : {best_overall['tag']}")
print(f"x0            : {best_overall['x0']}")
print(f"T0            : {best_overall['T0']}")
print(f"schedule      : {best_overall['schedule']} (alpha={best_overall['alpha']})")
print(f"step_coeff    : {best_overall['step_coeff']}")
print(f"best_x        : {best_overall['best_x']}")
print(f"best_cost     : {best_overall['best_cost']:.6f}")
print(f"true optimum  : x=({PI:.4f},{PI:.4f}), f=-1.000000")
print(f"elapsed (ms)  : {best_overall['elapsed']*1000:.2f}")

# ============================================================
# A combined "all profiles" overview figure (helps visualize spread)
# ============================================================
fig, axes = plt.subplots(1, 3, figsize=(15, 4.2), sharey=True)
for ax, results, name in zip(
        axes, [exp1_results, exp2_results, exp3_results],
        ["Exp.1: initial points", "Exp.2: initial temperatures", "Exp.3: schedules"]):
    for r in results:
        ax.plot(r["history"], linewidth=0.9, alpha=0.8)
    ax.axhline(-1.0, color="black", linestyle="--", linewidth=1)
    ax.set_title(name)
    ax.set_xlabel("Iteration")
axes[0].set_ylabel("Best-so-far cost f(x)")
plt.tight_layout()
plt.savefig("overview_all_experiments.png", dpi=140)
plt.close()
print("Saved overview_all_experiments.png")
