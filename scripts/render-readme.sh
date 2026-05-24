#!/usr/bin/env bash
# Emit README.txt for the published Squishy corpus.
cat <<'EOF'
                              The Squishy Corpus
                              ──────────────────
                       https://jackdanger.com/squishy/

A corpus of real, weird, and intentionally broken files, built to help you
test compression and decompression libraries against the bytes that actually
show up in production — and the bytes you hope never to see.

ABOUT
─────
Squishy pulls together three things:

  1. The classic Silesia corpus (Sebastian Deorowicz, 2003) — twelve files
     that have been the standard reference for lossless compression
     benchmarks for two decades.
  2. A small "modern" set of files shaped like what production systems
     actually move around: minified JS, CSS, an HTML page, JSON, NDJSON,
     SQLite, parquet, protobuf wire data, log lines.
  3. A pathological set of synthetic inputs designed to land on specific
     decoder boundaries: zero/random/highly-repetitive bytes, sub-window
     sizes, and window-boundary triples (size, size ± 1) for the major
     codecs.

Plus negative-path fixtures: truncated frames, bit-flipped headers,
checksum mismatches, declared-length attacks, decompression bombs, and
shapes modeled on real-world decoder CVE classes (gzip BTYPE=11, zstd
FCS=UINT64_MAX, zip slip, zip64/32 EOCD mismatch, BCJ-filtered xz, ...).

Every artifact is reproducible from a single Makefile pinned to specific
tool versions (see versions.txt). The bytes are stable across rebuilds.

LAYOUT
──────
  index.txt              TSV manifest: sha256 / size / content-type / tier / path / description
  manifest.json          same data, JSON
  CHECKSUMS.sha256       GNU sha256sum format — `sha256sum -c CHECKSUMS.sha256`
  versions.txt           every tool version used to produce this build
  expected-ratio.json    known compressed sizes per (input, codec, level)
  index.html             a human-readable directory of everything
  listing.html           plain wget/grep-friendly link dump

  individual/<set>/<file>.<codec>[.l<level>]
                         each input compressed by each codec at multiple levels
  individual/<set>/<file>.zip.<internal>
                         zip containers with internal codec variants (store
                         is deliberately omitted — it's just a container)

  bundles/<set>/<set>.<ordering>.tar.<codec>
                         tar bundles, multiple orderings × every codec
  bundles/<set>/<set>.alpha.zip.<internal>
  bundles/<set>/<set>.<ordering>.7z.<method>
  bundles/<set>/<set>.<ordering>.squashfs.<comp>
  bundles/<set>/<set>.alpha.concat-<codec>
                         multi-member streams (no tar) — exercises decoder
                         restart-state across frame boundaries
  bundles/<set>/<set>.alpha.concat-zst-skipframes
                         zstd frames concatenated with skippable metadata
                         frames interleaved between them
  bundles/combined/everything.alpha.tar.<codec>
                         silesia + modern + pathological, combined

  dict/                  zstd dictionary fixtures: trained, applied,
                         and applied to non-matching content (worst case)

  negative/              ⚠ INTENTIONALLY MALFORMED. Do not auto-walk.
    truncated/           chopped at decoder-sensitive offsets
    bitflip/             single byte flipped at magic/header/body/checksum
    declared-length/     header lies about uncompressed size
    valid-empty/         minimal valid empty streams (positive sanity)
    concat-mixed/        valid frame followed by truncated frame
    zstd-skipframe-only/ only a skippable frame, no data
    cve-class/           shapes modeled on known decoder CVEs
    bomb/                small input, huge expansion — decompression bombs

REPRODUCIBILITY
───────────────
Bytes are stable for a given snapshot of tool versions. versions.txt
records every binary used; treat it as the cache key for any "immutable"
claim. We use:
  gzip -n -k         (no embedded name/timestamp)
  xz   -T1 -k        (single-thread; -T>1 produces nondeterministic block
                      boundaries)
  zstd -T1 -k        (same reason)
  brotli -k -q ...   (deterministic at all levels)
  tar  --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner
       --format=ustar
  zip  -X            (no extra fields = no uid/gid/mtime leaks)
  7z   -mtm=off -mtc=off -mta=off
  mksquashfs ... -no-exports -no-recovery  (with SOURCE_DATE_EPOCH=0)

TIERS
─────
Every artifact carries a `tier` field in the manifest:

  pr       small + critical, ~50 MiB. Use for per-commit CI.
  nightly  mid-size, ~500 MiB. Use for daily runs.
  full     everything, several GiB. Use for release validation.

Pull what you need:

  curl -s https://jackdanger.com/squishy/manifest.json | \
    jq -r '.artifacts[] | select(.tier=="pr") | .path' | \
    xargs -I{} curl -sO https://jackdanger.com/squishy/{}

LICENSING
─────────
  Silesia       Original distribution by Sebastian Deorowicz; the 12 files
                are re-hosted unchanged. Use under the same terms as the
                upstream distribution.
  jQuery        MIT.
  Bootstrap     MIT.
  EFF homepage  Snapshot of eff.org; included as a representative HTML
                sample.
  Synthetic     All locally generated from fixed PRNG seeds; public domain
                (CC0).
  Pathological  Same; public domain (CC0).

CAVEATS
───────
  • negative/ contains MALFORMED data. Walk it only with strict
    expansion / time / memory limits.
  • bomb/ entries can expand to many gigabytes. Apply a decompressed-size
    cap before feeding them to any decoder.
  • Storage is single-AZ (S3 ONEZONE_IA). The whole corpus is rederivable
    from the Makefile in the source repository; durability is intentionally
    traded for cost.

WHO
───
Built by Jack Danger (https://jackdanger.com) because I needed a corpus
that exercised both the typical and the awkward cases for a compression
library I was working on, and the existing corpora skipped the awkward
ones. If something is wrong, missing, or you have an input you wish
Squishy included, the source is in my blog repo — let me know.
EOF
