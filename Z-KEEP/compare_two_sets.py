"""Compare 2025-11-11/shots_150000 vs 2026-02-24-LuGo/shots_150000_nelem5."""
import json
import math
from pathlib import Path
from q8020_cfd_metautil.compare import flatten

BASE = Path("/home/agallojr/proj/src/q8020-cfd/q8020-cfd-experiments/results/fvm_euler_1d_solver")
OLD = BASE / "2025-11-11/shots_150000"
NEW = BASE / "2026-02-24-LuGo/shots_150000_nelem5"


def load_trials(root):
    trials = {}
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        metas = sorted(d.glob("q8020_metadata_*.json"))
        if not metas:
            continue
        with open(metas[0]) as f:
            trials[d.name] = json.load(f)
    return trials


def canon(k):
    return ".".join(t for t in k.split(".") if not t.isdigit())


def extract_metrics(meta):
    flat = flatten(meta, max_depth=0)
    m = {}

    # -- basic analysis scalars --
    m["iterations"] = flat.get("analysis.0.iterations_completed", "?")
    m["converged"] = flat.get("analysis.0.converged", "?")
    m["fidelity"] = flat.get("analysis.0.fidelity", None)
    m["l2_error_norm"] = flat.get("analysis.0.l2_error_normalized", None)
    m["residual"] = flat.get("analysis.0.residual", None)
    m["residual_reduction"] = flat.get("analysis.0.residual_reduction", None)
    m["shots"] = flat.get("analysis.0.shots", flat.get("backend.0.nshots", "?"))
    m["backend"] = flat.get("backend.0.name", "?")
    m["method"] = flat.get("backend.0.method", "?")
    m["num_qubits"] = flat.get("backend.0.num_qubits", "?")
    m["algorithm"] = flat.get("code.0.algorithm", "?")
    m["nelem"] = flat.get("case.0.nelem", "?")
    m["max_iters"] = flat.get("case.0.max_iters", "?")

    # -- L2 absolute & linear-system residual from last hhl_metrics entry --
    n_hhl = 0
    while flat.get(f"analysis.0.hhl_metrics.{n_hhl}.step") is not None:
        n_hhl += 1
    m["n_hhl_metrics"] = n_hhl
    if n_hhl > 0:
        last = n_hhl - 1
        m["l2_error_abs"] = flat.get(f"analysis.0.hhl_metrics.{last}.l2_error_abs", None)
        m["l2_error_rel"] = flat.get(f"analysis.0.hhl_metrics.{last}.l2_error_rel", None)
        m["linsys_residual"] = flat.get(f"analysis.0.hhl_metrics.{last}.linsys_residual", None)
    else:
        m["l2_error_abs"] = None
        m["l2_error_rel"] = None
        m["linsys_residual"] = None

    # -- total (CFD) residual: first and last from residual_history --
    n_res = 0
    while flat.get(f"results.0.residual_history.{n_res}.residual_total") is not None:
        n_res += 1
    m["n_residual_entries"] = n_res
    if n_res > 0:
        m["residual_total_first"] = flat.get("results.0.residual_history.0.residual_total", None)
        m["residual_total_last"] = flat.get(f"results.0.residual_history.{n_res-1}.residual_total", None)
    else:
        m["residual_total_first"] = None
        m["residual_total_last"] = None

    # -- final solution vector --
    sol = []
    i = 0
    while True:
        xp = flat.get(f"results.0.final_solution.{i}.xp")
        if xp is None:
            break
        sol.append({
            "xp": xp,
            "rho": flat.get(f"results.0.final_solution.{i}.rho"),
            "u":   flat.get(f"results.0.final_solution.{i}.u"),
            "p":   flat.get(f"results.0.final_solution.{i}.p"),
            "Mach": flat.get(f"results.0.final_solution.{i}.Mach"),
        })
        i += 1
    m["final_solution"] = sol
    # L2 norm of final solution (rho, u, p combined)
    if sol:
        ssq = sum(
            (s.get("rho", 0) or 0)**2 + (s.get("u", 0) or 0)**2 + (s.get("p", 0) or 0)**2
            for s in sol
        )
        m["final_solution_l2"] = math.sqrt(ssq)
    else:
        m["final_solution_l2"] = None

    # -- circuit depths: first pass, and per-iteration min/max/last --
    m["circ_depth_before"] = flat.get("artifacts.0.transpile_passes.0.before.depth", "?")
    m["circ_depth_after"] = flat.get("artifacts.0.transpile_passes.0.after.depth", "?")
    m["circ_gates_before"] = flat.get("artifacts.0.transpile_passes.0.before.gate_count", "?")
    m["circ_gates_after"] = flat.get("artifacts.0.transpile_passes.0.after.gate_count", "?")
    m["circ_qubits_before"] = flat.get("artifacts.0.transpile_passes.0.before.num_qubits", "?")
    m["circ_qubits_after"] = flat.get("artifacts.0.transpile_passes.0.after.num_qubits", "?")
    n_passes = 0
    depths_after_all = []
    while flat.get(f"artifacts.0.transpile_passes.{n_passes}.step") is not None:
        d = flat.get(f"artifacts.0.transpile_passes.{n_passes}.after.depth")
        if d is not None:
            depths_after_all.append(d)
        n_passes += 1
    m["n_transpile_passes"] = n_passes
    m["circ_depths_after_all"] = depths_after_all
    if depths_after_all:
        m["circ_depth_after_min"] = min(depths_after_all)
        m["circ_depth_after_max"] = max(depths_after_all)
        m["circ_depth_after_last"] = depths_after_all[-1]
    else:
        m["circ_depth_after_min"] = "?"
        m["circ_depth_after_max"] = "?"
        m["circ_depth_after_last"] = "?"

    # -- timing --
    m["time_generate"] = flat.get("artifacts.0.circuit_timing_total_s.generate", "?")
    m["time_transpile"] = flat.get("artifacts.0.circuit_timing_total_s.transpile", "?")
    m["time_execute"] = flat.get("artifacts.0.circuit_timing_total_s.execute", "?")
    m["time_total"] = flat.get("artifacts.0.circuit_timing_total_s.total", "?")

    return m


def fmt(v, w=12):
    if v is None:
        return "-".rjust(w)
    if isinstance(v, float):
        return f"{v:.4g}".rjust(w)
    return str(v).rjust(w)


def print_main_table(metrics):
    """Primary table: iterations, fidelity, errors, residuals, circuit, timing."""
    hdr = (f"{'trial':>30} {'iters':>6} {'fidelity':>10} {'l2_norm':>10} "
           f"{'l2_abs':>10} {'linsys_r':>10} {'res_total':>10} "
           f"{'res_redu':>10} {'d_aft':>8} {'g_aft':>8} {'t_s':>8}")
    print(hdr)
    print("-" * len(hdr))
    for name in sorted(metrics):
        m = metrics[name]
        print(f"{name:>30} {fmt(m['iterations'],6)} {fmt(m['fidelity'],10)} "
              f"{fmt(m['l2_error_norm'],10)} {fmt(m['l2_error_abs'],10)} "
              f"{fmt(m['linsys_residual'],10)} {fmt(m['residual_total_last'],10)} "
              f"{fmt(m['residual_reduction'],10)} "
              f"{fmt(m['circ_depth_after'],8)} {fmt(m['circ_gates_after'],8)} "
              f"{fmt(m['time_total'],8)}")


def print_residual_table(metrics):
    """Residual history: first, last, and reduction."""
    hdr = (f"{'trial':>30} {'iters':>6} {'res_first':>12} {'res_last':>12} "
           f"{'reduction':>12} {'linsys_res':>12}")
    print(hdr)
    print("-" * len(hdr))
    for name in sorted(metrics):
        m = metrics[name]
        print(f"{name:>30} {fmt(m['iterations'],6)} {fmt(m['residual_total_first'])} "
              f"{fmt(m['residual_total_last'])} {fmt(m['residual_reduction'])} "
              f"{fmt(m['linsys_residual'])}")


def print_circuit_table(metrics):
    """Per-trial circuit depths: before, after (first/min/max/last), gate count."""
    hdr = (f"{'trial':>30} {'iters':>6} {'d_bef':>8} {'d_aft_1st':>10} "
           f"{'d_aft_min':>10} {'d_aft_max':>10} {'d_aft_last':>10} "
           f"{'g_aft':>8} {'q_aft':>6}")
    print(hdr)
    print("-" * len(hdr))
    for name in sorted(metrics):
        m = metrics[name]
        print(f"{name:>30} {fmt(m['iterations'],6)} {fmt(m['circ_depth_before'],8)} "
              f"{fmt(m['circ_depth_after'],10)} "
              f"{fmt(m['circ_depth_after_min'],10)} {fmt(m['circ_depth_after_max'],10)} "
              f"{fmt(m['circ_depth_after_last'],10)} "
              f"{fmt(m['circ_gates_after'],8)} {fmt(m['circ_qubits_after'],6)}")


def print_solution_table(metrics):
    """Final solution vector and L2 norm per trial."""
    # Determine how many grid points from first trial with data
    n_pts = 0
    for m in metrics.values():
        if m["final_solution"]:
            n_pts = len(m["final_solution"])
            break
    if n_pts == 0:
        print("  (no final solution data)")
        return

    # Header
    pt_hdrs = ""
    for i in range(n_pts):
        pt_hdrs += f" {'rho_'+str(i):>10} {'u_'+str(i):>10} {'p_'+str(i):>10}"
    hdr = f"{'trial':>30} {'sol_L2':>10}" + pt_hdrs
    print(hdr)
    print("-" * len(hdr))
    for name in sorted(metrics):
        m = metrics[name]
        row = f"{name:>30} {fmt(m['final_solution_l2'],10)}"
        for s in m["final_solution"]:
            row += f" {fmt(s.get('rho'),10)} {fmt(s.get('u'),10)} {fmt(s.get('p'),10)}"
        if not m["final_solution"]:
            row += "  (no data)"
        print(row)


def print_summary(label, metrics):
    vals = list(metrics.values())
    iters = [v["iterations"] for v in vals if isinstance(v["iterations"], (int, float))]
    fids = [v["fidelity"] for v in vals if v["fidelity"] is not None]
    depths_b = [v["circ_depth_before"] for v in vals if isinstance(v["circ_depth_before"], (int, float))]
    depths_a = [v["circ_depth_after"] for v in vals if isinstance(v["circ_depth_after"], (int, float))]
    times = [v["time_total"] for v in vals if isinstance(v["time_total"], (int, float))]
    algos = set(v["algorithm"] for v in vals)
    backends = set(v["backend"] for v in vals)
    shots_vals = set(str(v["shots"]) for v in vals)
    nqubits = set(str(v["num_qubits"]) for v in vals)
    nelem = set(str(v["nelem"]) for v in vals)
    n_passes = [v["n_transpile_passes"] for v in vals]

    print(f"\n{label}:")
    print(f"  trials:           {len(vals)}")
    print(f"  algorithm:        {algos}")
    print(f"  backend:          {backends}")
    print(f"  shots:            {shots_vals}")
    print(f"  nelem:            {nelem}")
    print(f"  num_qubits:       {nqubits}")
    if iters:
        print(f"  iterations:       min={min(iters)}, max={max(iters)}, avg={sum(iters)/len(iters):.1f}")
    else:
        print("  iterations:       NO DATA")
    if fids:
        print(f"  fidelity:         min={min(fids):.6f}, max={max(fids):.6f}, avg={sum(fids)/len(fids):.6f}")
    else:
        print("  fidelity:         NO DATA")
    if depths_b:
        print(f"  circuit depth (before): min={min(depths_b)}, max={max(depths_b)}")
        print(f"  circuit depth (after):  min={min(depths_a)}, max={max(depths_a)}")
    else:
        print("  circuit depth:    NO DATA")
    if n_passes:
        print(f"  transpile passes: min={min(n_passes)}, max={max(n_passes)}")
    if times:
        print(f"  total time (s):   min={min(times):.1f}, max={max(times):.1f}, avg={sum(times)/len(times):.1f}")
    else:
        print("  total time:       NO DATA")


def main():
    old_trials = load_trials(OLD)
    new_trials = load_trials(NEW)
    print(f"2025-11-11/shots_150000: {len(old_trials)} trials")
    print(f"2026-02-24-LuGo/shots_150000_nelem5: {len(new_trials)} trials")

    # --- STRUCTURAL COMPARISON ---
    print("\n" + "=" * 70)
    print("STRUCTURAL COMPARISON")
    print("=" * 70)

    old_sample = list(old_trials.values())[0]
    new_sample = list(new_trials.values())[0]

    old_sections = set(old_sample.keys())
    new_sections = set(new_sample.keys())
    print(f"\nOLD sections: {sorted(old_sections)}")
    print(f"NEW sections: {sorted(new_sections)}")
    print(f"Only in OLD: {sorted(old_sections - new_sections)}")
    print(f"Only in NEW: {sorted(new_sections - old_sections)}")

    old_flat = flatten(old_sample, max_depth=0)
    new_flat = flatten(new_sample, max_depth=0)
    old_canon = set(canon(k) for k in old_flat)
    new_canon = set(canon(k) for k in new_flat)
    print(f"\nCanonical keys: OLD={len(old_canon)}, NEW={len(new_canon)}")

    only_old = sorted(old_canon - new_canon)
    only_new = sorted(new_canon - old_canon)
    if only_old:
        print(f"\nKeys only in OLD ({len(only_old)}):")
        for k in only_old[:40]:
            print(f"  {k}")
        if len(only_old) > 40:
            print(f"  ... +{len(only_old)-40} more")
    if only_new:
        print(f"\nKeys only in NEW ({len(only_new)}):")
        for k in only_new[:40]:
            print(f"  {k}")
        if len(only_new) > 40:
            print(f"  ... +{len(only_new)-40} more")

    old_metrics = {n: extract_metrics(m) for n, m in sorted(old_trials.items())}
    new_metrics = {n: extract_metrics(m) for n, m in sorted(new_trials.items())}

    for label, metrics in [("2025-11-11 / shots_150000", old_metrics),
                           ("2026-02-24-LuGo / shots_150000_nelem5", new_metrics)]:
        print("\n" + "=" * 70)
        print(f"MAIN TABLE: {label}")
        print("=" * 70)
        print_main_table(metrics)

        print(f"\nRESIDUAL HISTORY: {label}")
        print("-" * 70)
        print_residual_table(metrics)

        print(f"\nCIRCUIT DEPTHS: {label}")
        print("-" * 70)
        print_circuit_table(metrics)

        print(f"\nFINAL SOLUTION: {label}")
        print("-" * 70)
        print_solution_table(metrics)

    # --- SUMMARY ---
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print_summary("OLD (2025-11-11/shots_150000)", old_metrics)
    print_summary("NEW (2026-02-24-LuGo/shots_150000_nelem5)", new_metrics)


if __name__ == "__main__":
    main()
