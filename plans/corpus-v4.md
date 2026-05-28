# Squishy Corpus v4

## Thesis

Modern lossless compressors are not distinguishable on existing public benchmarks. On Silesia and Calgary, zstd-19, xz-9, brotli-11, and bzip2-9 fall within a 4% compressed-size band on every file. This does not mean the codecs are equivalent — it means the benchmarks do not span the input space where they diverge.

This corpus is a measurement instrument designed to find, characterize, and report the regions of input space where compressor families disagree. The deliverable is not a corpus alone but a corpus + benchmark report: every release includes measurements for a fixed set of codecs, so users see what the corpus actually discriminates before they download it.

---

## What is in the box

The release is a single tarball containing:

1. `files/` — corpus files, organized by `domain/size/filename`
2. `manifest.csv` — one row per file, columns defined below
3. `benchmark.csv` — one row per (file, codec, level) tuple, codec output sizes
4. `report.html` — disagreement map, codec ranking flips, headline findings
5. `LICENSE-MANIFEST.csv` — license, source URL, sha256 for every natural file
6. `tools/` — measurement scripts pinned by version, reproducible from raw files

Total release size: ~12 GB. The tarball is content-addressed; its sha256 is the version identifier. There is no v4.0 / v4.1 distinction — any change to any file produces a new identifier.

---

## Two corpora, one schema where they overlap

### Synthetic corpus

Generated files with known construction parameters. Used to span the (entropy, structure) plane uniformly. Synthetic files carry a `construction` column and `*_target` columns that are absent on natural files. They are never aggregated with natural files for codec ranking. The benchmark report presents synthetic and natural findings side by side, clearly labeled.

Synthetic file count: 240 files across 60 cells × 4 sizes.

### Natural corpus

License-clean real-world files, sliced or concatenated to exact sizes from upstream sources. Natural files have only measured properties — no ground-truth construction parameters. Each natural file links to a single upstream source (no cross-source concatenation) and carries a `LICENSE-MANIFEST.csv` entry with the full provenance chain.

Natural file count: 96 files across 24 source domains × 4 sizes. Domains with insufficient bytes at a given size are omitted — no padding, no repetition.

---

## Axes

### H — Marginal byte entropy

Shannon entropy of the byte distribution. Exact, O(n), no estimation.

**Bins:**

| Bin | Range | Anchor data |
|-----|-------|-------------|
| H0 | < 1.0 | Sparse / near-constant |
| H1 | 1.0–2.0 | DNA, base-4 sensors |
| H2 | 2.0–3.5 | Structured logs |
| H3 | 3.5–5.0 | English text, source code |
| H4 | 5.0–6.5 | x86 binaries, structured archives |
| H5 | 6.5–7.7 | Compressed text streams |
| H6 | 7.7–8.0 | Near-random (JPEG entropy-coded, AES) |

### S — Structural compressibility

A single ratio, defined operationally and codec-neutral-by-construction:

```
S = 1 − (min_compressed_bytes_over_reference_set / raw_bytes)
```

Where `reference_set` is a fixed ensemble of three codecs from disjoint families, run at fixed levels:

- `zstd --long=27 -19` (LZ77 with large window)
- `bzip2 -9` (BWT)
- `lpaq8 -7` (context mixing)

`min_compressed_bytes_over_reference_set` is the minimum over the three. S ∈ [0, 1]: high S means at least one of three structurally-different codecs found substantial structure.

S is reproducible (codec binaries pinned, command lines fixed), interpretable ("best of three disjoint families compressed this to (1−S) of its raw size"), and codec-neutral in the sense that no single codec family defines it.

**Bins:**

| Bin | S range | Interpretation |
|-----|---------|----------------|
| S0 | < 0.05 | Incompressible by all three families |
| S1 | 0.05–0.25 | Marginal structure |
| S2 | 0.25–0.50 | Moderate structure |
| S3 | 0.50–0.75 | Strong structure |
| S4 | ≥ 0.75 | Near-fully compressible |

### Domain (categorical, natural only)

One of: `text-english`, `text-code`, `text-markup`, `genome`, `image-raw`, `image-jpeg`, `audio-pcm`, `audio-compressed`, `binary-x86`, `binary-arm`, `archive-tar`, `archive-zip`, `pdf`, `csv-numeric`, `csv-categorical`, `log-structured`, `sensor-iot`, `database-dump`, `dict-wordlist`, `parquet`, `json-api`, `xml-feed`, `protobuf-stream`, `mixed`.

Synthetic files use `domain = synthetic-<construction>`.

---

## Size tiers

Three tiers: **4 MB, 64 MB, 1 GB**

- **4 MB**: Smallest size at which BWT block amortization (~4 bzip2 blocks), zstd long-distance matching, and context-mixing warmup stabilize. All cells.
- **64 MB**: Working size for the bulk of the corpus. All codec implementations reach steady-state behavior by this point. All cells with available data.
- **1 GB**: One file per domain, only in domains with sufficient license-clean upstream data. Verifies that codec rankings at 64 MB persist at scale. Benchmarked separately from the main cell grid.

The 256K tier is dropped: results there are dominated by codec startup transients, and cell ordering at 256K does not reliably predict cell ordering at 4 MB. The 16M and 256M tiers are folded into 64 MB. The 1 GB tier is retained as a scale-validation tier.

---

## Synthetic generators

Three constructions, each producing files at all three sizes. Construction parameters are recorded exactly; measured properties (H, S, per-file diagnostics) are the source of truth for cell placement. No `R_ref` or reference-rate claim is made.

### markov

Stationary k-th-order Markov chain, k ∈ {1, 2, 4}. Transition kernel derived from SHAKE-256 over (seed, state). State weights use a geometric-rank construction (`weight_i = exp(-τ × rank_i)`) where τ controls entropy without depending on the hash distribution. Parameters recorded; H is measured.

### lz77-synth

Direct LZ77 parse sampling. Parameters:

- Copy length distribution: geometric with mean L ∈ {4, 16, 64, 256}
- Copy distance distribution: log-uniform over [1, W], W ∈ {4K, 32K, 256K, 4M}
- Copy fraction M ∈ {0.0, 0.3, 0.6, 0.85}
- Literals: tilted-exponential PMF tuned to target H ∈ {2, 4, 6, 8}

Second-order copy artifact (copies-of-copies) is mitigated by rejecting candidate copies whose source range overlaps a previously emitted copy. Rejected-copy fraction is recorded per file.

### periodic

Fixed-period record streams for periodicity-sensitive codecs. Record sizes P ∈ {4, 8, 16, 32, 256} with two per-position entropy profiles:

- `gradient`: linear H_i from 1 to 8 across positions within record
- `block`: low-H header bytes, high-H payload bytes

Each file is paired with a shuffled variant whose per-position entropy is preserved but positional structure is destroyed. The structured/shuffled compression gap measures periodicity sensitivity.

---

## Manifest columns

**Identity** (all files):
- `path` — relative path within `files/`
- `sha256` — hex digest of file contents
- `size_bytes`
- `corpus` — `synthetic` or `natural`
- `domain`

**Measured axes** (all files):
- `H` — marginal byte entropy, computed exactly
- `S` — structural compressibility (see definition)
- `H_bin`, `S_bin` — bin labels

**Per-file diagnostics** (all files, not used for binning):
- `H8` — H(byte | 7 preceding bytes); exact for files ≤ 256 MB, block-bootstrap-estimated with CI on larger files
- `H8_ci_lo`, `H8_ci_hi` — bootstrap CI bounds (equal to `H8` for files ≤ 256 MB)
- `Lp90_lz77_32k` — 90th-percentile greedy LZ77 match length, 32 KB window
- `Lp90_lz77_256k` — same, 256 KB window (pair shows window sensitivity)
- `M_lz77_32k` — greedy LZ77 match density, 32 KB window (provided for continuity; not a binning axis)
- `ncd_halves` — normalized compression distance between file halves (zstd-19; non-stationarity indicator)

**Construction parameters** (synthetic only, empty for natural):
- `construction` — `markov` | `lz77-synth` | `periodic`
- `seed`
- `H_target`
- `construction_params_json`

**Provenance** (natural only, empty for synthetic):
- `source_url`
- `source_sha256` — sha256 of upstream artifact before slicing
- `source_byte_offset`, `source_byte_length`
- `license` — SPDX identifier
- `license_url`

---

## Benchmark

`benchmark.csv` ships with every release.

Codecs and levels run for every file:

| Codec | Levels | Family |
|-------|--------|--------|
| zstd | 1, 9, 19 | LZ77 |
| zstd --long=27 | 19 | LZ77 (large window) |
| xz | 1, 6, 9 | LZMA |
| brotli | 1, 6, 11 | LZ77+context |
| gzip | 9 | LZ77 (DEFLATE) |
| bzip2 | 9 | BWT |
| lz4 | 1 | fast LZ |
| zpaq | 3, 5 | context mixing |
| lpaq8 | 7 | context mixing |

`benchmark.csv` columns: `file_sha256`, `codec`, `level`, `compressed_bytes`, `compress_seconds`, `decompress_seconds`, `peak_rss_bytes`, `tool_version`, `command_line`.

Codec binaries are pinned to specific versions recorded in `tools/codec-versions.txt`. The benchmark reruns for every release; results are content-addressed by `(corpus_sha256, codec_versions_sha256)`.

---

## Headline experiment: codec disagreement map

For every (H_bin, S_bin) cell containing ≥ 4 files, the report computes Kendall-τ between every codec pair in the benchmark set. The disagreement map is a heatmap of `1 − τ` across cells, one heatmap per codec pair.

**The thesis the corpus tests**: there exist (H_bin, S_bin) cells where Kendall-τ between zstd-19 and lpaq8-7 drops below 0.8, and those cells contain natural files — not only synthetic.

If true: the corpus identifies real-world regimes where codec choice matters.
If false: the corpus has produced a negative result that is itself publishable — existing codecs are operationally equivalent on the natural data we could collect.

This experiment runs as part of the release pipeline. The report is shipped in the tarball. If the experiment does not complete, the release does not ship.

---

## License discipline

Every natural file's upstream source carries a license from: CC0, CC-BY 4.0, CC-BY-SA 4.0, ODC-BY 1.0, public domain by US federal authorship, or a license expressly granting redistribution.

CC-BY-NC, CC-BY-ND, "all rights reserved with implicit permission," and dataset cards without explicit license terms are excluded.

License verification is done by hand for every source, recorded in `LICENSE-MANIFEST.csv` with the URL of the license statement and an archive.org snapshot at the time of inclusion. A natural file is not in the corpus unless a human has read its license terms.

---

## Out of scope

- Lossy compression
- Format-aware compression (PNG, FLAC, etc.) — files in these formats are included; format-aware encoders are not benchmarked
- Streaming latency — wall time is measured but streaming behavior is not characterized
- Neural compressors — the corpus is suitable for evaluating them; the release does not include their benchmark results due to portability requirements (GPU, model weights)

---

## Implementation phases

Each phase has a hard exit criterion. Nothing that fails an exit criterion carries forward as "documented as a known issue."

**Phase 1: Schema and tooling.**
Land the manifest schema, exact H measurement, S measurement driver (runs three codecs, takes minimum), and H8 block-bootstrap estimator. Test suite covers both on a 20-file pilot set.
_Exit criterion_: `make manifest` produces valid `manifest.csv` on pilot set; H8 CI implementation passes synthetic non-stationary test cases.

**Phase 2: Synthetic generators.**
Land `markov`, `lz77-synth` (with second-order copy rejection), and `periodic` generators, each with a deterministic PRNG and a ground-truth invariant test. Generate 240-file synthetic set.
_Exit criterion_: every synthetic file's measured H is within 0.05 bpb of target.

**Phase 3: Natural corpus.**
License-audit and slice 96 natural files.
_Exit criterion_: `LICENSE-MANIFEST.csv` has a verified entry for every natural file, with reviewer initials and an archive.org URL for license terms at time of inclusion.

**Phase 4: Benchmark.**
Run 9-codec benchmark across all 336 files. Pin codec binaries.
_Exit criterion_: `benchmark.csv` is complete with no NaN entries; codec versions recorded in `tools/codec-versions.txt`.

**Phase 5: Report.**
Compute disagreement map. Render report HTML. State the thesis result.
_Exit criterion_: Report is in the tarball; thesis result (supported / not supported / partial) is stated on page 1.

**Phase 6: Release.**
Tarball, sha256, S3 upload, Zenodo deposit for archival DOI.
_Exit criterion_: a third party can download the tarball, run `make verify`, and reproduce every measurement and benchmark number from the tarball contents alone.
