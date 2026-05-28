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

### Results: balanced corpus (95-file, 5 per cell)

H×S coverage:

```
        S0    S1    S2    S3    S4
  H0     0     0     0     0     5  ← new cell
  H1     0     0     0     1     8
  H2     0     0     0     4     2
  H3     0     0     5     5     6
  H4     0     5     5     5     5
  H5     0     5     5     0     4  ← two new cells
  H6     5     5     5     5     5
```

57 codec-disagreement pairs (τ < 0.8) across 16 cells. Full tables: [`results/v4/`](results/v4/).

Sharpest disagreements:

| Cell | Pair | τ | Note |
|------|------|---|------|
| H6/S0 | zstd-1 vs zpaq-m5 | −1.000 | Near-incompressible: zpaq perfectly inverts zstd ordering |
| H6/S0 | zstd-19 vs zpaq-m5 | −1.000 | |
| H4/S1 | zstd-1 vs zpaq-m5 | −0.800 | Pure-literal marginal-structure: zpaq nearly inverts zstd |
| H5/S1 | zstd-1 vs zstd-19 | 0.000 | Even zstd levels completely disagree at 7 bpb / low structure |
| H4/S2 | zstd-1 vs bzip2-9 | 0.000 | Periodic+LZ mix: zstd vs BWT family total disagreement |
| H5/S2 | all pairs | 1.000 | LZ-structured 7-bpb: all codecs agree perfectly |

Three findings stand out:

**H6/S0**: zpaq-m5 exactly inverts both zstd levels (τ = −1.000). Context-mixing finds ordering signals in near-random bytes that LZ-family distance coding is completely blind to.

**H4/S1 and H5/S1** (pure literals, no copies): All five files in each cell have identical measured H and S — yet codecs rank them in completely different orders. The ranking divergence must arise from higher-order byte statistics (digram/trigram entropy) that zstd's FSE and zpaq's context mixing weight differently, invisible to marginal H and structural S alone.

**H5/S2** (lz77 M=0.3 at 7 bpb): Perfect agreement across all codec families. Once there is visible LZ structure even at near-maximum entropy, all families converge on the same ranking.

### Build pipeline

```sh
uv run scripts/gen-synthetic.py --calibrate-only   # calibration sweep (84 files)
uv run scripts/gen-balanced.py                     # balanced corpus (95 files, 5/cell)
uv run scripts/bench-v4.py --input build/raw/synthetic/balanced --out-dir build/bench/balanced
```

Or via Make:

```sh
make v4          # full pipeline: calibrate → balanced → benchmark
make v4-test     # run test suite
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
