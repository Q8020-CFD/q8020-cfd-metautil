"""Compare 2025-11-11/shots_150000 vs 2026-02-24-LuGo/shots_150000_nelem5."""
import json
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
    m["circ_depth_before"] = flat.get("artifacts.0.transpile_passes.0.before.depth", "?")
    m["circ_depth_after"] = flat.get("artifacts.0.transpile_passes.0.after.depth", "?")
    m["circ_gates_before"] = flat.get("artifacts.0.transpile_passes.0.before.gate_count", "?")
    m["circ_gates_after"] = flat.get("artifacts.0.transpile_passes.0.after.gate_count", "?")
    m["circ_qubits_before"] = flat.get("artifacts.0.transpile_passes.0.before.num_qubits", "?")
    m["circ_qubits_after"] = flat.get("artifacts.0.transpile_passes.0.after.num_qubits", "?")
    m["time_generate"] = flat.get("artifacts.0.circuit_timing_total_s.generate", "?")
    m["time_transpile"] = flat.get("artifacts.0.circuit_timing_total_s.transpile", "?")
    m["time_execute"] = flat.get("artifacts.0.circuit_timing_total_s.execute", "?")
    m["time_total"] = flat.get("artifacts.0.circuit_timing_total_s.total", "?")
    n_passes = 0
    while flat.get(f"artifacts.0.transpile_passes.{n_passes}.step") is not None:
        n_passes += 1
    m["n_transpile_passes"] = n_passes
    return m


def fmt(v, w=12):
    if v is None:
        return "-".rjust(w)
    if isinstance(v, float):
        return f"{v:.4g}".rjust(w)
    return str(v).rjust(w)


def print_trial_table(metrics):
    hdr = (f"{'trial':>30} {'iters':>6} {'fidelity':>12} {'l2_err':>12} "
           f"{'residual':>12} {'depth_bef':>10} {'depth_aft':>10} "
           f"{'gates_bef':>10} {'gates_aft':>10} {'t_total_s':>10}")
    print(hdr)
    print("-" * len(hdr))
    for name in sorted(metrics):
        m = metrics[name]
        print(f"{name:>30} {fmt(m['iterations'],6)} {fmt(m['fidelity'])} "
              f"{fmt(m['l2_error_norm'])} {fmt(m['residual'])} "
              f"{fmt(m['circ_depth_before'],10)} {fmt(m['circ_depth_after'],10)} "
              f"{fmt(m['circ_gates_before'],10)} {fmt(m['circ_gates_after'],10)} "
              f"{fmt(m['time_total'],10)}")


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

    # --- PER-TRIAL METRICS ---
    print("\n" + "=" * 70)
    print("PER-TRIAL METRICS: 2025-11-11 / shots_150000")
    print("=" * 70)
    old_metrics = {n: extract_metrics(m) for n, m in sorted(old_trials.items())}
    print_trial_table(old_metrics)

    print("\n" + "=" * 70)
    print("PER-TRIAL METRICS: 2026-02-24-LuGo / shots_150000_nelem5")
    print("=" * 70)
    new_metrics = {n: extract_metrics(m) for n, m in sorted(new_trials.items())}
    print_trial_table(new_metrics)

    # --- SUMMARY ---
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print_summary("OLD (2025-11-11/shots_150000)", old_metrics)
    print_summary("NEW (2026-02-24-LuGo/shots_150000_nelem5)", new_metrics)


if __name__ == "__main__":
    main()
