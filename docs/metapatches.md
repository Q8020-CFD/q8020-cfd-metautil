# Metapatches: additive metadata grooming

## Principle

Raw and as-run metadata is **write-once**. A grooming fix (from a review,
a cross-version alignment, or a closed-box gap-fill) is never an edit --
it is a **new file added** alongside the originals. Nothing on disk is
ever altered or deleted.

## Immutability tiers

1. **Fragments** `q8020_<section>_N.json` -- raw, frozen.
2. **Base rollup** `q8020_metadata_<id>.json` -- first harvest's as-run
   assembly, frozen. Never rewritten.
3. **Patches** -- additive deltas under `metapatches/` (below).

## Layout: one subdir per patch run

```
<case>/
    q8020_metadata_<id>.json              # tier 2, as-run, frozen
    metapatches/
        20260715/                         # one patch RUN = one dated subdir
            q8020_patch_code_0.json        #   many patch files per run
            q8020_patch_backend_0.json
            q8020_metadata_<id>.json       #   OPTIONAL groomed rollup, produced
                                           #   into the run subdir (a parallel
                                           #   copy -- NOT an edit of tier 2)
        20260801/                          # next run, alongside
            ...
```

- A **patch run** is a datestamped subdir holding every file that run
  produced. Same-day second run -> `20260715_2/`. ISO dates sort =
  chronological.
- A run may cover **any subset up to the whole**: one key, or a full
  restatement. The producer decides; the consumer copes (see below).
- If a run wants to "alter" the top-level rollup, it writes its own
  `q8020_metadata_<id>.json` **into its subdir** -- the top-level file
  is never opened for writing.

## Patch file shape

```json
{
  "_source": "review",
  "_patch_date": "2026-07-15",
  "_note": "sweep wrapper recorded bash -c, not the app",
  "patches": { "code": { "algorithm": "cole_hopf_circuit",
                         "entry_point": "burgers_solver.py" } }
}
```

`patches` mirrors metautil section names. `_source` distinguishes a
groomed value from a solver-supplied one.

## Composition (how a consumer reads it)

**base rollup (+) active patch runs, in date order, deep-merged,
later-wins-per-key.** One code path handles both delta and full-
restatement runs -- no need to classify a run:

- delta run merged over base = base + those keys;
- full run merged over base = the full run (it covers everything).

A consumer either:
- **JIT-composes** from base + active runs (originals stay untouched), or
- reads a run's pre-baked `q8020_metadata_<id>.json` as a fast path.

Both yield the same result. A cross-case study reads the composed view;
with zero patches that is just the base rollup.

Open item: deep-merge cannot express **deletion** of a base key. Handle
by overriding the value (or a tombstone the consumer honors), never by
removing. No current patch needs this.

## Deprecation (skip without deleting)

Rename so the glob stops matching; content is never touched:

- whole run:  `metapatches/20260715/` -> `metapatches/20260715.off/`
- one file:   `q8020_patch_code_0.json` -> `q8020_patch_code_0.json.off`

Reversible by renaming back. The active set = subdirs/files still
matching the pattern.
