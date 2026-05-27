# Squishy Corpus v4 — Real-File Heterogeneous Corpus

_Revised incorporating Opus advisor critiques (2026-05-26, updated 2026-05-27)_

---

## Readiness assessment (2026-05-27, after M-axis empirical review)

**Status: instrument is sound *in the high-H regime (H≥4)*; the low-H M
sub-grid is a measurement-resolution problem, not a corpus-construction
problem; ship v4 with calibrated honesty about where the M axis carries
signal and where it does not.**

What changed since the post-bundle assessment:
- Direct empirical measurement of M_greedy on calibrated files revealed
  the IID floor at 256K is materially different from the 4M floor used
  to build `_M_FLOOR_TABLE` (e.g., H=4: 0.6950 at 256K vs 0.7140 at 4M).
  At 256K/H=1.7 the floor is 0.9941, not the interpolated 0.9934 used
  today — a 0.0018 raw error that is multiplied 270× in normalized space.
- The dynamic range of `M_greedy_norm` at H<3 is so small (<0.01 at H=1.7,
  256K) that adjacent construction-M targets (M_target=0.00 and 0.10)
  produce *overlapping* M_greedy_norm distributions. The M sub-grid below
  H≈4 does not separate cells; it is below the instrument's noise floor.
- Match-length `mean_L` measurably shifts M_greedy_norm at the same
  M_target (the greedy parser chains long copies into longer-than-mean
  matches, inflating M). The L axis is not orthogonal to the M axis.
- The τ=0.962 rank-stability headline was computed across zstd-3 and
  gzip-6 only — both greedy LZ77+Huffman variants. The corpus axis is an
  LZ77 statistic, validated by LZ77 codecs. This is structurally circular
  and must be addressed before publication.
- `R_ref` clamps at `H_marginal` for H<1.86 with M>0. Files that were
  built with copies (M_target>0) currently advertise an `R_ref` that
  describes a literals-only encoding. Rate_ratio < 1.0 is doubly
  expected on these cells (and meaningless as a "construction-parse
  comparison").

What is still defensible:
- The measurement pipeline itself is well-defined, reproducible, and
  tested. The flaws are in *how the corpus uses* the measurements to
  assign cells, not in the measurements.
- 52 populated H×M×L cells is still 8–9× the coverage of Silesia /
  Canterbury in this space.
- Calibrated generator reproducibility (seeds, PMF, parse) is intact.
  Construction-time ground truth (`M_target`, `H_target`, `mean_L`) is
  exact and recorded in `ground-truth.json` — it is the *measurement* of
  M that is unreliable below H≈4, not the construction.
- Rank stability within the LZ77 family is τ=0.962. That is a real
  result, narrowly scoped.

What changes for v4 release:
- Replace `_M_FLOOR_TABLE` with size-keyed `_M_FLOOR_TABLES` measured
  directly from M=0 calibrated files at 256K and 4M, with H=1.7 as a
  direct anchor (no interpolation through a regime where the curve
  steepens sharply).
- Add a `M_norm_reliable` column to `manifest.csv` (and a matching flag
  in `ground-truth.json`). True iff `m_greedy_floor(H, size_bytes) < 0.90`,
  i.e., when the M dynamic range exceeds 10% of the unit interval.
- For *calibrated* files in cells where `M_norm_reliable=False`, bin on
  `M_target` instead of measured `M_greedy_norm`. Cell labels then
  reflect construction intent (which is exact and known) rather than
  the noisy measurement. Natural files in the same H band have no
  alternative — they remain binned on M_greedy_norm and are flagged.
- Add `R_ref_clamped` boolean to `manifest.csv` and ground-truth, and
  print a note in `score.py` when rate_ratio is computed on a clamped
  cell.
- Document the τ scope: τ=0.962 is *within the LZ77 family*. Re-running
  `compare` with bzip2 (BWT) or zpaq (context-mixing) is now a release
  gate, not an optional extension. If τ<0.9 cross-family, document
  explicitly which axis loses rank stability.
- Document the L→M confound: at fixed M_target, long-L files measure
  higher M_greedy_norm. This is an instrument property, not a corpus
  flaw, but it must be disclosed.
- Document the second-order copying artifact: `apply_lz_duplication`
  can copy bytes that were themselves copies. Context-mixing codecs may
  exploit this structure to compress below R_ref on high-H cells.

**Recommendation:** v4 instrument is publishable *with the changes
above*. Without them, the M sub-grid at H<3 is silently mislabeling
cells, and the headline τ result is structurally narrower than its
presentation suggests.

---

## M-axis reliability map (NEW, 2026-05-27)

For each (H, size) the empirical IID floor on M=0 calibrated files is:

| H   | floor @ 256K | floor @ 4M | M_norm dynamic range (4M) | Reliable? |
|-----|--------------|------------|----------------------------|-----------|
| 0.0 | 1.000        | 1.000      | 0.000                      | NO        |
| 1.0 | 0.9986       | 0.9989     | 0.001                      | NO        |
| 1.7 | 0.9941       | 0.9952     | 0.005                      | NO        |
| 2.0 | 0.9892       | 0.9911     | 0.009                      | NO        |
| 3.0 | 0.9332       | 0.9408     | 0.059                      | NO        |
| 4.0 | 0.6950       | 0.7140     | 0.286                      | YES       |
| 5.0 | 0.2360       | 0.2479     | 0.752                      | YES       |
| 6.0 | 0.0228       | 0.0249     | 0.975                      | YES       |
| 7.0 | 0.0014       | 0.0015     | 0.998                      | YES       |
| 8.0 | 0.000        | 0.000      | 1.000                      | YES       |

Reliability threshold: `floor < 0.90` (dynamic range > 0.10).

**Consequences:**
- H<4 cells: `M_greedy_norm` cannot distinguish M_target=0.00, 0.10,
  and 0.25 (and only barely distinguishes 0.50 from 0.75 even at 4M).
  Calibrated files in this regime are binned on `M_target` from
  `ground-truth.json` (Fix 3 below).
- H≥4 cells: `M_greedy_norm` has > 25% dynamic range; binning on
  measured `M_greedy_norm` is valid.
- Natural files: no `M_target` is available. They remain binned on
  `M_greedy_norm` even at H<4, and the manifest's `M_norm_reliable`
  flag warns the user that cell assignment is noisy.

The grid itself is not changed — the 6 M bins remain. What changes is
**which measurement is used for cell assignment** in each regime.

---

## Implementation status (2026-05-27, post-bundle, M-axis correction in progress)

Steps 1–18 complete. Applied: P0-1, P0-3, P0-4, P0-5, P0-6, P0-7, P1-1, P1-2, P1-4, P1-5, P1-6, P1-7.

Step 19 (M-axis correction) opened 2026-05-27 — see "M-axis reliability map" above and P0-8 / P1-9 / P1-10 below.

- `scripts/measure-corpus.py` — full v4 metric set implemented and tested
- `scripts/download-candidates.py` — Silesia, Canterbury, enwik8, Pizza&Chili, Gutenberg, Census
- Measurement CSVs: `build/bench/{corpus,candidates,calibrated}-measurements.csv`
  - `generator`, `H_target`, `M_target`, `R_ref`, `reference_bytes` — from ground-truth.json join
  - `M_greedy_norm` — normalized match fraction with IID floor removed (P0-1)
  - `L_p90` — used for L-axis binning (P1-4)
  - CI columns all-NaN for files < 10 MB (P1-1)
  - `.json` / `.md` metadata files excluded from scan (P1-6)
- `coverage.py` bins on `M_greedy_norm` (P0-5); fallback to `M_greedy` when norm is None
- `KNOWN_EMPTY_HM` updated for M_greedy_norm semantics: only H<0.5 and H7.5+/M0.80+
- Cell coverage: **52 populated H×M×L cells**, 44 empty (vs 39/31 under raw-M binning)
- `build/raw/curated/` — **52 symlinks**, one per populated cell
- **`build/bundle/`** — assembled by `scripts/build-corpus-bundle.py`:
  - `natural/` (13 files), `calibrated/` (112 files = 3 replicates × cells)
  - `manifest.csv` (128 rows, 14 columns including SHA-256 and all H/M/L coords)
  - `ground-truth.json` (112 entries with seeds, H_marginal, M_fraction, R_ref)
  - `score.py` — zero-dependency `list` / `score` / `compare` subcommands
- **Rank-stability headline metric**: zstd-3 vs gzip-6 over 39 calibrated cells yields
  corpus-level Kendall-τ = 0.962, and 38/38 cells show a consistent winner across
  all 3 replicates. **Scope: LZ77+Huffman family only**; cross-family τ is now
  a release gate (P1-10).

Remaining before public release:
1. Apply M-axis correction: size-keyed floor tables (P1-9), `M_norm_reliable`
   flag in manifest (P0-8a), construction-M binning for H<4 calibrated cells
   (P0-8b), `R_ref_clamped` flag (P0-8c).
2. Cross-family τ validation: run `score.py compare` with at least one
   non-LZ77 codec (bzip2 = BWT; zpaq or paq8 = context mixing). Document the
   result whether τ ≥ 0.9 or not (P1-10).
3. SHA-256 lockfile + Zenodo / DOI deposition (release engineering).

Non-blocking improvements deferred to v4.1: P0-2 (M_optimal), P1-3 (natural file
for H3.0-4.5/M0.40-0.60), all P2.

---

## Open issues (Opus advisor review, 2026-05-26; re-triaged 2026-05-27 + M-axis review)

**At-a-glance triage:**

| ID    | Status      | Blocking? | Description                                          |
|-------|-------------|-----------|------------------------------------------------------|
| P0-1  | DONE        | —         | `M_greedy_norm` instrument-floor correction          |
| P0-2  | OPEN        | NO (v4.1) | `M_optimal` — defer; keep `M_greedy_norm` for v4     |
| P0-3  | DONE        | —         | `R_ref` / `reference_bytes` in CSV schema            |
| P0-4  | DONE        | —         | 3-replicate bundle + Kendall-τ = 0.962 (in-family)   |
| P0-5  | DONE        | —         | Bin on `M_greedy_norm`                               |
| P0-6  | DONE        | —         | `rate_ratio` + "ratios < 1.0 expected" in `score.py` |
| P0-7  | DONE        | —         | `manifest.csv` + `score.py` reference impl shipped   |
| P0-8  | **OPEN**    | **YES**   | **M-axis correction (a–c) — see below**              |
| P1-1  | DONE        | —         | Subsample-CI guard for files < 10 MB                 |
| P1-2  | DONE        | —         | Natural vs calibrated bundle split (`--bundle` flag) |
| P1-3  | OPEN        | NO        | Natural file for H3.0-4.5 / M0.40-0.60               |
| P1-4  | DONE        | —         | L-bin uses `L_p90`                                   |
| P1-5  | DONE        | —         | `L_BREAKS` reconciled                                |
| P1-6  | DONE        | —         | Ground-truth orphan resolved                         |
| P1-7  | DONE        | —         | IID-floor regression test                            |
| P1-8  | SUBSUMED    | —         | Folded into P0-8a (anchor H=1.7 directly)            |
| P1-9  | **OPEN**    | **YES**   | **Size-keyed floor tables (256K, 4M)**               |
| P1-10 | **OPEN**    | **YES**   | **Cross-family τ validation (bzip2, zpaq, …)**       |
| P1-11 | OPEN        | NO        | Document L→M confound in score.py / grid.py          |
| P1-12 | OPEN        | NO        | Document second-order copying artifact               |

### P0 — Scientific validity blockers

**P0-1 (DONE, 2026-05-27)** — see prior entry; the *instrument* is correct,
but the floor table itself was miscalibrated (P0-8a) and dynamic range
collapses at low H (P0-8b).

**P0-2: Add M_optimal alongside M_greedy** — deferred to v4.1. The
greedy parse remains the instrument; M_optimal is a v5 redesign.

**P0-3 (DONE), P0-4 (DONE in-family), P0-5 (DONE), P0-6 (DONE), P0-7 (DONE)**
— see prior entries.

**P0-8 (NEW, 2026-05-27): M-axis correction — three coupled sub-fixes.**

The M sub-grid is structurally unreliable at H<4 and the current floor
table contains a 0.0018 calibration error at H=1.7 (270× amplified in
normalized space). Three sub-fixes are required and must ship together:

**P0-8a: Size-keyed floor tables with H=1.7 as a direct anchor.**

Replace `_M_FLOOR_TABLE: list[...]` in `squishy/corpus/metrics.py` with:

```python
_M_FLOOR_TABLES: dict[int, list[tuple[float, float]]] = {
    262144: [   # 256K — measured from M=0 calibrated files
        (0.0, 1.000),
        (1.0, 0.9986),
        (1.7, 0.9941),  # direct anchor; no interpolation through the steep regime
        (2.0, 0.9892),
        (3.0, 0.9332),
        (4.0, 0.6950),
        (5.0, 0.2360),
        (6.0, 0.0228),
        (7.0, 0.0014),
        (8.0, 0.000),
    ],
    4194304: [  # 4M — measured from M=0 calibrated files
        (0.0, 1.000),
        (1.0, 0.9989),
        (1.7, 0.9952),
        (2.0, 0.9911),
        (3.0, 0.9408),
        (4.0, 0.7140),
        (5.0, 0.2479),
        (6.0, 0.0249),
        (7.0, 0.0015),
        (8.0, 0.000),
    ],
}

def m_greedy_floor(h: float, size_bytes: int = 4_194_304) -> float:
    """Nearest-size table, linear interpolation between H anchors."""
    table_size = min(_M_FLOOR_TABLES, key=lambda s: abs(math.log(s / size_bytes)))
    table = _M_FLOOR_TABLES[table_size]
    # ... existing linear-interp logic over `table`
```

`measure_file()` passes the file's actual `size_bytes` through to
`m_greedy_norm()`. Update `test_floor_table_matches_iid_parse` to
exercise both anchored sizes (256K and 4M). Add a new test that asserts
H=1.7 / M_target=0 calibrated files measure M_greedy_norm < 0.01 at
both sizes.

**P0-8b: Bin calibrated H<4 files on `M_target`, not `M_greedy_norm`.**

In `coverage.py.load_rows()` (and `select-curated.py`, which uses the
same rows):

```python
# Inside the per-row loop, after H/M_greedy_norm are loaded:
m_target = _float(row.get("M_target"))
is_calibrated = row.get("generator", "").startswith("calibrated")

if is_calibrated and h < 4.0 and m_target is not None:
    m_for_binning = m_target          # construction intent, exact
else:
    m_for_binning = m_norm            # measured (only reliable for H>=4)

row["_M"] = m_for_binning
row["_m_bin"] = m_bin(m_for_binning)
# row["_M_measured"] remains M_greedy_norm for reporting
```

Add `M_norm_reliable` column to `manifest.csv` and to the per-file row
in `ground-truth.json`:

```python
M_norm_reliable = m_greedy_floor(H, size_bytes) < 0.90
```

`M_norm_reliable=True` for H≥4 at both sizes; False for H<4. Users
filtering for "trustworthy M-axis analysis" filter to True.

**Why this asymmetry between natural and calibrated is acceptable:**
We have no `M_target` for natural files; we cannot improve their cell
assignment, only flag it. Calibrated files in the H<4 region exist *to
fill cells that nature does not occupy*; they are by construction the
authoritative source for that region. Using their construction intent
for binning is more honest than using a measurement that has lost its
signal-to-noise.

**P0-8c: Add `R_ref_clamped` to manifest and ground-truth.**

In `calibrated.py.ground_truth_record()`:

```python
H_marginal = pmf_entropy(pmf)
copy_cost = _copy_bits_per_byte(mean_L)
rec["R_ref_clamped"] = H_marginal < copy_cost and M > 0
```

In `score.py`'s `score` subcommand, when reporting rate_ratio for a
row with `R_ref_clamped=True`, append:

```
note: R_ref clamped to H_marginal (M_target>0 but copying unprofitable at H<copy_cost)
```

This addresses the legitimate user confusion of "I built a file with
50% copies but R_ref describes a literals-only encoding" — without
changing R_ref's mathematical definition (which is correct).

### P1 — Important improvements

**P1-1 through P1-8** — see prior entries; P1-8 (linear interp in the
H=3.5-5.5 transition) is subsumed by P0-8a's direct H=1.7 anchor and the
verified measurement at H=4 / 5 / 6.

**P1-9 (NEW, 2026-05-27): Size-keyed floor table** — see P0-8a.

**P1-10 (NEW, 2026-05-27): Cross-family τ validation as a release gate.**

The corpus measures LZ77 statistics. The τ=0.962 result is between
zstd-3 and gzip-6, both greedy LZ77+Huffman variants. Validating that
the corpus orders cells stably *across codec families* is required
before claiming the corpus is publication-grade.

Action: run `score.py compare` with at least one of:
- bzip2 (BWT + RLE + Huffman; LZ-free)
- zpaq -5 (context mixing)
- paq8 (context mixing; gold-standard reference)

For each pair (LZ77 vs non-LZ77), report τ. Threshold:
- τ ≥ 0.9 across at least one cross-family pair → corpus is
  family-agnostic. Publish.
- τ ∈ [0.7, 0.9) → the corpus orders cells stably *within* a family but
  the LZ77 axes don't generalize. Document which cells flip ordering.
- τ < 0.7 → the corpus axes (H, M_greedy, L_p90) are LZ77-specific
  measurements that do not generalize. Re-scope the publication: the
  corpus is a LZ77 benchmark, not a universal compression benchmark.

This is a data-collection task, not a code change. Allocate one
benchmark run.

**P1-11 (NEW, 2026-05-27): Document L→M confound.**

In `squishy/corpus/grid.py` and `build/bundle/score.py`, add:

> **`M_greedy_norm` is not orthogonal to `L`**: at fixed `M_target`,
> longer construction match lengths produce higher measured
> `M_greedy_norm` because the greedy parser chains long copies into
> matches longer than the construction `mean_L`. A calibrated file with
> M_target=0.50 and mean_L=128 will measure higher M_greedy_norm than
> one with M_target=0.50 and mean_L=8. Cell assignments for L-long
> calibrated files at H<4 are particularly affected (which is why P0-8b
> binnes calibrated H<4 cells on `M_target` directly).

**P1-12 (NEW, 2026-05-27): Document second-order copying artifact.**

In `squishy/generators/calibrated.py` docstring, add:

> **Second-order copying artifact**: `apply_lz_duplication` copies bytes
> in-place from earlier in the buffer. When the source region itself
> contains a prior copy, the new copy carries that structure forward,
> creating statistical correlations beyond what the (H, M_target, mean_L)
> axes describe. Context-mixing codecs (cmix, paq8, nncp) may exploit
> this structure to achieve rate_ratio < 1.0 even on high-H cells where
> the construction parse looks near-optimal. This is a known property of
> the generator, not a bug, but it must be disclosed when comparing
> context-mixing codecs against LZ-family codecs on this corpus.
> Mitigation in a hypothetical v5: copy from a "pristine" buffer (the
> pre-duplication IID stream) rather than in-place. Not changed in v4
> because changing the generator invalidates the entire shipped corpus.

### P2 — Nice-to-haves (unchanged)

**P2-1: Bundle script should emit both 256K and 4MB per cell**
**P2-2: Bundle should include ground-truth.json and a score() reference** (DONE)
**P2-3: Add H=1.85, 1.90, 2.5 to calibrated generator**
**P2-4: ncd_halves is useful as a heterogeneity flag, not a primary axis**

---

## What this is

A compression benchmark corpus organized along three independently measured axes,
using real public-domain files as the primary corpus. Synthetic files fill cells
that don't occur in nature.

This is a **dataset paper + tools release**, not a theory paper. The thesis:

> Existing benchmark corpora (Silesia, Canterbury, enwik) cluster in a small
> region of (H, M, L) space. Our corpus provides coverage across the full
> achievable range, including cells that are real-world relevant (H=2, M=0.7:
> genomic data; H=7, M=0.3: scientific sensor data) but absent from every prior
> benchmark. We show that codec *ranking* is not stable across the space — codecs
> that win on Canterbury lose on high-M cells — making existing benchmarks
> unreliable predictors of field performance.

**Scope caveat (2026-05-27):** the M sub-axis is a greedy-LZ77 measurement
and is well-resolved only for H≥4. Below H=4, cells are populated by
construction (the calibrated generator), and cell labels reflect
construction intent (`M_target`), not measurement (`M_greedy_norm`). The
manifest's `M_norm_reliable` column makes this distinction explicit on
every row.

---

## Three axes

**H — marginal byte entropy (bits/byte)**
Shannon entropy of the byte frequency histogram.

**M — match fraction (three estimates, all reported)**
No single M definition is standard. We report:
- `M_greedy` — greedy LZ77 coverage (min_len=4, window=32K). Fast, reproducible,
  no codec dependency. **WARNING**: not separable from H below H≈4 due to
  spurious 4-gram coincidences (see P0-1, P0-8). Use `M_greedy_norm` for
  cross-H comparisons.
- `M_greedy_norm` — normalized: `(M_greedy − M_floor(H, size)) / (1 − M_floor(H, size))`.
  Removes the H-correlated, size-correlated floor. **Reliable only when
  `M_norm_reliable=True` (H≥4 at the file's size tier).**
- `M_target` — for calibrated files: the construction parameter. Exact;
  recorded in `ground-truth.json`. Used for cell binning of calibrated
  files at H<4 (where measurement is unreliable).
- `M_zstd` — fraction of bytes emitted as match by zstd's own parser.
  Most closely reflects what LZ-family codecs actually see. Requires zstd
  with verbose match-stats output. Reported when available.
- `M_optimal` — TBD (v4.1+). From a length-cost minimal parse (P0-2).

**Cell assignment policy:**
- Natural files: bin on `M_greedy_norm`. Flag with `M_norm_reliable`.
- Calibrated H<4: bin on `M_target`. `M_norm_reliable=False` is shown.
- Calibrated H≥4: bin on `M_greedy_norm`. `M_norm_reliable=True`.
- Keep `M_greedy` in the CSV unchanged for reproducibility.

**L — match length statistics (from greedy parse)**
- `L_median` — median match length (P50). Reported but not used for binning
  (too many files at the min_len floor of 4–5; see P1-4).
- `L_p90`    — 90th percentile. **Used for cell binning** (more informative than median).
- `L_geomean`— geometric mean. Scale parameter for log-normal length distributions.

Cell assignment uses `L_p90` for binning. Note: L is not orthogonal to
M_greedy_norm (P1-11); L-long files at the same M_target measure higher
M_greedy_norm.

**σ_H — internal heterogeneity (diagnostic only)**
Standard deviation of H in sliding windows at {1K, 16K, 256K}.
Ratio sigma_H_1k / sigma_H_256k measures scale-invariance of heterogeneity.

**ncd_halves — heterogeneity flag (diagnostic only)**
NCD(first_half, second_half) under zstd-19. Useful for detecting non-stationarity;
not used for cell assignment (see P2-4).

---

## Grid

Non-linear, anchored to known regime boundaries:

**H breakpoints:**
```
0.5   very low entropy (DNA in 2-bit encoding, run-length data)
1.5   low entropy (repetitive logs, structured records)
1.86  copy-cost threshold: below here LZ copying is unprofitable vs literals
3.0   medium-low (natural language post-BWT approx)
4.5   typical post-BWT order-0 entropy of English text (Fenwick 1996)
6.0   typical entropy of x86 machine code (Lucco-Sharp 2003)
7.5   typical entropy of JPEG entropy-coded streams
7.95  near-uniform (AES output, /dev/urandom)
```

**M breakpoints** (applied to `M_greedy_norm` for natural and calibrated H≥4;
applied to `M_target` for calibrated H<4):
```
0.05  noise floor (near-random data, coincidental matches)
0.20  light structure (audio, floating-point scientific data)
0.40  moderate repetition (source code, JSON/CSV with repeated keys)
0.60  heavy repetition (natural language, executables)
0.80  extreme repetition (XML, database records, log files)
```

**L_p90 breakpoints (replaces L_median; see P1-4):**
```
short   L_p90 < 10    (short-pattern repetition; calibrated L3 generator tier)
medium  10 ≤ L_p90 < 60  (phrase-level; calibrated L8 and L32 tiers)
long    L_p90 ≥ 60    (record-level; calibrated L128 tier)
```
These breakpoints correctly separate the four calibrated L tiers (mean lengths 3/8/32/128).
Using 20/100 would collapse L3 and L8 into "short" and leave L32 in "short" too.

8 × 6 × 3 = 144 theoretical cells. Realistic: ~35–45 populated.

**Known physics-empty (H, M) pairs (post-`M_greedy_norm` binning, source of truth in `grid.py`):**
```python
KNOWN_EMPTY_HM = {
    # H<0.5: zeros-like data has M_greedy_norm≈1; only the M0.80+ column
    # is reachable. Lower-M cells require deliberate un-copies at H≈0,
    # which is not meaningful.
    (0, 0), (0, 1), (0, 2), (0, 3), (0, 4),
    # H7.5+ / M0.80+: at near-random entropy the calibrated generator's
    # current M_target ceiling (0.75) lands in M0.60-0.80 after
    # normalization. May be a generator gap rather than physics.
    (7, 5),
}
```

---

## Real-file sources

License note: Wikipedia is CC-BY-SA (requires attribution + share-alike).
US government data (NCBI, NASA, Census) is public domain. Project Gutenberg
pre-1928 texts are public domain. All files require license audit before
publication.

### H ≈ 0.5–1.5 (very low entropy)
| Target cell | Source | License |
|-------------|--------|---------|
| M=0.6–0.8, L=long | NCI chemical compound database | NCBI (US gov, PD) |
| M=0.4–0.6, L=medium | NCBI RefSeq genome FASTA (bacteria) | NCBI (US gov, PD) |

### H ≈ 1.5–3.0 (low entropy)
| Target cell | Source | License |
|-------------|--------|---------|
| M=0.6–0.8, L=long | Server access log files | Varies |
| M=0.4–0.6, L=medium | Silesia: mr (medical records) | Freely redistributable |
| M=0.2–0.4, L=short | DNA sequence FASTA (NCBI) | NCBI (PD) |

### H ≈ 3.0–4.5 (medium entropy — natural language) — KEY GAP
| Target cell | Source | License |
|-------------|--------|---------|
| M=0.4–0.6, L=medium | **Genomic SAM/BAM plain text** (new) | NCBI (PD) |
| M=0.4–0.6, L=medium | **Protein FASTA multi-sequence** (new) | NCBI (PD) |
| M=0.4–0.6, L=medium | **FITS astronomy tables** (new) | NASA (PD) |
| M=0.4–0.6, L=medium | **PCAP packet captures** (new) | Check license |
| M=0.6–0.8, L=long | OpenStreetMap PBF extract | ODbL |
| M=0.6–0.8, L=long | Wikipedia XML dump sample | CC-BY-SA |

### H ≈ 4.5–6.0 (medium-high entropy — code, structured binary)
| Target cell | Source | License |
|-------------|--------|---------|
| M=0.6–0.8, L=medium | Linux kernel C source | GPL-2.0 |
| M=0.4–0.6, L=medium | Silesia: samba, mozilla | Freely redistributable |
| M=0.2–0.4, L=short | US Census CSV exports | US gov, PD |

### H ≈ 6.0–7.5 (high entropy — binary, compressed-adjacent)
| Target cell | Source | License |
|-------------|--------|---------|
| M=0.2–0.4, L=short | Silesia: x-ray | Freely redistributable |
| M=0.2–0.4, L=short | Uncompressed PCM audio (LibriVox WAV) | Public domain |

### H ≈ 7.5–8.0 (near-random)
| Target cell | Source | License |
|-------------|--------|---------|
| M=0.1–0.2, L=short | NASA FITS floating-point data | US gov, PD |
| M≈0.0, any | AES-256-CTR of /dev/urandom | N/A |

---

## Crosswalk with existing corpora

| Prior corpus | File | Our cell (approx) |
|-------------|------|-------------------|
| Calgary | book1, book2 | H≈4.5, M≈0.5, L=medium |
| Calgary | pic (FAX image) | H≈0.8, M≈0.8, L=long |
| Calgary | obj1, obj2 | H≈6.0, M≈0.4, L=medium |
| Canterbury | alice29.txt | H≈4.6, M≈0.45, L=medium |
| Silesia | nci | H≈2.4, M≈0.89, L=long |
| Silesia | sao | H≈7.5, M≈0.54, L=short |
| enwik8 | enwik8 | H≈4.9, M≈0.85, L=long |
| Pizza&Chili | dna | H≈1.97, M≈0.6, L=short |

Silesia/Canterbury cluster in H∈[4,6], M∈[0.4,0.9], L=medium — ~6 of 35–45 cells.
The H<3 and H>7 regions are completely absent from all prior benchmarks.

---

## File selection workflow

1. **Download candidates** — `scripts/download-candidates.py`
   Target size: 10–100 MB. Hard minimum: 10 MB (CI reliability; see P1-1 below).

2. **Measure all candidates** — `scripts/measure-corpus.py`
   Full metrics: H, M_greedy, M_greedy_norm (size-aware floor), L_median,
   L_p90, L_geomean, sigma_H (3 scales), ncd_halves, R_ref (calibrated only),
   R_ref_clamped (calibrated only), M_norm_reliable, size_bytes.

3. **Assign to cells** using the policy in "Three axes" above:
   - Natural: M_greedy_norm + M_norm_reliable flag.
   - Calibrated H<4: M_target.
   - Calibrated H≥4: M_greedy_norm.
   All files use L_p90 for the L axis.

4. **Select representatives** — `scripts/select-curated.py`
   Prefer: largest file with L_ci_rel < 0.15.
   Include all 3 seed replicates for calibrated cells (see P0-4).
   Tag each cell: `source_type: natural | calibrated`.

5. **Fill gaps with calibrated synthetics** — cells with no natural candidate
   get a calibrated file. Marked `synthetic` in manifest.

6. **Lock and bundle** — `scripts/build-corpus-bundle.py`:
   - Emit both 256K and 4MB per cell (see P2-1)
   - Include ground-truth.json, manifest.csv, score.py (see P2-2)
   - Manifest includes `M_norm_reliable` and `R_ref_clamped` columns
   - Separate natural vs calibrated tarballs (see P1-2)
   - SHA-256 lockfile for byte-identical reproducibility

---

## Statistical validity

**Measurement CIs**
Subsample CIs (stratified, 10 strata × 5 samples = 50 × 1MB windows) characterize
per-region variability across the file. **Only valid for files ≥ 10 MB**
(`strata × ss = 10 × 1MB`). For smaller files, all CI fields are None (see P1-1).

**Cell occupancy**
A cell is "well-populated" if its best representative has L_ci_rel < 0.15
(L_median variability < 15%). Report under-populated cells explicitly.

**Rank stability (P0-4)**
The primary publication metric. For each pair of calibrated-cell replicates
(s0, s1, s2), compute Kendall-τ of codec ranks. Report mean τ across all pairs
and all codec pairs. A corpus is publication-ready when τ > 0.9 across the
benchmark panel **including at least one cross-family pair** (P1-10).

In-family τ (zstd-3 vs gzip-6) is 0.962 today. Cross-family τ (vs bzip2 /
zpaq) is the remaining release gate.

---

## Corpus structure

```
build/raw/candidates/           ← all downloaded files
build/raw/curated/              ← best representative per populated cell (symlinks)
build/raw/calibrated/           ← synthetic files (256K + 4M, no 64M)
  4M-H5p0-M0p50-L8-s0.bin

build/bench/corpus-measurements.csv     ← Silesia measurements
build/bench/candidates-measurements.csv ← candidate file measurements
build/bench/calibrated-measurements.csv ← calibrated file measurements (fast mode)
build/bench/calibrated-ci.csv           ← codec benchmark CIs
build/bench/tools.lock                  ← codec version pins

build/raw/curated/
  H0.5-1.5__M0.80p__L-short__calibrated__4M-H1p0-M0p00-s0.bin  → symlink
  H1.86-3.0__M0.80p__L-short__pizza-chili-extracted__dna.50MB   → symlink
  ...  (52 total)
```

---

## Bundle composition

**`natural.tar`** — natural files only (13 files currently; grow as downloads expand)
**`calibrated-instrument.tar`** — full H×M×L calibrated grid, 3 replicates per cell (112 files)
**`all-cells.tar`** — one representative per populated cell (natural preferred over calibrated)

Each bundle ships with:
- `manifest.csv` — cell tuple, file hash, H/M/L coordinates, source_type,
  `M_norm_reliable`, `R_ref_clamped`
- `ground-truth.json` — calibrated-only: H, R_ref, R_ref_clamped, seeds, generator
- `score.py` — computes rate_ratio (with clamp annotation), Kendall-τ rank
  stability, per-cell summary

Do not concatenate files into a single stream — keep as separate members in the
tarball so codecs exploiting solid-block context don't get an artificial advantage.

---

## Calibrated generator

H_VALUES = [1.0, 1.7, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
M_VALUES = [0.00, 0.10, 0.25, 0.50, 0.75]
Sizes: 256K and 4M (64M excluded — disk budget constraint)
L-sweep: H × mean_L ∈ {3, 8, 32, 128} at fixed M=0.50

**Planned additions (P2-3):** H = 1.85, 1.90, 2.5 to sample the copy-cost boundary.

R_ref formula (for calibrated files only):
```
R_ref = (1−M)·H_marginal + M·(H_D + H_L(mean_L)) / mean_L
      clamped at H_marginal
H_D = H(log-uniform 1..32768) ≈ 10.55 bits
H_L = H(geometric p=1/mean_L)
```
R_ref is NOT a lower bound; it is the cost of the construction parse. Real codecs
can beat it by finding better parses.

When `H_marginal < (H_D + H_L)/mean_L` (i.e., H<1.86 at default mean_L=8),
the clamp activates: `R_ref = H_marginal` and the file is tagged
`R_ref_clamped=True`. Rate_ratio on these cells compares the codec against
a literals-only reference even though the file was constructed with copies;
this is mathematically correct (R_ref is the cheapest of the two valid
encodings the *reference* coder could choose) but unintuitive, so the flag
is surfaced to users.

**Second-order copying artifact (P1-12):** copies may be of bytes that
were themselves copied, introducing higher-order structure beyond what
(H, M_target, mean_L) describes. Context-mixing codecs may exploit this
and achieve rate_ratio < 1.0 on high-H cells. Disclose when publishing
comparisons against cmix/paq8/nncp.

---

## Codec panel

| Codec | Levels | Version |
|-------|--------|---------|
| gzip  | -9 | system gzip |
| bzip2 | -9 | system bzip2 |
| xz    | -6, -9 | system xz |
| zstd  | -1, -3, -9, -19, --ultra -22 | zstd 1.5.x |
| brotli | -1, -6, -11 | system brotli |
| lz4   | -1, -9 | system lz4 |
| lzma  | -6 | xz-utils lzma |
| zpaq  | -1, -4 | zpaq 7.15 |
| bzip3 | default | latest |

Record tool versions at benchmark time in `build/bench/tools.lock`.

**Cross-family τ requirement (P1-10):** at minimum, run `score.py compare`
with one LZ77-family codec (zstd or gzip) and one non-LZ77-family codec
(bzip2 = BWT, or zpaq = context mixing). Report τ. This is a release gate.

---

## Implementation order (updated)

### Completed
1. ✅ `scripts/measure-corpus.py` — H, M_greedy, L_median/p90/geomean, sigma_H, ncd_halves, subsample CIs
2. ✅ `scripts/download-candidates.py` — Silesia, Canterbury, enwik8, Pizza&Chili, Gutenberg, Census
3. ✅ Measure all candidates → `build/bench/candidates-measurements.csv`
4. ✅ Identify cell coverage — 52 cells populated; physics-empty cells documented
5. ✅ `scripts/select-curated.py` → `build/raw/curated/` (52 symlinks)
6. ✅ **P0-1 (Option B)** — `M_greedy_norm` added to `metrics.py` and CSV schema.
7. ✅ **P1-1** — `subsample_cis()` returns NaN for files < 10 MB.
8. ✅ **P0-3** — `R_ref`, `reference_bytes`, `generator`, `H_target`, `M_target` added to `FIELDNAMES`.
9. ✅ **P1-4** — `l_bin()` uses `L_p90`.
10. ✅ **P0-5** — `coverage.py` and `select-curated.py` bin on `M_greedy_norm`.
11. ✅ **P1-5** — `L_BREAKS = [0, 10, 60, ∞]` consistent.
12. ✅ **P1-6** — calibrated set now 488/488 with ground truth.
13. ✅ **P1-7** — IID-floor regression test.
14. ✅ **P0-4 (in-family)** — bundle ships 3 replicates per cell; in-family τ = 0.962.
15. ✅ **P0-6 (rate_ratio)** — emitted by `score.py score` with documentation.
16. ✅ **P0-7 / step 18 bundle composer** — `build/bundle/` shipped.

### Release gate for v4 (in priority order)
17. **P0-8a: Size-keyed floor tables.** Update `metrics.py` and tests.
    Re-emit `M_greedy_norm` in all three measurement CSVs (cheap; the
    metric is recomputable from M_greedy and the new table).
18. **P0-8b: Construction-M binning for calibrated H<4 cells.**
    Update `coverage.py.load_rows()`. Re-run `select-curated.py`. Add
    `M_norm_reliable` column to `manifest.csv` and to `ground-truth.json`.
19. **P0-8c: `R_ref_clamped` flag.** Update `calibrated.py.ground_truth_record()`
    and re-emit `ground-truth.json` and `manifest.csv`. Update `score.py`'s
    report to annotate clamped rows.
20. **P1-10: Cross-family τ run.** Compress all 112 calibrated files with
    bzip2 -9 (or zpaq -5) and run `score.py compare` against the existing
    zstd-3 results. Document τ. If τ < 0.9, narrow the publication scope.
21. **P1-11 / P1-12 documentation:** add the L→M confound and second-order
    copying paragraphs to `grid.py`, `score.py`, and `calibrated.py` docstrings.
22. **Run the full benchmark** (codec panel × all cells).
23. **Lock corpus** — SHA-256 lockfile, Zenodo deposition, DOI.

### v4.1 (deferred; non-blocking)
24. **Fix P0-2 (M_optimal)** — add an LZSS / length-cost minimal parse and a
    second M column. Switch binning from `M_greedy_norm` to `M_optimal` in v5.
25. **Fix P1-3** — download genomic SAM, protein FASTA, FITS, PCAP for the
    H3.0-4.5/M0.40-0.60 natural gap.
26. **64M size tier** — measure floor at 64M, extend `_M_FLOOR_TABLES`.
27. **P2-*** — denser H sweep near 1.86, both-size emission per cell, etc.
