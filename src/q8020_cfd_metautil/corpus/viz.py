#!/usr/bin/env python3
"""Corpus cube visualization: Case x Algorithm x Backend.

Reads a corpus_index.json produced by corpus_ingest and generates:
  1. A bubble chart (case_family x backend), one panel per algorithm
  2. A summary table printed to stdout

Usage:
    q8020-corpus-viz corpus_index.json [-o cube.png]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np


# ---------------------------------------------------------------------------
# Coarsening helpers
# ---------------------------------------------------------------------------

# Strip trial/run/hash suffixes to get a case family
_TRIAL_RE = re.compile(r"_trial_\d+.*|_run\d+.*|_\d+$|_[0-9a-f]{6,}$")


def _case_family(case_name: str, case_id: str) -> str:
    """Coarsen case identifiers to a family name."""
    name = case_name or case_id or "unknown"
    name = _TRIAL_RE.sub("", name)
    # Collapse numbered variants like baseline_0 -> baseline
    name = re.sub(r"_\d+$", "", name)
    name = name.strip("_") or "unknown"

    # Aggressive coarsening: group related families
    if re.match(r"size_\d+", name):
        return "modular_hhl_size_sweep"
    if name.startswith("baseline"):
        return "baseline"
    if name.startswith("time_steps"):
        return "fvm_time_steps"
    if name.startswith("more_iters"):
        return "fvm_more_iters"
    if name.startswith("opt-sweep") or name.startswith("opt_sweep"):
        return "opt_sweep"
    if name.startswith("mps_bond_sweep"):
        return "mps_bond_sweep"
    if name.startswith("paper_"):
        return "paper_aligned"
    if name.startswith("n") and re.match(r"n\d+_(modular|monolithic|extra)", name):
        return "modular_hhl_size_sweep"
    if name.startswith("fvm_1d_nelem"):
        return "fvm_euler_nelem"
    if name.startswith("frontier-qlsa") or name.startswith("frontier_"):
        return "frontier_qlsa"
    if name.startswith("qng"):
        return "qng"
    return name


def _normalize_algorithm(algo: str) -> str:
    if not algo:
        return "classical/other"
    return algo.upper()


def _normalize_backend(name: str) -> str:
    if not name:
        return "unknown"
    # Shorten aer_simulator_from(fake_xxx) -> fake_xxx
    m = re.match(r"aer_simulator_from\((\w+)\)", name)
    if m:
        return m.group(1)
    return name


def _best_fidelity(quality: dict) -> float | None:
    """Pick the best available fidelity-like metric (higher = better)."""
    for key in ("fidelity", "sv_fidelity", "fidelity_shots"):
        if key in quality and quality[key] is not None:
            return float(quality[key])
    # Convert error metrics: fidelity ~ 1 - error (clamped)
    for key in ("l2_error_rel", "l2_error_abs", "l2_error_normalized",
                "final_error_epsilon"):
        if key in quality and quality[key] is not None:
            return max(0.0, 1.0 - float(quality[key]))
    return None


def _best_size(size: dict) -> float | None:
    """Pick a representative problem size scalar."""
    for key in ("grid_points", "matrix_size_hermitian",
                "matrix_size_original", "nelem"):
        if key in size and size[key] is not None:
            return float(size[key])
    if "nx" in size and "ny" in size:
        return float(size["nx"] * size["ny"])
    return None


# ---------------------------------------------------------------------------
# Load and group
# ---------------------------------------------------------------------------

def load_and_group(index_path: str) -> tuple[
    dict[tuple[str, str, str], dict],
    list[str], list[str], list[str],
]:
    """Load index, group by (case_family, algorithm, backend).

    Returns (groups, case_families, algorithms, backends) where
    groups maps (cf, algo, be) -> {count, fidelities, sizes}.
    """
    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)

    groups: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"count": 0, "fidelities": [], "sizes": []}
    )

    for rec in index["records"]:
        cf = _case_family(rec.get("case_name", ""), rec.get("case_id", ""))
        algo = _normalize_algorithm(rec.get("algorithm", ""))
        be = _normalize_backend(rec.get("backend_name", ""))

        key = (cf, algo, be)
        groups[key]["count"] += 1

        fid = _best_fidelity(rec.get("quality", {}))
        if fid is not None:
            groups[key]["fidelities"].append(fid)

        sz = _best_size(rec.get("size", {}))
        if sz is not None:
            groups[key]["sizes"].append(sz)

    # Filter to families with >= min_count total records
    family_counts: dict[str, int] = defaultdict(int)
    for (cf, _, _), g in groups.items():
        family_counts[cf] += g["count"]
    min_count = 3
    active_families = {cf for cf, n in family_counts.items() if n >= min_count}

    filtered = {k: v for k, v in groups.items() if k[0] in active_families}

    case_families = sorted({k[0] for k in filtered})
    algorithms = sorted({k[1] for k in filtered})
    backends = sorted({k[2] for k in filtered})

    return dict(filtered), case_families, algorithms, backends


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

def make_cube_plot(
    groups: dict,
    case_families: list[str],
    algorithms: list[str],
    backends: list[str],
    outpath: str,
) -> None:
    n_algo = len(algorithms)
    fig, axes = plt.subplots(
        1, n_algo,
        figsize=(6 + 5 * n_algo, max(6, 0.35 * len(case_families) + 2)),
        squeeze=False,
    )

    cmap = plt.colormaps["RdYlGn"]
    norm = mcolors.Normalize(vmin=0.0, vmax=1.0)

    for col, algo in enumerate(algorithms):
        ax = axes[0, col]
        xs, ys, sizes, colors = [], [], [], []

        for i, be in enumerate(backends):
            for j, cf in enumerate(case_families):
                g = groups.get((cf, algo, be))
                if g is None or g["count"] == 0:
                    continue

                xs.append(i)
                ys.append(j)

                # Bubble size ~ log(mean problem size)
                if g["sizes"]:
                    mean_sz = np.mean(g["sizes"])
                    sizes.append(max(20, 15 * np.log2(mean_sz + 1)))
                else:
                    sizes.append(20)

                # Color = mean fidelity
                if g["fidelities"]:
                    colors.append(np.mean(g["fidelities"]))
                else:
                    colors.append(0.5)  # gray for unknown

        if xs:
            _sc = ax.scatter(
                xs, ys, s=sizes, c=colors, cmap=cmap, norm=norm,
                edgecolors="0.3", linewidths=0.5, alpha=0.85, zorder=5,
            )
        ax.set_title(algo, fontsize=13, fontweight="bold")
        ax.set_xticks(range(len(backends)))
        ax.set_xticklabels(backends, rotation=55, ha="right", fontsize=8)
        ax.set_yticks(range(len(case_families)))
        ax.set_yticklabels(case_families, fontsize=8)
        ax.set_xlim(-0.5, len(backends) - 0.5)
        ax.set_ylim(-0.5, len(case_families) - 0.5)
        ax.grid(True, alpha=0.15)
        if col == 0:
            ax.set_ylabel("Case Family", fontsize=11)
        ax.set_xlabel("Backend", fontsize=11)

    # Colorbar
    cbar_ax = fig.add_axes([0.92, 0.15, 0.015, 0.7])
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    fig.colorbar(sm, cax=cbar_ax, label="Fidelity (higher = better)")

    fig.suptitle(
        "Experiment Corpus: Case × Backend (panels = Algorithm)\n"
        f"bubble size ∝ log₂(problem size)  |  {sum(g['count'] for g in groups.values())} runs",
        fontsize=13,
    )
    plt.tight_layout(rect=[0, 0, 0.91, 0.93])
    fig.savefig(outpath, dpi=180, bbox_inches="tight", facecolor="white")
    print(f"Saved to {outpath}")


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def print_summary(
    groups: dict,
    case_families: list[str],
    algorithms: list[str],
    backends: list[str],
) -> None:
    print(f"\n{'Case Family':<25} {'Algorithm':<18} {'Backend':<30} {'N':>4} {'Fid':>6} {'Size':>8}")
    print("-" * 95)
    for cf in case_families:
        for algo in algorithms:
            for be in backends:
                g = groups.get((cf, algo, be))
                if g is None or g["count"] == 0:
                    continue
                fid = f"{np.mean(g['fidelities']):.3f}" if g["fidelities"] else "  —"
                sz = f"{np.mean(g['sizes']):.0f}" if g["sizes"] else "  —"
                print(f"{cf:<25} {algo:<18} {be:<30} {g['count']:>4} {fid:>6} {sz:>8}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("index", help="Path to corpus_index.json")
    parser.add_argument("-o", "--output", default="corpus_cube.png",
                        help="Output PNG path")
    parser.add_argument("--no-table", action="store_true",
                        help="Skip printing the summary table")
    args = parser.parse_args()

    groups, case_families, algorithms, backends = load_and_group(args.index)

    make_cube_plot(groups, case_families, algorithms, backends, args.output)

    if not args.no_table:
        print_summary(groups, case_families, algorithms, backends)


if __name__ == "__main__":
    main()
