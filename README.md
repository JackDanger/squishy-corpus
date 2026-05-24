# Squishy

**A corpus of real, weird, and intentionally broken files for testing compression and decompression libraries.**

🌐 **Browse: [jackdanger.com/squishy](https://jackdanger.com/squishy/)**
📦 **Manifest: [manifest.json](https://jackdanger.com/squishy/manifest.json) · [index.txt (TSV)](https://jackdanger.com/squishy/index.txt) · [listing.html](https://jackdanger.com/squishy/listing.html)**
✅ **Checksums: [CHECKSUMS.sha256](https://jackdanger.com/squishy/CHECKSUMS.sha256)**

Squishy pulls together:

- the canonical [Silesia corpus](https://sun.aei.polsl.pl/~sdeor/index.php?page=silesia) (Sebastian Deorowicz, 2003),
- a small set of modern web and data files (JSON, NDJSON, SQLite, parquet, protobuf, syslog, jQuery, Bootstrap, HTML),
- deterministically-generated pathological inputs at every interesting decoder boundary (sub-window sizes; window-boundary triples for [zstd](https://github.com/facebook/zstd), [brotli](https://github.com/google/brotli), and [deflate](https://datatracker.ietf.org/doc/html/rfc1951); entropy extremes),
- and a museum of intentionally-malformed fixtures shaped like real-world decoder CVE classes ([CVE-2022-4899](https://nvd.nist.gov/vuln/detail/CVE-2022-4899), [CVE-2018-25032](https://nvd.nist.gov/vuln/detail/CVE-2018-25032), [CVE-2020-8927](https://nvd.nist.gov/vuln/detail/CVE-2020-8927), [Zip Slip](https://snyk.io/research/zip-slip-vulnerability), and friends).

Each input is compressed with every common codec at multiple levels and packaged in every common container, then published to S3 + CloudFront with `Cache-Control: immutable` so consumers can pin against stable URLs.

## Quick start

```sh
# Browse the manifest
curl -s https://jackdanger.com/squishy/manifest.json | jq

# Pull just the "pr" tier (~50 MiB) — small + critical, right for per-commit CI
curl -s https://jackdanger.com/squishy/manifest.json \
  | jq -r '.artifacts[] | select(.tier=="pr") | .path' \
  | xargs -I{} curl -sO https://jackdanger.com/squishy/{}

# Verify a download
curl -O https://jackdanger.com/squishy/individual/silesia/dickens.gz
curl -s https://jackdanger.com/squishy/CHECKSUMS.sha256 \
  | grep individual/silesia/dickens.gz | sha256sum -c
```

## Building locally

```sh
make doctor          # check toolchain (brew install gnu-tar brotli zpaq lzip lzop sevenzip squashfs on macOS)
make all             # full local build (sources → raw → individual → bundles → dict → negative → manifest)
make stream-publish  # build → upload → delete per artifact (peak local disk: ~2 GB)
```

## Design

- **No uncompressed bytes on S3.** The official "raw" delivery for each input is the gzip `-9` version at `individual/<set>/<file>.gz`. Decompress client-side for the original.
- **Reproducible.** Every encoder is invoked with flags that suppress nondeterminism (no embedded timestamps, single-threaded where parallelism matters). Tool versions are pinned in [versions.txt](https://jackdanger.com/squishy/versions.txt); treat that file as the cache key for any "immutable" claim.
- **Streaming publish.** `make stream-publish` builds, uploads, and deletes one artifact at a time. Peak local disk ≈ raw inputs (~1 GB) + one in-flight artifact (~1 GB). Designed so a CI runner with limited disk can still publish the full corpus.
- **Single-AZ storage.** Hosted on S3 ONEZONE_IA — single availability zone, ~half the cost of standard. The whole corpus is rederivable from this Makefile + pinned tool versions, so durability is intentionally traded for cost.

See the [website](https://jackdanger.com/squishy/) for the full design notes, codec details, bundle formats, and tier definitions.

## Layout

```
raw/silesia/, raw/modern/, raw/pathological/   # source inputs (kept locally; not published)
individual/<set>/<file>.<codec>[.l<level>]     # every input, every codec, multiple levels
individual/<set>/<file>.zip.{deflate,bzip2,lzma}
bundles/<set>/<set>.<ordering>.tar.<codec>     # per-set combined archives
bundles/<set>/<set>.{alpha,size-desc}.7z.<method>
bundles/<set>/<set>.{alpha,size-desc}.squashfs.<comp>
bundles/<set>/<set>.alpha.concat-{gz,xz,zst}   # multi-member streams (no tar)
bundles/<set>/<set>.alpha.concat-zst-skipframes
bundles/combined/everything.alpha.tar.<codec>  # silesia + modern + pathological
dict/                                          # zstd dictionary fixtures
negative/                                      # intentionally malformed — see warning
```

## Publishing

The published corpus at https://jackdanger.com/squishy/ is built and pushed from the maintainer's laptop:

```sh
make stream-publish   # build → upload → delete per artifact
make invalidate       # CloudFront invalidation for index files
```

To publish to your own bucket, override the configuration via environment or command-line — no need to edit the Makefile:

```sh
S3_BUCKET=mybucket S3_PREFIX=corpus CLOUDFRONT_DIST=EXXXXX make stream-publish
```

## Contributing

If something is wrong, missing, or you have an input you wish Squishy included, open an issue or a PR.

## License

[MIT](LICENSE) for the build system (Makefile, scripts, workflows). Bundled and generated content carries its own provenance (see `LICENSE` and the published `README.txt` for details).
