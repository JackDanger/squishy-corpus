#!/usr/bin/env python3
"""Download candidate files for the v4 corpus.

Downloads into build/raw/candidates/<source>/ with provenance logged.
Sources chosen to cover the H×M×L grid with public-domain / openly-licensed files.

Each source entry contains:
  url       — direct download URL
  dest      — path relative to build/raw/candidates/
  license   — SPDX identifier or short description
  note      — target cell region (informational)
  min_bytes — minimum acceptable size; skip if smaller (network/truncation guard)

Usage:
    uv run scripts/download-candidates.py
    uv run scripts/download-candidates.py --list      # list sources, no download
    uv run scripts/download-candidates.py --only nci  # download by source key
    uv run scripts/download-candidates.py --skip-existing  # don't re-download
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEST_BASE = ROOT / "build" / "raw" / "candidates"
PROVENANCE_FILE = DEST_BASE / "provenance.json"

# ---------------------------------------------------------------------------
# Source catalogue
# ---------------------------------------------------------------------------
# Each entry: key → {url, dest (relative), license, note, min_bytes}
#
# License abbreviations:
#   PD-US-gov  — US government work, public domain in the USA
#   PD-CC0     — Creative Commons CC0 (explicit public domain waiver)
#   PD-old     — pre-1928 work, public domain worldwide
#   CC-BY      — Creative Commons Attribution
#   CC-BY-SA   — Creative Commons Attribution-ShareAlike (copyleft)
#   ODbL       — Open Database License (share-alike for data)
#   GPL-2.0    — GNU GPL v2 (redistribution requires source)
#   MIT        — MIT License
# ---------------------------------------------------------------------------

SOURCES: dict[str, dict] = {
    # ── H ≈ 2–3 (low entropy) ───────────────────────────────────────────────
    "nci": {
        "url": "https://corpus.canterbury.ac.nz/descriptions/silesia/nci.bz2",
        "dest": "silesia-compat/nci.bz2",
        "decompress": "bz2",  # decompress after download
        "license": "Silesia (freely redistributable)",
        "note": "H≈2.4, M≈0.98, L=long — chemical compound SDF, already in silesia/ but included for candidate pipeline",
        "min_bytes": 10_000_000,
    },
    "gutenberg_dna": {
        "url": "http://pizzachili.dcc.uchile.cl/texts/dna/dna.50MB.gz",
        "dest": "pizza-chili/dna.50MB.gz",
        "decompress": "gz",
        "license": "Pizza&Chili (freely redistributable)",
        "note": "H≈2.0, M≈0.6, L=short — DNA 4-letter encoding",
        "min_bytes": 30_000_000,
    },

    # ── H ≈ 3–4.5 (medium entropy — natural language) ───────────────────────
    "canterbury_alice": {
        "url": "https://corpus.canterbury.ac.nz/resources/cantrbry.tar.gz",
        "dest": "canterbury/cantrbry.tar.gz",
        "license": "Canterbury (freely redistributable)",
        "note": "H≈4.5-5.1, M≈0.4-0.5 — classic Canterbury corpus (alice29, lcet10, plrabn12, etc.)",
        "min_bytes": 500_000,
    },
    "gutenberg_dickens": {
        "url": "https://www.gutenberg.org/cache/epub/1400/pg1400.txt",
        "dest": "gutenberg/dickens-great-expectations.txt",
        "license": "PD-old",
        "note": "H≈4.5, M≈0.5, L=medium — English novel",
        "min_bytes": 400_000,
    },
    "gutenberg_tolstoy": {
        "url": "https://www.gutenberg.org/cache/epub/2600/pg2600.txt",
        "dest": "gutenberg/tolstoy-war-and-peace.txt",
        "license": "PD-old",
        "note": "H≈4.7, M≈0.5, L=medium — English translation of Russian novel",
        "min_bytes": 2_000_000,
    },
    "gutenberg_austen": {
        "url": "https://www.gutenberg.org/cache/epub/1342/pg1342.txt",
        "dest": "gutenberg/austen-pride-and-prejudice.txt",
        "license": "PD-old",
        "note": "H≈4.4, M≈0.5, L=medium — English novel",
        "min_bytes": 600_000,
    },
    "gutenberg_bible": {
        "url": "https://www.gutenberg.org/cache/epub/10/pg10.txt",
        "dest": "gutenberg/king-james-bible.txt",
        "license": "PD-old",
        "note": "H≈4.4, M≈0.6, L=medium — highly repetitive religious text",
        "min_bytes": 3_000_000,
    },
    "enwik8": {
        "url": "https://mattmahoney.net/dc/enwik8.zip",
        "dest": "enwik/enwik8.zip",
        "license": "CC-BY-SA (Wikipedia)",
        "note": "H≈4.9, M≈0.85, L=long — 100 MB Wikipedia XML dump",
        "min_bytes": 30_000_000,
    },
    "pizza_sources": {
        "url": "http://pizzachili.dcc.uchile.cl/texts/code/sources.50MB.gz",
        "dest": "pizza-chili/sources.50MB.gz",
        "decompress": "gz",
        "license": "Pizza&Chili (freely redistributable)",
        "note": "H≈5.5, M≈0.6, L=medium — C source code",
        "min_bytes": 30_000_000,
    },

    # ── H ≈ 4.5–6 (code, structured binary) ─────────────────────────────────
    "pizza_english": {
        "url": "http://pizzachili.dcc.uchile.cl/texts/nlang/english.50MB.gz",
        "dest": "pizza-chili/english.50MB.gz",
        "decompress": "gz",
        "license": "Pizza&Chili (freely redistributable)",
        "note": "H≈4.6, M≈0.5, L=medium — English text",
        "min_bytes": 30_000_000,
    },
    "census_csv": {
        "url": "https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/state/totals/NST-EST2023-ALLDATA.csv",
        "dest": "us-gov/census-state-pop-2023.csv",
        "license": "PD-US-gov",
        "note": "H≈4-5, M≈0.4, L=short — US Census data CSV (small; part of larger set)",
        "min_bytes": 10_000,
    },

    # ── H ≈ 6–7.5 (high entropy — audio, images) ─────────────────────────────
    "canterbury_large": {
        # Large Canterbury corpus: E.coli genome, world192.txt (WT geography), bible.txt
        "url": "https://corpus.canterbury.ac.nz/resources/large.tar.gz",
        "dest": "canterbury/large.tar.gz",
        "license": "Canterbury (freely redistributable)",
        "note": "H≈2-5, M≈0.2-0.6 — large Canterbury corpus (bible.txt, E.coli 4-letter DNA, world192.txt)",
        "min_bytes": 10_000_000,
    },

    # ── Silesia compatibility (already locally available) ────────────────────
    "silesia_x_ray": {
        "url": "https://sun.aei.polsl.pl//~sdeor/corpus/silesia.zip",
        "dest": "silesia-orig/silesia.zip",
        "license": "Silesia (freely redistributable)",
        "note": "Full Silesia corpus ZIP — use only if build/raw/silesia/ is missing",
        "min_bytes": 200_000_000,
    },
}

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

_CHUNK = 1 << 20  # 1 MB read chunks


def _sha256_of_path(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, skip_existing: bool = False) -> bool:
    """Download url → dest. Returns True if downloaded, False if skipped."""
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.name}")
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    print(f"    ↓ {url}")
    print(f"      → {dest.relative_to(ROOT)}", flush=True)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "squishy-corpus/2.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            t0 = time.monotonic()
            with open(tmp, "wb") as f:
                while chunk := resp.read(_CHUNK):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        elapsed = time.monotonic() - t0
                        rate = downloaded / elapsed / 1e6 if elapsed > 0.1 else 0
                        print(f"\r      {pct:3d}%  {downloaded/1e6:.1f}/{total/1e6:.1f} MB"
                              f"  {rate:.1f} MB/s", end="", flush=True)
        print()  # newline after progress
        tmp.rename(dest)
        return True
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        print(f"\n    ERROR: {e}", file=sys.stderr)
        return False


def _check_min_bytes(path: Path, min_bytes: int) -> bool:
    size = path.stat().st_size
    # .gz files are typically 10–50% of decompressed size; use a 10× slack factor
    # to avoid false WARN on compressed candidates.
    effective = size * (10 if path.suffix == ".gz" else 1)
    if effective < min_bytes:
        print(f"    WARN: {path.name} is {size} bytes (effective {effective}), "
              f"expected ≥ {min_bytes:,}")
        return False
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true",
                        help="List all sources and exit")
    parser.add_argument("--only", nargs="+", metavar="KEY",
                        help="Download only these source keys")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="Skip files that already exist (default: True)")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if file already exists")
    args = parser.parse_args()

    skip_existing = args.skip_existing and not args.force

    if args.list:
        print(f"{'Key':30s}  {'License':30s}  Note")
        print("-" * 100)
        for key, src in SOURCES.items():
            print(f"{key:30s}  {src['license']:30s}  {src['note']}")
        return

    keys = args.only or list(SOURCES.keys())
    unknown = [k for k in keys if k not in SOURCES]
    if unknown:
        print(f"Unknown keys: {unknown}", file=sys.stderr)
        sys.exit(1)

    DEST_BASE.mkdir(parents=True, exist_ok=True)
    provenance: dict = {}
    if PROVENANCE_FILE.exists():
        with open(PROVENANCE_FILE) as f:
            provenance = json.load(f)

    downloaded = 0
    skipped = 0
    failed = 0

    for key in keys:
        src = SOURCES[key]
        dest = DEST_BASE / src["dest"]
        print(f"\n[{key}]  {src['note']}")

        ok = _download(src["url"], dest, skip_existing=skip_existing)
        if not ok:
            if dest.exists():
                skipped += 1
            else:
                failed += 1
            continue

        if "min_bytes" in src and not _check_min_bytes(dest, src["min_bytes"]):
            failed += 1
            continue

        sha = _sha256_of_path(dest)
        provenance[key] = {
            "url": src["url"],
            "dest": src["dest"],
            "license": src["license"],
            "sha256": sha,
            "size_bytes": dest.stat().st_size,
        }
        downloaded += 1

    # Save provenance
    if provenance:
        with open(PROVENANCE_FILE, "w") as f:
            json.dump(provenance, f, indent=2)
        print(f"\nProvenance written → {PROVENANCE_FILE.relative_to(ROOT)}")

    print(f"\nDone: {downloaded} downloaded, {skipped} skipped, {failed} failed")

    if downloaded + skipped > 0:
        print(f"\nNext step:")
        print(f"  uv run scripts/measure-corpus.py --dirs build/raw/candidates \\")
        print(f"    --out build/bench/corpus-measurements.csv")


if __name__ == "__main__":
    main()
