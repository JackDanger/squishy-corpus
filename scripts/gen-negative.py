#!/usr/bin/env python3
"""Generate negative-path fixtures: corrupted / malformed inputs that a
decompressor must reject without crashing, OOM, or producing wrong output.

Categories:
  truncated/          — input chopped off at decoder-sensitive offsets
  bitflip/            — single byte flipped at magic/header/body/checksum/last
  declared-length/    — header claims wrong decompressed size
  empty-body/         — valid header, no payload
  garbage-body/       — valid header, random payload
  concat-mixed/       — first member valid, second corrupt
  valid-empty/        — minimal valid empty stream (sanity)
  zstd-skipframe-only/— only a skippable frame, no data frame
  zstd-legacy/        — legacy-format frames (need libzstd legacy headers)
  cve-class/          — fixtures shaped like real-world decoder CVEs
  bomb/               — small input, huge expansion (decompression-bomb test)

These fixtures are intentionally SMALL on disk. Decompression-bomb fixtures
are clearly labeled and bounded so test harnesses can apply expansion caps.
"""
from __future__ import annotations
import argparse, gzip, io, os, struct, sys
from pathlib import Path

def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.rename(path)

# ─── helpers ──────────────────────────────────────────────────────────────
def take(path: Path) -> bytes:
    return path.read_bytes()

def flip(data: bytes, offset: int, mask: int = 0xff) -> bytes:
    if offset >= len(data):
        return data
    b = bytearray(data)
    b[offset] ^= mask
    return bytes(b)

# ─── truncation ───────────────────────────────────────────────────────────
def make_truncations(src: Path, outdir: Path) -> None:
    data = take(src)
    base = src.name
    for n, label in [(0, "trunc-0B"), (8, "trunc-8B"), (32, "trunc-32B"),
                     (1024, "trunc-1024B"), (len(data) // 2, "trunc-mid"),
                     (max(0, len(data) - 1), "trunc-tailless")]:
        write(outdir / f"{base}.{label}", data[:n])

# ─── bit-flip ─────────────────────────────────────────────────────────────
def make_bitflips(src: Path, outdir: Path) -> None:
    data = take(src); base = src.name
    flips = {
        "flip-magic-byte0":  flip(data, 0),
        "flip-magic-byte1":  flip(data, 1) if len(data) > 1 else data,
        "flip-header-mid":   flip(data, min(8, len(data) - 1)),
        "flip-body-mid":     flip(data, len(data) // 2) if len(data) > 16 else data,
        "flip-last":         flip(data, len(data) - 1) if data else data,
    }
    for label, mutated in flips.items():
        write(outdir / f"{base}.{label}", mutated)

# ─── declared-length attacks (gzip / xz / zstd) ───────────────────────────
def make_declared_length(src: Path, outdir: Path) -> None:
    """gzip stores ISIZE (uncompressed size mod 2^32) in the last 4 bytes.
    Toggle it to lie about size."""
    data = take(src); base = src.name
    if base.endswith(".gz") and len(data) >= 4:
        too_large = data[:-4] + struct.pack("<I", 0xffffffff)
        too_small = data[:-4] + struct.pack("<I", 0x00000001)
        write(outdir / f"{base}.isize-toolarge", too_large)
        write(outdir / f"{base}.isize-toosmall", too_small)

# ─── valid-empty + empty-body + garbage-body ──────────────────────────────
def write_valid_empties(outdir: Path) -> None:
    # canonical empty gzip stream (mtime=0, no name)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(b"")
    write(outdir / "valid-empty.gz", buf.getvalue())
    # zstd empty frame: ZSTD_MAGIC + frame_header(no content) + raw block(last)
    # 28 b1 2f fd  20 00  01 00 00  — magic + FHD + raw-empty
    write(outdir / "valid-empty.zst", bytes.fromhex("28b52ffd20000100 00"))
    # xz empty: header magic, footer magic, two-byte index
    write(outdir / "valid-empty.xz", bytes.fromhex(
        "fd377a585a000004e6d6b446"
        "0000"  # stream flags
        "0000000000"  # index
        "00000000"  # backward size
        "0000"  # stream flags (footer)
        "595a"  # footer magic
    ))

# ─── concat-mixed: good frame followed by truncated frame ─────────────────
def make_concat_mixed(src: Path, outdir: Path) -> None:
    data = take(src); base = src.name
    write(outdir / f"{base}.good-then-trunc", data + data[: max(8, len(data) // 4)])

# ─── zstd skippable-frame-only stream ─────────────────────────────────────
def write_zstd_skipframe_only(outdir: Path) -> None:
    # Skippable frame magic 0x184D2A50..0x184D2A5F + LE 4-byte size + payload
    payload = b"this is metadata, not data"
    frame = struct.pack("<II", 0x184D2A50, len(payload)) + payload
    write(outdir / "zst-skippable-only.zst", frame)

# ─── decompression bombs ──────────────────────────────────────────────────
def write_bombs(outdir: Path) -> None:
    # gzip bomb: 1 MiB of zeros compresses to ~1 KiB
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(b"\x00" * (1 << 20))
    write(outdir / "bomb-gz-1MiB-to-zeros.gz", buf.getvalue())

    # Larger gzip bomb (10 MiB zeros)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(b"\x00" * (10 << 20))
    write(outdir / "bomb-gz-10MiB-to-zeros.gz", buf.getvalue())

# ─── CVE-class shapes (best-effort hand-crafted) ──────────────────────────
def write_cve_class(outdir: Path) -> None:
    # gzip BTYPE=11 (reserved) — first deflate block header has btype field
    # in bits 1-2. We patch a minimal valid gzip and OR the btype to 0b11.
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=0) as gz:
        gz.write(b"A")
    data = bytearray(buf.getvalue())
    # find start of deflate stream (after 10-byte gzip header)
    if len(data) > 11:
        data[10] |= 0b00000110  # set btype bits to 11 (reserved)
        write(outdir / "gz-btype-11-reserved.gz", bytes(data))

    # zstd: malformed frame_content_size where Single_Segment is set but
    # the declared size is absurd (UINT64_MAX)
    # Magic + FHD with FCS=8 bytes (size_format=3), single_segment=1
    fhd = 0b11_0_0_0_0_00  # size_format=3 (FCS=8), single_segment, no content checksum
    frame = struct.pack("<I", 0xFD2FB528) + bytes([fhd])
    frame += struct.pack("<Q", 0xFFFFFFFFFFFFFFFF)  # impossible decompressed size
    # raw block, last=1, size=0
    frame += bytes([0x01, 0x00, 0x00])
    write(outdir / "zst-fcs-uint64max.zst", frame)

    # zip-slip path in zip archive
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo(filename="../../etc/passwd")
        zi.date_time = (1980, 1, 1, 0, 0, 0)
        zf.writestr(zi, b"root::0:0:root:/root:/bin/sh\n")
    write(outdir / "zip-slip-path.zip", buf.getvalue())

    # zip with mismatched zip32 EOCD vs zip64 EOCD (truncated zip64 locator)
    # Build a normal zip then prepend a bogus zip64 EOCD locator
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hello")
    z = buf.getvalue()
    # Insert a fake zip64 EOCD locator (magic 0x07064b50) with garbage offset
    fake_locator = struct.pack("<IIQI", 0x07064b50, 0, 0xffffffffffffffff, 1)
    write(outdir / "zip-z64-mismatch.zip", z[:-22] + fake_locator + z[-22:])

# ─── main orchestrator ────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--indiv",  required=True, help="path to build/individual")
    ap.add_argument("--bundle", required=True, help="path to build/bundles")
    ap.add_argument("--out",    required=True, help="path to build/negative")
    args = ap.parse_args()

    indiv = Path(args.indiv); out = Path(args.out)

    # Pick a small subset of individual files as mutation sources — one per codec.
    # Using a tiny input (small-256B) keeps fixtures small.
    sources = []
    for codec_ext in ["gz", "bz2", "xz", "zst", "lz4", "br", "lzma"]:
        cand = indiv / "pathological" / f"small-256B.{codec_ext}"
        if cand.exists():
            sources.append(cand)

    for src in sources:
        make_truncations(src, out / "truncated")
        make_bitflips(src, out / "bitflip")
        make_declared_length(src, out / "declared-length")
        make_concat_mixed(src, out / "concat-mixed")

    write_valid_empties(out / "valid-empty")
    write_zstd_skipframe_only(out / "zstd-skipframe-only")
    write_bombs(out / "bomb")
    write_cve_class(out / "cve-class")

    print(f"wrote negative fixtures to {out}", file=sys.stderr)

if __name__ == "__main__":
    main()
