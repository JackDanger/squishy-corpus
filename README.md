# Squishy Corpus

Two things live here:

1. **Calibrated corpus v4** — a measurement instrument for compression research: synthetic files designed to expose cells in the (H, S) space where codec families disagree on file ordering.
2. **Squishy fixture set** — real, weird, and intentionally broken files for testing compression/decompression libraries.

---

## Calibrated corpus v4

### What it is

Natural benchmarks (Silesia, enwik8, Calgary) confound entropy with structure: you cannot vary match density while holding entropy fixed in a real text file. This corpus can. Each file has independently controlled axes:

| Axis | Definition | Bins |
|------|-----------|------|
| **H** — marginal byte entropy | Shannon entropy of the byte histogram | H0 (0–1 bpb) … H6 (7.7–8 bpb) |
| **S** — structural compressibility | 1 − min(zstd-long27/bzip2/zpaq compressed) / raw | S0 (≤5%) … S4 (≥75%) |

The goal: find (H, S) cells where codec families disagree on file ordering. In those cells, Kendall-τ between e.g. zstd-19 and zpaq-m5 drops below 0.8 — meaning the two codecs rank files in a different order, revealing that they exploit different structural features.

### Generators

Three independent generator families cover the reachable H×S space:

- **Markov** — k-th order Markov chain (k ∈ {1, 2, 4}) with a SHAKE-256 transition kernel. Temperature τ controls marginal entropy; higher-order k introduces long-range structure exploited by context-mixing codecs.
- **LZ77-synth** — directly samples an LZ77 parse. Match fraction M, mean copy length L, window size W, and literal entropy H_lit are independently controlled. Generates the ground-truth parse as a `.parse.jsonl` sidecar.
- **Periodic** — fixed-size record streams with per-position entropy profiles. Structured vs. shuffled variants measure LZMA's pb/lp position-bit sensitivity.

### Physics constraints

Shannon's source-coding bound caps S from above: a file in H_bin h has max S ≤ 1 − H_lo/8. This makes 11 of the 35 H×S cells physically unreachable (e.g. H6/S1 requires compressing near-random bytes by 5–25%, violating entropy limits).

### Quick start

```sh
# Run calibration sweep (generate ~84 files at 4 MB, measure H and S)
uv run scripts/gen-synthetic.py --calibrate-only

# Benchmark codec suite over calibration files, compute Kendall-τ
uv run scripts/bench-v4.py --input build/raw/synthetic/calibration

# Results
cat build/bench/v4-kendall-tau.csv   # τ per H×S cell × codec pair
cat build/bench/v4-coverage.txt      # H×S coverage map
```

### Pilot results (84-file calibration sweep)

H×S coverage (files per cell):

```
        S0    S1    S2    S3    S4
  H0     0     0     0     0     0
  H1     0     0     0     0     8
  H2     0     0     0     1     6
  H3     0     0    17     1     3
  H4     0     1     5     1     3
  H5     0     0     0     0     2
  H6     9     4    10    10     3
```

Cells with Kendall-τ < 0.8 between codec families (30 disagree pairs across 7 cells):

| Cell | Pair | τ | Note |
|------|------|---|------|
| H6/S0 | zstd-1 vs zpaq-m5 | −0.889 | Near-incompressible: zpaq completely inverts the ordering |
| H6/S0 | zstd-19 vs zpaq-m5 | −0.444 | |
| H6/S2 | zstd-1 vs zpaq-m5 | 0.067 | High-entropy moderate-structure: near-random ordering |
| H6/S3 | zstd-1 vs zpaq-m5 | 0.200 | |
| H4/S2 | zstd-1 vs bzip2-9 | 0.000 | Periodic+LZ mix: zstd family and bzip2/zpaq completely disagree |
| H3/S2 | zstd-19 vs zpaq-m5 | 0.647 | Moderate entropy: 17 periodic files, visible codec split |
| H6/S1 | zstd-1 vs zstd-19 | 0.667 | Marginal-structure high-entropy: even zstd levels disagree |

The H6/S0 cell (near-random, incompressible) shows the sharpest disagreement: zpaq-m5 inverts the ranking established by both zstd levels, presumably because its context-mixing model finds noise-floor differences that escape LZ-family distance coding.

### Build pipeline

```sh
uv run scripts/gen-synthetic.py --calibrate-only   # Phase 1: calibration sweep
uv run scripts/bench-v4.py                         # Phase 2: benchmark + τ
```

---

## Squishy fixture set

**Browse:** [jackdanger.com/squishy](https://jackdanger.com/squishy/)
**Manifest:** [manifest.json](https://jackdanger.com/squishy/manifest.json) · [index.txt](https://jackdanger.com/squishy/index.txt)
**Checksums:** [CHECKSUMS.sha256](https://jackdanger.com/squishy/CHECKSUMS.sha256)

Includes:

- The [Silesia corpus](https://sun.aei.polsl.pl/~sdeor/index.php?page=silesia) (Sebastian Deorowicz, 2003)
- Modern web and data files (JSON, NDJSON, SQLite, Parquet, Protobuf, syslog, jQuery, Bootstrap, HTML)
- Deterministically-generated pathological inputs at every interesting decoder boundary (sub-window sizes; window-boundary triples for zstd, brotli, deflate; entropy extremes)
- Intentionally malformed fixtures shaped like real-world decoder CVE classes (CVE-2022-4899, CVE-2018-25032, CVE-2020-8927, Zip Slip)

Each input is compressed with every common codec at multiple levels and packaged in every common container format, then published to S3 + CloudFront with `Cache-Control: immutable`.

### Quick start

```sh
# Pull just the "pr" tier (~50 MiB) — right for per-commit CI
curl -s https://jackdanger.com/squishy/manifest.json \
  | jq -r '.artifacts[] | select(.tier=="pr") | .path' \
  | xargs -I{} curl -sO https://jackdanger.com/squishy/{}
```

### Build locally

```sh
make doctor          # check toolchain
make all             # full local build
make stream-publish  # build → upload → delete (peak disk ~2 GB)
```

---

## License

[MIT](LICENSE) for the build system (Makefile, scripts). Bundled and generated content carries its own provenance — see the published `README.txt`.
