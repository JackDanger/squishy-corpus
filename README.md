# Squishy Corpus

Two things live here:

1. **Calibrated corpus v4** — a measurement instrument for compression research: synthetic files with independently controlled entropy (H) and match density (M).
2. **Squishy fixture set** — real, weird, and intentionally broken files for testing compression/decompression libraries.

---

## Calibrated corpus v4

Files generated with two axes under independent control:

| Axis | Values | Plain English |
|------|--------|---------------|
| **H** — marginal byte entropy | 1.0, 1.7, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0 bpb | How unpredictable are the bytes? |
| **M** — LZ match density | 0.00, 0.10, 0.25, 0.50, 0.75 | What fraction came from copy-paste? |
| **L̄** — mean match length | 3, 8, 32, 128 bytes | How long are the copies? (M=0.50 sweep only) |

Natural benchmarks (Silesia, enwik8, Calgary) confound H and M — you cannot vary match density while holding entropy fixed in a real text file. This corpus can, which isolates codec-family differences that are invisible on natural files.

**Validation:** Kendall-τ = 0.939 between zstd-3 and bzip2-9 across 33 unclamped cells (threshold ≥ 0.9). 40/40 cells have a consistent per-cell winner across all 3 replicates.

**Full documentation:** [`build/bundle/index.html`](build/bundle/index.html) (generated) — covers the glossary, scoring workflow, all manifest fields, limitations, and ground-truth schema.

### Quick start

```sh
# Score a codec against the calibrated bundle
cd build/bundle
python score.py list --manifest manifest.csv --bundle calibrated | \
  xargs -I{} zstd -3 calibrated/{} -c -q -o results/zstd-3/{}
python score.py score --manifest manifest.csv --results results/zstd-3 --codec zstd-3

# Compare two codecs, get Kendall-τ
python score.py compare --manifest manifest.csv \
    --results-a results/zstd-3 --results-b results/bzip2-9 \
    --codec-a zstd-3 --codec-b bzip2-9
```

### Build the bundle

```sh
make calibrated-bundle    # generate + bench + curate + assemble build/bundle/
make calibrated-html      # rebuild index.html from scripts/bundle-index.html
make calibrated-publish   # sync to S3
```

Edit website copy: `scripts/bundle-index.html` (plain HTML with `$var` placeholders).
Run `make calibrated-html` to rebuild `build/bundle/index.html`.

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

# Verify a download
curl -O https://jackdanger.com/squishy/individual/silesia/dickens.gz
curl -s https://jackdanger.com/squishy/CHECKSUMS.sha256 \
  | grep individual/silesia/dickens.gz | sha256sum -c
```

### Build locally

```sh
make doctor          # check toolchain
make all             # full local build
make stream-publish  # build → upload → delete (peak disk ~2 GB)
```

### Layout

```
build/raw/silesia/, build/raw/modern/, build/raw/pathological/
individual/<set>/<file>.<codec>[.l<level>]
bundles/<set>/<set>.<ordering>.tar.<codec>
bundles/<set>/<set>.{alpha,size-desc}.7z.<method>
bundles/<set>/<set>.{alpha,size-desc}.squashfs.<comp>
bundles/<set>/<set>.alpha.concat-{gz,xz,zst}
bundles/combined/everything.alpha.tar.<codec>
dict/          # zstd dictionary fixtures
negative/      # intentionally malformed fixtures
build/bundle/  # calibrated corpus v4 bundle
```

### Design notes

- **No uncompressed bytes on S3.** Raw inputs are delivered as `.gz -9`; decompress client-side.
- **Reproducible.** Every encoder is invoked with flags that suppress nondeterminism. Tool versions pinned in [versions.txt](https://jackdanger.com/squishy/versions.txt).
- **Single-AZ storage.** S3 ONEZONE_IA — the corpus is rederivable from the Makefile, so durability trades for cost.

---

## License

[MIT](LICENSE) for the build system (Makefile, scripts). Bundled and generated content carries its own provenance — see `LICENSE` and the published `README.txt`.
