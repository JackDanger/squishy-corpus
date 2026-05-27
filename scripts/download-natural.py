#!/usr/bin/env python3
"""Download and prepare natural corpus files for calibrated-corpus cell validation.

Each dataset targets a specific H×M cell that was previously covered only by
synthetic files. Having real-world data in the same cell lets us validate the
synthetic generator and expose blind spots (e.g., periodic byte-column structure,
tandem-repeat biology, temporal video correlation).

Files produced in build/raw/natural/<dataset>/
  mnist/               H<0.5, M>0.80   — MNIST pixel bytes (sparse zeros)
  gharchive/           H≈4.0-4.5, M≈0.60-0.80  — GitHub event JSON
  genomic/             H≈1.0-1.4, M>0.90  — human chrY alpha-satellite
  video/               H≈6.5-7.5, M≈0.40-0.70  — Xiph CIF video luma plane
  neural/              H≈7.7-7.9, M≈0.20-0.35  — BERT float32 weight bytes

After running this script, measure with:
    uv run scripts/measure-corpus.py \\
        --dirs build/raw/natural \\
        --out build/bench/natural-measurements.csv

Usage:
    uv run scripts/download-natural.py
    uv run scripts/download-natural.py --only mnist gharchive
    uv run scripts/download-natural.py --list
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import struct
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEST_BASE = ROOT / "build" / "raw" / "natural"

_CHUNK = 1 << 20  # 1 MiB read chunks
_4M = 4 * 1024 * 1024
_256K = 256 * 1024


# ---------------------------------------------------------------------------
# Source catalogue
# ---------------------------------------------------------------------------
# Each entry: key → metadata dict. Extraction logic lives in _extract_<key>().
# ---------------------------------------------------------------------------
SOURCES: dict[str, dict] = {
    "mnist": {
        "url": "https://storage.googleapis.com/cvdf-datasets/mnist/train-images-idx3-ubyte.gz",
        "compressed": "mnist/train-images.gz",
        "license": "CC-BY-SA (Yann LeCun, Corinna Cortes, Christopher Burges)",
        "note": "H<0.5, M>0.80 — 60k training image pixels, sparse zeros with digit-shape structure",
        "min_bytes_compressed": 9_000_000,
    },
    "gharchive": {
        "url": "https://data.gharchive.org/2024-01-01-12.json.gz",
        "compressed": "gharchive/2024-01-01-12.json.gz",
        "license": "CC-BY (GitHub Archive, github.com/igrigorik/gharchive.org)",
        "note": "H≈4.0-4.5, M≈0.60-0.80 — GitHub event NDJSON with repeated keys and URL prefixes",
        "min_bytes_compressed": 5_000_000,
    },
    "genomic": {
        # NCBI efetch: human chromosome Y alpha-satellite region, FASTA format
        # NC_000024.10 = GRCh38 chrY; coords 10M–14.2M cover the alpha-satellite array
        "url": (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=nuccore&id=NC_000024.10&seq_start=10000001&seq_stop=14200000"
            "&rettype=fasta&retmode=text"
        ),
        "compressed": None,  # not compressed; direct FASTA text
        "raw": "genomic/grch38-chry-alphasat.fasta",
        "license": "PD-US-gov (NCBI GRCh38 human reference genome)",
        "note": "H≈1.0-1.4, M>0.90 — 171 bp alpha-satellite tandem repeats, 4-symbol alphabet",
        "min_bytes_compressed": 3_000_000,
    },
    "video": {
        "url": "https://media.xiph.org/video/derf/y4m/foreman_cif.y4m",
        "compressed": None,
        "raw": "video/foreman_cif.y4m",
        "license": "xiph.org test media (freely redistributable)",
        "note": "H≈6.5-7.5, M≈0.40-0.70 — CIF 352×288 video, luma plane only extracted",
        "min_bytes_compressed": 40_000_000,
    },
    "bert": {
        # safetensors: [8B header_size][header_size JSON][raw tensors]
        # Range request: first 8 MiB covers header + start of weight data
        "url": "https://huggingface.co/bert-base-uncased/resolve/main/model.safetensors",
        "compressed": None,
        "raw": "neural/bert-safetensors-head8M.bin",
        "range": (0, 8 * 1024 * 1024 - 1),
        "license": "Apache-2.0 (Google BERT, huggingface.co/bert-base-uncased)",
        "note": "H≈7.7-7.9, M≈0.20-0.35 — float32 weights; period-4 exponent-byte clustering",
        "min_bytes_compressed": 7_000_000,
    },
}


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path, byte_range: tuple[int, int] | None = None,
              skip_existing: bool = True) -> bool:
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.relative_to(ROOT)}")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    headers: dict[str, str] = {"User-Agent": "squishy-corpus/2.0"}
    if byte_range is not None:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"

    print(f"    ↓ {url[:80]}{'…' if len(url) > 80 else ''}")
    if byte_range:
        print(f"      Range: bytes={byte_range[0]}-{byte_range[1]}")
    print(f"      → {dest.relative_to(ROOT)}", flush=True)

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
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
        print()
        tmp.rename(dest)
        actual = dest.stat().st_size
        print(f"      {actual/1e6:.2f} MB written")
        return True
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        print(f"\n    ERROR downloading {url[:60]}: {e}", file=sys.stderr)
        return False


def _decompress_gz(src: Path, dest: Path, skip_existing: bool = True) -> bool:
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.name}")
        return True
    print(f"    decompress → {dest.name}", flush=True)
    try:
        with gzip.open(src, "rb") as f_in, open(dest, "wb") as f_out:
            written = 0
            while chunk := f_in.read(_CHUNK):
                f_out.write(chunk)
                written += len(chunk)
                print(f"\r      {written/1e6:.1f} MB", end="", flush=True)
        print(f"\r      {written/1e6:.2f} MB decompressed   ")
        return True
    except Exception as e:
        print(f"\n    ERROR decompressing {src.name}: {e}", file=sys.stderr)
        if dest.exists():
            dest.unlink()
        return False


def _write_slice(src: Path, dest: Path, start: int, length: int,
                 skip_existing: bool = True) -> bool:
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.name}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(src, "rb") as f:
            f.seek(start)
            data = f.read(length)
        dest.write_bytes(data)
        print(f"    slice [{start}:{start+length}] → {dest.name}  ({len(data)/1024:.0f} KiB)")
        return True
    except Exception as e:
        print(f"    ERROR slicing: {e}", file=sys.stderr)
        return False


def _fasta_to_nt(src: Path, dest: Path, skip_existing: bool = True) -> bool:
    """Strip FASTA header lines; write concatenated nucleotide bytes."""
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.name}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        written = 0
        with open(src, "rb") as f_in, open(dest, "wb") as f_out:
            for line in f_in:
                stripped = line.strip()
                if stripped.startswith(b">") or stripped.startswith(b";"):
                    continue
                f_out.write(stripped.upper())
                written += len(stripped)
        print(f"    fasta_strip → {dest.name}  ({written/1e6:.2f} MB nucleotide bytes)")
        return True
    except Exception as e:
        print(f"    ERROR stripping FASTA: {e}", file=sys.stderr)
        return False


def _y4m_luma(src: Path, dest_prefix: Path, skip_existing: bool = True) -> bool:
    """Extract concatenated Y-plane bytes from a YUV4MPEG2 (.y4m) file.

    Produces:
      <dest_prefix>-luma-4M.bin   (4 MiB of luma)
      <dest_prefix>-luma-256K.bin (256 KiB of luma)
    """
    dest_4m = Path(str(dest_prefix) + "-luma-4M.bin")
    dest_256k = Path(str(dest_prefix) + "-luma-256K.bin")
    if dest_4m.exists() and dest_256k.exists() and skip_existing:
        print(f"    skip (exists): {dest_4m.name}, {dest_256k.name}")
        return True
    dest_4m.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(src, "rb") as f:
            # Read global header line
            header_line = f.readline().decode("ascii", errors="replace")
            if not header_line.startswith("YUV4MPEG2"):
                print(f"    ERROR: not a Y4M file (header: {header_line[:40]!r})", file=sys.stderr)
                return False

            # Parse width and height
            w = h = 0
            for token in header_line.split():
                if token.startswith("W"):
                    w = int(token[1:])
                elif token.startswith("H"):
                    h = int(token[1:])
            if w == 0 or h == 0:
                print(f"    ERROR: couldn't parse W/H from Y4M header: {header_line!r}", file=sys.stderr)
                return False

            y_size = w * h
            # Assume 4:2:0 chroma; each chroma plane is (w/2)*(h/2)
            uv_size = (w // 2) * (h // 2) * 2
            frame_header = b"FRAME"

            luma_buf = bytearray()
            frames_read = 0
            while len(luma_buf) < _4M:
                tag = f.read(len(frame_header))
                if len(tag) < len(frame_header):
                    break
                if tag != frame_header:
                    print(f"    ERROR: expected FRAME tag, got {tag!r}", file=sys.stderr)
                    return False
                rest_of_line = f.readline()  # consume "\n" and any extra params
                y_data = f.read(y_size)
                if len(y_data) < y_size:
                    break
                luma_buf.extend(y_data)
                f.read(uv_size)  # skip chroma
                frames_read += 1

        print(f"    y4m_luma: {frames_read} frames, {w}×{h}, {len(luma_buf)/1e6:.2f} MB luma")
        luma = bytes(luma_buf)
        dest_4m.write_bytes(luma[:_4M])
        print(f"    → {dest_4m.name}  ({_4M/1024:.0f} KiB)")
        dest_256k.write_bytes(luma[:_256K])
        print(f"    → {dest_256k.name}  ({_256K/1024:.0f} KiB)")
        return True
    except Exception as e:
        print(f"    ERROR extracting Y4M luma: {e}", file=sys.stderr)
        return False


def _safetensors_weights(src: Path, dest_prefix: Path, skip_existing: bool = True) -> bool:
    """Skip the safetensors JSON header and extract raw float32 weight bytes.

    Produces:
      <dest_prefix>-float32-4M.bin
      <dest_prefix>-float32-256K.bin
    """
    dest_4m = Path(str(dest_prefix) + "-float32-4M.bin")
    dest_256k = Path(str(dest_prefix) + "-float32-256K.bin")
    if dest_4m.exists() and dest_256k.exists() and skip_existing:
        print(f"    skip (exists): {dest_4m.name}, {dest_256k.name}")
        return True
    dest_4m.parent.mkdir(parents=True, exist_ok=True)

    try:
        data = src.read_bytes()
        if len(data) < 8:
            print(f"    ERROR: file too small ({len(data)} bytes)", file=sys.stderr)
            return False

        header_size = struct.unpack_from("<Q", data, 0)[0]
        weight_start = 8 + header_size
        if weight_start >= len(data):
            print(f"    ERROR: weight_start ({weight_start}) >= file size ({len(data)})", file=sys.stderr)
            return False

        weights = data[weight_start:]
        print(f"    safetensors: header_size={header_size}, "
              f"weights available: {len(weights)/1e6:.2f} MB")

        dest_4m.write_bytes(weights[:_4M])
        print(f"    → {dest_4m.name}  ({min(len(weights), _4M)/1024:.0f} KiB)")
        dest_256k.write_bytes(weights[:_256K])
        print(f"    → {dest_256k.name}  ({min(len(weights), _256K)/1024:.0f} KiB)")
        return True
    except Exception as e:
        print(f"    ERROR extracting safetensors weights: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Per-source extraction pipelines
# ---------------------------------------------------------------------------

def _process_mnist(d: Path, skip_existing: bool) -> bool:
    gz = d / "train-images.gz"
    raw = d / "train-images-idx3-ubyte"
    ok = _decompress_gz(gz, raw, skip_existing)
    if not ok:
        return False
    # Skip 16-byte IDX3 header (magic, #images, rows, cols — each 4 bytes big-endian)
    ok = (_write_slice(raw, d / "mnist-train-4M.bin",  start=16, length=_4M,   skip_existing=skip_existing)
       and _write_slice(raw, d / "mnist-train-256K.bin", start=16, length=_256K, skip_existing=skip_existing))
    # Delete large intermediate to save disk
    if ok and raw.exists():
        raw.unlink()
        print(f"    removed intermediate {raw.name}")
    return ok


def _process_gharchive(d: Path, skip_existing: bool) -> bool:
    gz  = d / "2024-01-01-12.json.gz"
    raw = d / "2024-01-01-12.json"
    ok = _decompress_gz(gz, raw, skip_existing)
    if not ok:
        return False
    ok = (_write_slice(raw, d / "gharchive-4M.bin",  start=0, length=_4M,   skip_existing=skip_existing)
       and _write_slice(raw, d / "gharchive-256K.bin", start=0, length=_256K, skip_existing=skip_existing))
    if ok and raw.exists():
        raw.unlink()
        print(f"    removed intermediate {raw.name}")
    return ok


def _process_genomic(d: Path, skip_existing: bool) -> bool:
    fasta = d / "grch38-chry-alphasat.fasta"
    nt    = d / "grch38-chry-alphasat.nt"
    ok = _fasta_to_nt(fasta, nt, skip_existing)
    if not ok:
        return False
    size = nt.stat().st_size
    print(f"    nucleotide file: {size/1e6:.2f} MB")
    if size >= _4M:
        ok = _write_slice(nt, d / "grch38-chry-alphasat-4M.bin",  0, _4M,   skip_existing)
    if size >= _256K:
        ok = _write_slice(nt, d / "grch38-chry-alphasat-256K.bin", 0, _256K, skip_existing)
    return ok


def _process_video(d: Path, skip_existing: bool) -> bool:
    y4m = d / "foreman_cif.y4m"
    return _y4m_luma(y4m, d / "foreman-cif", skip_existing)


def _process_bert(d: Path, skip_existing: bool) -> bool:
    head = d / "bert-safetensors-head8M.bin"
    return _safetensors_weights(head, d / "bert-base", skip_existing)


PROCESSORS = {
    "mnist":     _process_mnist,
    "gharchive": _process_gharchive,
    "genomic":   _process_genomic,
    "video":     _process_video,
    "bert":      _process_bert,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="List sources and exit")
    parser.add_argument("--only", nargs="+", metavar="KEY",
                        help="Download only these source keys")
    parser.add_argument("--force", action="store_true",
                        help="Re-download and re-extract even if files exist")
    args = parser.parse_args()

    if args.list:
        print(f"{'Key':12s}  {'License':45s}  Note")
        print("-" * 110)
        for key, src in SOURCES.items():
            print(f"{key:12s}  {src['license']:45s}  {src['note']}")
        return

    keys = args.only or list(SOURCES.keys())
    unknown = [k for k in keys if k not in SOURCES]
    if unknown:
        print(f"Unknown keys: {unknown}", file=sys.stderr)
        sys.exit(1)

    skip = not args.force
    ok_count = fail_count = 0

    for key in keys:
        src = SOURCES[key]
        dest_dir = DEST_BASE / key
        dest_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n[{key}]  {src['note']}")

        # Step 1: download
        if src.get("compressed"):
            raw_dest = dest_dir / src["compressed"].split("/")[-1]
            dl_ok = _download(src["url"], raw_dest, skip_existing=skip)
        else:
            raw_dest = dest_dir / src["raw"].split("/")[-1]
            dl_ok = _download(src["url"], raw_dest,
                              byte_range=src.get("range"), skip_existing=skip)

        if not dl_ok:
            print(f"  FAILED download for {key}", file=sys.stderr)
            fail_count += 1
            continue

        # Step 2: extract
        proc = PROCESSORS[key]
        ext_ok = proc(dest_dir, skip)
        if ext_ok:
            ok_count += 1
        else:
            print(f"  FAILED extraction for {key}", file=sys.stderr)
            fail_count += 1

    print(f"\n{'='*60}")
    print(f"Done: {ok_count} succeeded, {fail_count} failed")
    print(f"\nNext step — measure the extracted files:")
    print(f"  uv run scripts/measure-corpus.py \\")
    print(f"      --dirs build/raw/natural \\")
    print(f"      --out build/bench/natural-measurements.csv")
    print(f"\nThen rebuild the curated selection:")
    print(f"  uv run scripts/select-curated.py")


if __name__ == "__main__":
    main()
