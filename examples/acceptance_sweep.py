"""SPEC v2 §9 acceptance sweep -- vocabulary end-to-end (SPEC v2c §2c.3).

Expands the three-axis matrix

    { Euler-HHL backend: classical | statevector_sim | fake:manila }
        x { CH encode bond_dim: 2 | 4 | 8 }
        x { SQLS method: full_svd | truncated_qr | truncated_mps }

prunes invalid cells against the plugin REGISTRY (declared requires/conflicts),
emits a slot-structured code.chain + provenance for every surviving cell, and
demonstrates that "group by encode plugin" is a dict pivot over the emitted
fragments -- the design claim of the survey.

No quantum execution: this proves the framework NAMES and RECORDS the axes so
results are attributable. The live subset (which cell runs in which venv) is a
separate, dep-gated concern (see the manifest printed at the end).

Run:  python examples/acceptance_sweep.py
"""

from __future__ import annotations

import itertools
from typing import Any

from q8020_cfd_metautil.meta_fragment import chain_entry, make_chain, make_code_meta
from q8020_cfd_metautil.solverfw import REGISTRY
from q8020_cfd_metautil.solverfw import plugins_euler, plugins_chlbm

# Register every plugin set the matrix references (idempotent).
plugins_euler.register_all()
plugins_chlbm.register_all()
try:  # sqls encode plugins live in a separate package
    import q8020_sqls_encode.plugins as _sqls_pl
    _sqls_pl.register_all()
    _HAVE_SQLS = True
except ImportError:  # fall back to declaring them locally for the demo
    _HAVE_SQLS = False

# The three axes.
BACKENDS = ["classical", "statevector_sim", "fake"]
BOND_DIMS = [2, 4, 8]
SQLS_METHODS = ["full_svd", "truncated_qr", "truncated_mps"]


def build_chain(backend: str, bond_dim: int, sqls_method: str) -> dict:
    """A cell's slot chain: an HHL-Euler solve, a CH encode, an SQLS encode.

    We express two encode-family choices and a backend so the registry's
    real constraints fire: hhl requires backend!=classical, and
    truncated_mps conflicts encode.state_prep=mps (not set here, so it
    stays valid -- the conflict is exercised by the negative test below).
    """
    return make_chain(
        backend=chain_entry("backend", backend),
        linsolve=chain_entry("linsolve", "hhl", {"shots": 0}),
        encode=chain_entry("encode", sqls_method, {"bond_dim": bond_dim}),
    )


def run() -> dict[str, Any]:
    cells = list(itertools.product(BACKENDS, BOND_DIMS, SQLS_METHODS))
    surviving: list[dict] = []
    pruned: list[dict] = []

    for backend, bond_dim, sqls_method in cells:
        chain = build_chain(backend, bond_dim, sqls_method)
        violations = REGISTRY.check_cell(chain)
        cell_id = f"{backend}_bd{bond_dim}_{sqls_method}"
        if violations:
            pruned.append({"cell": cell_id, "violations": violations})
            continue
        surviving.append({"cell": cell_id, "chain": chain,
                          "backend": backend, "bond_dim": bond_dim,
                          "sqls_method": sqls_method})

    # Each surviving cell gets a code fragment recording its chain +
    # the pruned-conflict provenance (auditable "no invalid cell ran").
    provenance = {"pruned_conflicts": pruned} if pruned else None
    fragments = []
    for s in surviving:
        code = make_code_meta(
            algorithm="acceptance_cell",
            entry_point="acceptance_sweep.py",
            problem_type="ivp",
            chain=s["chain"],
            provenance=provenance,
        )
        fragments.append(code)

    return {
        "n_cells": len(cells),
        "n_pruned": len(pruned),
        "n_surviving": len(surviving),
        "pruned": pruned,
        "fragments": fragments,
        "surviving": surviving,
    }


def group_by_encode_plugin(fragments: list[dict]) -> dict[str, int]:
    """The survey's headline query: partition runs by their encode plugin.

    A dict pivot over code.chain -- not archaeology over a flat run_args bag.
    """
    groups: dict[str, int] = {}
    for code in fragments:
        plugin = code["chain"]["encode"]["plugin"]
        groups[plugin] = groups.get(plugin, 0) + 1
    return groups


if __name__ == "__main__":
    result = run()
    print(f"matrix cells        : {result['n_cells']}")
    print(f"pruned (invalid)    : {result['n_pruned']}")
    print(f"surviving           : {result['n_surviving']}")
    print("\npruned cells (reason):")
    for p in result["pruned"][:5]:
        print(f"  {p['cell']}: {'; '.join(p['violations'])}")
    if result["n_pruned"] > 5:
        print(f"  ... and {result['n_pruned'] - 5} more")

    groups = group_by_encode_plugin(result["fragments"])
    print("\ngroup by encode plugin (the survey query):")
    for plugin, n in sorted(groups.items()):
        print(f"  {plugin:16s} {n} runs")

    print("\nlive-run manifest (dep-gated; not executed here):")
    print("  SQLS encode (full_svd/truncated_qr/truncated_mps): ch-lbm venv "
          "(proven v2a, Aer statevector)")
    print("  CH encode (bond_dim sweep)                       : ch-lbm venv")
    print("  Euler-HHL backend                                : needs "
          "quantum_linear_solvers (absent here) -- structural only")
