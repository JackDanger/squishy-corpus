# Squishy governance

Squishy is meant to be a shared yardstick for decades. It is one corpus serving
two jobs — a citable compression-**ratio** score, and a diverse, representative
**test battery** for catching speed/CPU/memory regressions — and a benchmark only
stays trustworthy that long if the rules for *changing* it are as clear as the
rules for *running* it. This document covers editions, curation, and the
leaderboard. The scoring rules themselves live in [`RULES.md`](RULES.md); the
score definition + coverage-map rationale in [`plans/squishy-score.md`](plans/squishy-score.md).

## Editions are permanent and dated

- Each edition is a frozen, dated set with a Zenodo DOI: `Squishy-2026`, then a
  refresh roughly every ~4 years. The edition manifest pins the **exact set of
  `(kind, size)` members** — not just filenames — because the size ladder grows
  between editions; two people citing the same edition must run the identical
  leaf set.
- **A published edition is never edited.** Its bytes, SHA-256s, and DOI are
  immutable. Cite the edition you ran (`"4.2× on Squishy-2026"`); a number without
  an edition is meaningless.
- Every published edition stays citable forever; a new edition simply becomes the
  *current* recommendation. A codec that overfit one edition visibly stops winning
  on the next — that decay is a feature.

## Versioning: the edition *is* the version

There is no semver on the data. The dated edition name (`Squishy-2026`) is the
version, the [Zenodo](https://zenodo.org) DOI is its permanence anchor, and the
freeze is carried by the matching git tag (`Squishy-2026`) and the immutable S3
prefix (`s3://squishy-corpus/2026/`). There is deliberately **no "Squishy 1.0"** —
immutable bytes never get a `1.1`, so a semver would only invite "where's 2.0?".
The next version is simply the next dated edition (e.g. `Squishy-2030`). Semver is
reserved for the **tooling** (the `squishy` package), which is versioned
independently of any edition; a score's provenance tuple records the *codec* and
*edition*, never the runner's version.

## Errata & withdrawal (recourse without mutation)

"Never edited" means the bytes, SHA-256s, and DOI of a published edition are never
silently changed — not that there is no recourse when a defect (e.g. a license
problem on one file) surfaces after freeze. Because the Squishy Score is a flat
geomean, dropping or swapping any file moves *every* score, so a file can never be
quietly removed while still calling the result `Squishy-2026`. The defined paths:

- **Append-only errata.** Every published edition carries an `ERRATA` record
  (repo + the Zenodo version record). Defects are documented there; nothing is
  deleted in place.
- **Distribution-only defect** (we can no longer host a file, but it remains
  independently fetchable by its pinned SHA-256 and the math is unaffected): record
  it in errata, point to upstream. The edition stays citable.
- **Score-affecting defect** (a file must actually be replaced or removed): this
  forces a **new dated edition** — a point edition `Squishy-2026.1` if it is a
  correction of the same year's set, or the next scheduled edition — with the file
  changed and the reference panel **recomputed**. The defective edition is marked
  *superseded/withdrawn, with the reason*, and stays in the historical record so old
  citations remain interpretable.

This is exactly why the unit of change is the *edition*, not an in-place edit: a
yank has graceful vocabulary ("issue the next dated edition"), and the public S3
copy is therefore locked in **governance** mode at most — never compliance mode,
which would trap us with a known-defective artifact we legally could not fix.

## What a file must be to enter the core

A candidate core file must be **all** of:

1. **Real.** Genuine data of its kind from a real source — never synthetic or
   hand-built. (Transforms like slicing boilerplate or re-tarring a subtree are
   allowed *if* the recipe is pinned and the result is re-derivable; see the
   manifest.)
2. **Redistributable for a permanent public release.** Public-domain, CC-BY,
   Apache-2.0, MIT, or equivalent — verified and recorded in
   `build/meta/LICENSE-MANIFEST.csv`, with full license texts in `LICENSES/`.
3. **Free of data that should not be public** (PII, secrets) — checked by
   `scripts/pii-scan.py` and human review.
4. **Independent.** It must not share rows, vocabulary, or lineage with another
   `kind` cell. (This is why the 2026 tabular trio was decoupled: `csv`, `parquet`,
   and `sqlite` are now weather / airline / nutrition, not three views of one dataset.)
   A larger size rung of an *existing* kind is the one allowed exception — it is a
   `length` cell, declared as such in `build/meta/schema.json`, and re-samples its
   kind's data on purpose (capped at one per kind).
5. **Memorable.** You can name it in a sentence. The core stays small enough that
   a person can hold all of it in their head.

The scored roster is constituted cell-by-cell in
[`build/meta/schema.json`](../build/meta/schema.json) — each cell is one vote, with a
**role** (`kind` / `length` / `incompressible`) and declared **budgets**: ≤2 votes per
kind, a small near-incompressible budget (2026: 3 — photo, movie, weights), and a
per-category vote count. `tests/test_schema.py` fails the build if the live roster
drifts from those budgets, so balance and independence are enforced by code, not just
discipline. (Known tension flagged in the schema for the next edition: Binary & Media
sits at the high end of the category envelope after the 2026 executable expansion —
Hugo/ELF, fd/PE, hyperfine/ARM64, SQLite/Wasm, Lua/DWARF, five distinct programs — while
Prose sits at the floor.) Categories organize; they don't weight.

## Curating the next edition

1. The maintainer proposes the new file set publicly (issue/PR) with, per file,
   the candidate source + license + why it represents its kind in *that* year.
2. Each file is run through the same gates as above (license, PII, independence,
   non-degeneracy via `scripts/validate-core.py`).
3. **Continuity report.** Before freezing a new edition, publish the per-codec
   Squishy Score for the reference panel on *both* the old and new editions, and
   the per-file ratio deltas. This makes overfitting between editions measurable:
   a codec whose lead shrinks on the new edition was (partly) tuned to the old one.
4. Freeze exactly as in [`plans/PRE-FREEZE-VERIFICATION.md`](plans/PRE-FREEZE-VERIFICATION.md):
   pristine copy, tag, Zenodo DOI, backup, then announce.

## The leaderboard

Submitting a codec's score to the public board is governed in
[`RULES.md`](RULES.md#submitting-a-score-to-the-public-leaderboard): a submission
must carry the full tuple (codec, version, exact argv, edition) **and** a one-line
reproduction command, and the maintainer (or any third party) re-runs it before
it is published. A number nobody else can reproduce does not go on the board.

## Maintainership & succession

- The corpus, the runner, and these documents live in a public Git repository;
  every edition is additionally pinned in Zenodo, so the artifact survives
  independent of any one host, account, or maintainer.
- Changes to the scoring definition or the governance rules are made by PR and
  must explain how existing published scores remain interpretable. The score
  definition should change **rarely**; prefer adding an edition over redefining
  the number.
- If maintainership transfers, the new maintainer inherits this document and the
  DOIs. Nothing about reproducing a past edition depends on who currently holds
  the repo.
