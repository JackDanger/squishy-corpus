#!/usr/bin/env python3
"""Emit a plan.tsv enumerating every artifact stream-publish.sh should build
and ship, WITHOUT requiring those artifacts to already exist on disk. Used to
drive a streaming build→upload→delete pipeline that never materialises the
whole corpus locally.

Output format (TSV, sorted by predicted size DESCENDING — biggest first):
  <local_path>\t<s3_key>\t<content_type>\t<cache_control>

The local paths match the targets produced by the Makefile rules.
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path

# ─── Configuration (mirrors the Makefile's lists) ─────────────────────────
SILESIA_NAMES = ["dickens", "mozilla", "mr", "nci", "ooffice", "osdb",
                 "reymont", "samba", "sao", "webster", "x-ray", "xml"]
MODERN_FETCH  = ["jquery-2.1.4.min.js", "bootstrap-3.3.6.min.css", "eff.html",
                 "react-18.3.1.min.js"]
MODERN_GEN    = ["sample.json", "sample.ndjson", "sample.sqlite",
                 "sample.parquet", "sample.protobuf", "sample.log", "random-1M",
                 "sample.csv", "sample.arrow", "sample.wasm", "sample.msgpack",
                 "inter-regular.woff2"]
MODERN_NAMES  = MODERN_FETCH + MODERN_GEN
PATHO_TINY    = ["empty-0B","one-1B","tiny-13B","small-256B","page-4095B","short-65535B"]
PATHO_ENTROPY = ["zeros-1M","zeros-10M","zeros-100M","urandom-1M","urandom-10M","urandom-100M",
                 "repeat-A-1M","alternating-1M","ascii-1M","onebyte-per-page-1M",
                 "phrase-repeated-10M","pi-digits-10M","sparse-geometric-10M","already-compressed-blob"]
PATHO_WINDOW  = ["window-zstd-128M-minus1","window-zstd-128M","window-zstd-128M-plus1",
                 "window-brotli-16M-minus1","window-brotli-16M","window-brotli-16M-plus1",
                 "window-deflate-32K-minus1","window-deflate-32K","window-deflate-32K-plus1",
                 "window-zstd-8M-minus1","window-zstd-8M","window-zstd-8M-plus1",
                 "lz4-block-64K-minus1","lz4-block-64K","lz4-block-64K-plus1"]
PATHO_MATCH   = ["max-match-257B","max-match-258B","max-match-259B"]
PATHO_MISC    = ["mixed-entropy-blocks-2M","thue-morse-10M","debruijn-order3",
                 "near-dup-base","near-dup-variant"]
PATHO_NAMES   = PATHO_TINY + PATHO_ENTROPY + PATHO_WINDOW + PATHO_MATCH + PATHO_MISC

# Canterbury corpus fallback filenames (used when build/raw/squash/ does not exist)
SQUASH_FALLBACK = [
    "alice29.txt", "asyoulik.txt", "cp.html", "fields.c", "grammar.lsp",
    "kennedy.xls", "lcet10.txt", "plrabn12.txt", "ptt5", "sum", "xargs.1",
]

SETS = {
    "silesia":      SILESIA_NAMES,
    "modern":       MODERN_NAMES,
    "pathological": PATHO_NAMES,
    # squash is resolved dynamically in build_plan() via SQUASH_FALLBACK / disk scan
    "squash":       None,
}

CODECS_DEFAULT = ["gz", "bz2", "xz", "zst", "lz4", "br", "lzma", "lz", "lzo", "zpaq"]
LEVELS = {
    "gz":  ["1", "6", "9"],
    "xz":  ["0", "6", "9"],
    "zst": ["1", "3", "9", "19", "22"],
    "br":  ["1", "6", "11"],
}
ZIP_INTERNALS = ["store", "deflate", "bzip2", "lzma"]
ORDERINGS       = ["alpha", "random"]
SOLID_ORDERINGS = ["alpha", "size-desc"]
TAR_CODECS      = ["gz", "bz2", "xz", "zst", "lz4", "br", "lzma"]
SQUASHFS_CODECS = ["gzip", "xz", "lz4", "zstd"]
SEVENZ_CODECS   = ["lzma2", "ppmd", "bzip2", "deflate"]
CONCAT_CODECS   = ["gz", "xz", "zst"]

CONTENT_TYPES = {
    ".gz": "application/gzip", ".bz2": "application/x-bzip2",
    ".xz": "application/x-xz", ".zst": "application/zstd",
    ".lz4": "application/x-lz4", ".br": "application/x-brotli",
    ".lzma": "application/x-lzma", ".lz": "application/x-lzip",
    ".lzo": "application/x-lzop", ".zpaq": "application/x-zpaq",
    ".7z": "application/x-7z-compressed", ".zip": "application/zip",
    ".tar": "application/x-tar", ".cpio": "application/x-cpio",
    ".squashfs": "application/x-squashfs", ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8", ".json": "application/json",
    ".sha256": "text/plain; charset=utf-8", ".zdict": "application/octet-stream",
    ".bin": "application/octet-stream", ".pax": "application/x-tar",
    ".ar": "application/x-archive",
}

CC_IMMUTABLE = "public, max-age=31536000, immutable"
CC_INDEX     = "public, max-age=300, must-revalidate"

def content_type_for(name: str) -> str:
    parts = name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        ext = "." + parts[i]
        if ext in CONTENT_TYPES:
            return CONTENT_TYPES[ext]
    return "application/octet-stream"

def is_uncompressed_publish_target(path: str) -> bool:
    """Policy: never publish uncompressed bytes. Compressed equivalents exist
    (.tar.gz, .zip with deflate, etc). Locally these may exist as build
    intermediates (e.g. .tar feeds .tar.{codec}), but they don't get shipped."""
    if path.startswith("raw/") or "/raw/" in path:        return True
    # bare tar
    if path.endswith(".tar"):                              return True
    # uncompressed containers
    if path.endswith((".cpio", ".pax", ".ar")):           return True
    # zip with no internal compression
    if path.endswith(".zip.store"):                       return True
    return False

# Rough size estimates so we sort biggest-first.
# Real sizes will vary; this just orders the plan sensibly.
def estimate_size(local_path: str) -> int:
    if "/pathological/" in local_path:
        if ".tar.zst" in local_path or ".tar.gz" in local_path: return 600_000_000
        if ".tar" in local_path: return 700_000_000
        if ".7z" in local_path or ".squashfs" in local_path: return 550_000_000
        if ".zip" in local_path: return 600_000_000
        if "window-zstd-128M" in local_path: return 140_000_000
        if "-100M" in local_path: return 100_000_000
        if "-10M" in local_path: return 10_000_000
        if "-1M" in local_path: return 1_000_000
    if "/silesia/" in local_path:
        if ".tar" in local_path or ".7z" in local_path or ".squashfs" in local_path: return 80_000_000
        return 5_000_000
    return 100_000  # default small

def emit(plan: list[tuple[str, str, str, str]], local: str, s3_key: str, ct: str | None = None, cc: str = CC_IMMUTABLE):
    if is_uncompressed_publish_target(s3_key):
        return
    plan.append((local, s3_key, ct or content_type_for(Path(local).name), cc))

def build_plan(build_dir: str, prefix: str) -> list[tuple[str, str, str, str]]:
    plan: list[tuple[str, str, str, str]] = []

    # Resolve squash file list dynamically
    squash_raw = Path(build_dir) / "raw" / "squash"
    if squash_raw.exists():
        squash_files = sorted(f.name for f in squash_raw.iterdir() if f.is_file())
    else:
        squash_files = SQUASH_FALLBACK

    # Build resolved sets dict (replacing None placeholder for squash)
    resolved_sets: dict[str, list[str]] = {
        k: (squash_files if v is None else v) for k, v in SETS.items()
    }

    # ─── individual: per-codec per-file ───────────────────────────────
    LEVELED_SETS = {"silesia", "modern", "squash"}  # pathological has no per-level variants
    for set_name, files in resolved_sets.items():
        for f in files:
            for codec in CODECS_DEFAULT:
                local = f"{build_dir}/individual/{set_name}/{f}.{codec}"
                emit(plan, local, f"{prefix}/individual/{set_name}/{f}.{codec}")
            # per-level variants (silesia + modern only)
            if set_name in LEVELED_SETS:
                for codec, levels in LEVELS.items():
                    for lvl in levels:
                        local = f"{build_dir}/individual/{set_name}/{f}.{codec}.l{lvl}"
                        emit(plan, local, f"{prefix}/individual/{set_name}/{f}.{codec}.l{lvl}")
            # 7z + zip + zip variants
            emit(plan, f"{build_dir}/individual/{set_name}/{f}.7z",  f"{prefix}/individual/{set_name}/{f}.7z")
            emit(plan, f"{build_dir}/individual/{set_name}/{f}.zip", f"{prefix}/individual/{set_name}/{f}.zip")
            for v in ZIP_INTERNALS:
                emit(plan, f"{build_dir}/individual/{set_name}/{f}.zip.{v}",
                            f"{prefix}/individual/{set_name}/{f}.zip.{v}")

    # ─── per-set bundles ──────────────────────────────────────────────
    for set_name in resolved_sets:
        for ordering in ORDERINGS:
            emit(plan, f"{build_dir}/bundles/{set_name}/{set_name}.{ordering}.tar",
                        f"{prefix}/bundles/{set_name}/{set_name}.{ordering}.tar")
            for codec in TAR_CODECS:
                emit(plan, f"{build_dir}/bundles/{set_name}/{set_name}.{ordering}.tar.{codec}",
                            f"{prefix}/bundles/{set_name}/{set_name}.{ordering}.tar.{codec}")
        for v in ZIP_INTERNALS:
            emit(plan, f"{build_dir}/bundles/{set_name}/{set_name}.alpha.zip.{v}",
                        f"{prefix}/bundles/{set_name}/{set_name}.alpha.zip.{v}")
        for ordering in SOLID_ORDERINGS:
            for m in SEVENZ_CODECS:
                emit(plan, f"{build_dir}/bundles/{set_name}/{set_name}.{ordering}.7z.{m}",
                            f"{prefix}/bundles/{set_name}/{set_name}.{ordering}.7z.{m}")
            for c in SQUASHFS_CODECS:
                emit(plan, f"{build_dir}/bundles/{set_name}/{set_name}.{ordering}.squashfs.{c}",
                            f"{prefix}/bundles/{set_name}/{set_name}.{ordering}.squashfs.{c}")
        for k in ["cpio", "pax", "ar"]:
            emit(plan, f"{build_dir}/bundles/{set_name}/{set_name}.alpha.{k}",
                        f"{prefix}/bundles/{set_name}/{set_name}.alpha.{k}")
        for codec in CONCAT_CODECS:
            emit(plan, f"{build_dir}/bundles/{set_name}/{set_name}.alpha.concat-{codec}",
                        f"{prefix}/bundles/{set_name}/{set_name}.alpha.concat-{codec}")
        emit(plan, f"{build_dir}/bundles/{set_name}/{set_name}.alpha.concat-zst-skipframes",
                    f"{prefix}/bundles/{set_name}/{set_name}.alpha.concat-zst-skipframes")

    # combined/everything bundle retired — per-set bundles are the unit of caching

    # ─── mixed-member + dict ──────────────────────────────────────────
    emit(plan, f"{build_dir}/bundles/mixed-member/silesia-mixed.bin",
                f"{prefix}/bundles/mixed-member/silesia-mixed.bin")
    for d in ["json-samples.zdict", "json-samples.tar.zst",
              "json-samples.no-dict.tar.zst", "wrong-dict-silesia-dickens.zst"]:
        emit(plan, f"{build_dir}/dict/{d}", f"{prefix}/dict/{d}")

    # ─── negative + meta come at the end (small) ──────────────────────
    # We can't enumerate negative fixtures without running gen-negative.py,
    # so the negative + meta sweep is a separate step (see Makefile).

    # Sort by predicted size DESCENDING
    plan.sort(key=lambda r: -estimate_size(r[0]))
    return plan

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build",  default="build")
    ap.add_argument("--prefix", default="squishy")
    args = ap.parse_args()
    plan = build_plan(args.build, args.prefix)
    for local, s3, ct, cc in plan:
        print(f"{local}\t{s3}\t{ct}\t{cc}")
    print(f"# {len(plan)} entries", file=sys.stderr)

if __name__ == "__main__":
    main()
