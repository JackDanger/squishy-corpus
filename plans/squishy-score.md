# The Squishy Score (and what Squishy is)

Squishy is the 2026 successor to the Silesia corpus: **the authoritative set of
real, redistributable data you'd want to compress for any reason**, plus one
citable compression-ratio score. It's a fixed set of real files, each measured on
a few intrinsic properties and chosen to **span** the range of byte structure and
scale rather than pile up in one corner.

One corpus, two jobs — both resting on the *same* property (the set is diverse and
representative):

1. **Measure ratio** — the citable **Squishy Score** over the whole corpus.
2. **Test behavior** — a representative battery of real inputs to catch
   **time / CPU / memory** regressions when you change an implementation without
   changing what it outputs (the gzippy case: its Score ≈ stock gzip; the value is
   the diverse, principled test set).

This document fixes the design. The **coverage map** is measured evidence that a
small, real, sparse set is representative of the range of byte structure and scale
— it's the corpus's selection rationale, not a coordinate system you have to learn.

## The coverage map

Each artifact is measured on four **intrinsic, codec-free** properties — computed
from the bytes alone, never from how any compressor performs (that would be
circular for a compression benchmark):

- **how random** — order-0 entropy (bits/byte)
- **how repetitive** — fraction of the file that is exact repetition
- **repeat distance** — how far back those repeats sit (local vs. long-range)
- **how big** — file size

The files are **sparse in this space — not a dense grid — but representative of the
whole.** Honesty rules for how we talk about it:

- Claim **coverage of the range**, never "complete"/"exhaustive"/"full coverage."
- The properties are **descriptive, not predictive** — they are the dimensions
  along which compressors are *known* to behave differently, so spanning them gives
  defensible diversity. They do **not** predict a file's ratio, and two files at
  the same coordinates need not compress alike (codec internals dominate).
- Never frame any measured quantity as a **theoretical limit or lower bound**.
- The map is the corpus's **design rationale and navigation aid — not a third
  product** alongside the corpus and the Score.

## The Squishy Score

**Squishy Score (of a codec) = the geometric mean of the per-file compression ratio
(uncompressed ÷ compressed) over the whole corpus — one vote per file.**

```
score = geomean over every file in the corpus of  ( uncompressed / compressed )
```

That's the whole formula. **No category weights, no kind weights, no size weights,
no tuning constants, and no threshold deciding which files count** — every real file
you'd want to compress is in. The design rationale (why this, and what was retired to
get here) is in `plans/score-weighting-critique-and-proposal.md`.

- **One vote per file.** Every file counts equally. The geometric mean is what keeps
  any single huge or tiny file from running away with the number (a 10× and a 0.1×
  cancel; the arithmetic mean would let the 10× dominate).
- **File *count* is the only weight — and it lives in curation, not the formula.** A
  kind sampled at two sizes votes twice; a single-size kind votes once. There's no
  hidden weighting math: balance is a property of *what's in the corpus*. So the
  edition is curated to keep the per-kind count even and every member **structurally
  independent** — no two scored members may share a source or lineage (otherwise
  correlated near-duplicates would stack votes; this is why the three same-source NOAA
  CSVs are being broken up — see the corpus table).
- **Near-incompressible files stay in.** photo/movie/weights score ~1.0×, which pulls
  the headline down *by nearly the same factor for every codec*, so they barely move
  the ranking — a codec can earn a sliver by genuinely squeezing them but can't win on
  them — and a corpus of real data honestly contains some incompressible files. The
  only files left out are the unmeasured model-weight **throughput ladder** (a
  speed/RAM fixture, not a ratio corpus member).
- **Categories are presentation only.** The five categories below organize the
  corpus (the coverage map, the by-category diagnostic table) and carry **no weight**
  in the score. The intrinsic axes — entropy, repetition, repeat-distance (3
  structural) plus size (operational) — are what make the *file selection*
  representative; the score itself just averages every file once.
- Reported as **"×"** to 2 decimals — a **dimensionless quality index, NOT a bit
  rate; do not derive bpb from it.** Always shown beside it is the **corpus bpb**
  = `8 · total_out_bytes / total_in_bytes` (byte-weighted, the operational
  bits-per-byte) to 3 decimals. The two are deliberate complements: the Score
  weights every file equally (anti-gaming); corpus bpb is the size-weighted rate.
- **Whole-corpus and periodic.** Recompute it when you change your *algorithm*, not
  per commit; the corpus may be tens of GB. It is meant to be *stable*.

### Reporting
One headline `×` paired with the byte-weighted corpus bpb, plus the **by-category**
diagnostic sub-table (Prose · Code & Web · Structured · Tabular/DB · Binary & Media):
each cell is just the geomean of that slice's files. A **by-size-bucket** sub-table
(`≤100 MB` vs `≥1 GB`) lands once the large rungs are acquired (today's corpus is all
small).

Both tables are diagnostic re-slices of the same data — a geomean over a subset of
the files — never a second formula and never a weight in the headline.

### Canonical run rule
One codec, **one setting, all files.** No per-file/per-corpus tuning, no
filename/extension/magic model selection, no shipping any corpus byte (or hash
equivalent, or a dictionary trained on the corpus) inside the codec. A
general-purpose large-window / `--long` mode is fine — it's a real feature, not a
corpus exploit. The number is a property of **(codec, setting, codec-version,
codec-argv, corpus-edition)**, reproducible to the bit for a pinned build. See
`RULES.md`.

### Edge cases (enforced by the runner)
- **Missing/empty/unverifiable file** → run FAILS closed; never scores a partial set.
- **Expansion** (ratio < 1) → flagged; expected only for near-incompressibles.
- **Duplicate member** → load-time assertion.
- **Lossless round-trip required** — a codec that doesn't decompress to the exact
  bytes has no valid score (`--verify`).

### Speed is NOT in the canonical score
Speed isn't reproducible across machines, so it can't be a citable scalar. It lives
on a leaderboard as `(ratio, comp MB/s, decomp MB/s, peak RAM)` on a named machine.
This is exactly why a speed-focused implementer (gzippy) uses the corpus as a
**test battery**, not a scoreboard.

## CI/dev use — per-file, no second number

There is no named subset and no second score. For everyday regression checks, pull
whatever files stress your code — each is individually addressable by name in the
edition manifest (`build/meta/edition.json`: per-file HTTPS URL + SHA-256 + kind) —
and consult the coverage map to judge representativeness for
your codec (representativeness is codec-specific; a single blessed subset would
imply a universality it can't have). A run over fewer than all files prints
per-file ratios for your own diffing and **does not print a Squishy Score**. Only a
run over the complete edition yields the citable headline.

## The corpus (kinds × sizes)

Memorable like Silesia — you can name the **kinds**. Every file is real,
provenanced, redistributable (PD / CC0 / CC-BY / Apache-2.0 / MIT); manifest in
`build/meta/LICENSE-MANIFEST.csv`.

| Category | Kinds (core member) | large (~1 GB) member? |
|---|---|---|
| **Prose** | `dickens`, `aozora` | yes — a large PD English-prose rung (Gutenberg aggregate; long-range vocab reuse). `enwik9` is **not** this rung — it's XML-wrapped, filed under `markup`. |
| **Code & Web** | `monorepo`, `minjs`, `markup` | `monorepo` (repo-scale); `markup`'s large rung = `enwik9` |
| **Structured** | `json`, `log`, `genome` | `log`, `genome` (redundancy grows with length) |
| **Tabular / DB** | `csv`, `parquet`, `sqlite` | csv large rung; `parquet`/`sqlite` |
| **Binary & Media** | `exe`, `photo`, `movie`, `weights` | **no** — incompressible |

**CSV independence fix (2026-06-08):** the three current CSVs are all NOAA GHCN
weather (same source/lineage — the small `csv` is a prefix of the 1.3 GB rung),
violating the no-shared-lineage rule. Keep **one** NOAA GHCN CSV (the large rung:
numeric, narrow, integer-heavy weather) and replace the other two with structurally
*different* real CSVs so the kind spans CSV structure, not one schema:
- **wide, text/categorical CSV** — many string columns, free-text + categoricals
  (e.g. a government open-data records export: NYC 311 / Chicago crimes, both public
  domain under their open-data terms) → high-cardinality strings, very different byte
  structure from numeric weather;
- **floating-point time-series CSV** — high-precision floats (e.g. an exchange OHLCV
  or scientific-sensor series under a permissive/PD license) → dense decimal-float
  bytes, near-incompressible columns, different again.
These two need acquire + measure + SHA before they enter the manifest — tracked in
`plans/squishy-1.0-readiness.md`.

**Size-sampling rule:** add a large rung **only where redundancy grows with
length** — that's where window size and long-range matching change which codec
wins. **Skip the GB rung for near-incompressible kinds** (`photo`, `movie`,
`weights`): a 3 GB JPEG tells you nothing a 90 MB one didn't. Result: ~5–6 kinds at
two sizes, the rest single-size. **Memorability guard: ~8–12 kinds × at most 2
sizes** — if you can't recite the kind list, the size axis has overgrown.

The **weights throughput ladder** (SmolLM2-135M → Qwen2.5-0.5B → Qwen2.5-1.5B, …)
is a **speed/RAM diagnostic, not a ratio signal** — kept out of the headline Score.

## Hosting (keep a tens-of-GB benchmark inside ~10–20 GB)
- **Host real data where the large rung is load-bearing** (real long-range
  structure — csv, genome, repo, prose).
- **Generate, don't host,** every incompressible/repetitive/pathological large
  input and the parallel-single-member-decode set — seeded deterministic
  generators + published SHA-256 (pinned PRNG); users materialize locally.
- Pathological/synthetic files appear only in a separate **Bounds** panel, never
  the headline.

## Anti-gaming (20-year lifespan)
1. **Versioned, dated editions on frozen DOIs.** The edition manifest pins the
   exact set of `(kind, size)` members — not just filenames — so two people citing
   "Squishy-2026" can't silently disagree. Overfit-to-2026 codecs stop winning on
   `Squishy-2030`. (Most important mechanism; free.)
2. **The geometric mean is itself the anti-overfit guard** — one vote per file means
   no single file can run away with the headline, and a codec that overfits one kind
   pays for it on the rest; document this in `RULES.md`.
3. **A rules file** enforcing the canonical run rule; the no-corpus-bytes rule
   applies *especially* to the large files (the lucrative dictionary target).

## Leaderboard (trust without pretending re-runs are free)
A full re-run is hours of compute + a multi-GB download, so tier it:
- **self-reported** — the submitter's tuple + reproduction command;
- **verified** — a third party re-ran the *full* corpus and matched.
A cheap smoke check (round-trip + sane per-file ratios on any chosen files) is fine
for your own confidence but is not a score and is never ranked. A submission must
carry `(codec, version, exact argv, edition)` + a one-line reproduction command;
the maintainer (or anyone) re-runs before it's "verified."

## Adoption (the one thing that matters most)
A one-command runner, `squishy-calculate`, that streams the edition's corpus,
verifies every file's SHA-256 (fail closed), runs the codec once per file under the
rules, and prints the number + sub-tables. Zero friction; paired with a permanent
Zenodo DOI. The runner matters more than the website or a paper — Silesia had
neither and won on file-set + simplicity.

## Status
The small members are acquired, validated, verified, and scored; today's published
board is a **partial run over those small members** (large rungs pending), not yet
a Squishy Score. The size-spanning corpus is being assembled (load-bearing large
rungs per kind) and the Score computed over the complete edition before it freezes.
No Squishy Score is cited until then. See `RULES.md` and
`plans/squishy-1.0-readiness.md`.
