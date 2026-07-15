# metapatches scratch tools (internal, not shipped)

Throwaway utilities used to groom the burgers June2026 archive per the
metapatches approach (see `docs/metapatches.md`). Kept here rather than
`/tmp` so they aren't lost; deliberately NOT promoted to user-facing CLI.

- **app_identity.py** — shared derivation: pull (entry_point, algorithm)
  from an already-assembled `q8020_metadata_*.json`, the same way the
  live `sweep._resolve_app_identity` does for new runs.
- **survey.py** — report how many cases need code-section grooming and
  what would be derived. Read-only.
  `python -m tests.metapatches.survey <burgers_dir>`
- **apply_code_patch.py** — write `<case>/metapatches/<date>/`
  `q8020_patch_code_0.json` deltas. Dry-run by default; `--apply` to write.
  Additive only, never edits/deletes.
  `python -m tests.metapatches.apply_code_patch <burgers_dir> [--apply]`
- **compose.py** — JIT composer: base rollup (+) active patch runs, date
  order, deep-merge, later-wins. Read-only reference implementation of the
  composition contract. `python -m tests.metapatches.compose <metadata.json>`
