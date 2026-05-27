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
import argparse, gzip, io, json, os, struct, sys
from pathlib import Path

# ─── hazard metadata ──────────────────────────────────────────────────────
HAZARD_CATALOG: dict[str, dict] = {
    "bomb/bomb-gz-1MiB-to-zeros.gz": {
        "class": "bomb",
        "severity": "medium",
        "expansion_bytes_max": 1 << 20,
        "expansion_ratio_max": 1000,
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject_or_cap",
        "rationale": "gzip bomb: 1 MiB zeros compressed to ~1 KiB",
    },
    "bomb/bomb-gz-10MiB-to-zeros.gz": {
        "class": "bomb",
        "severity": "medium",
        "expansion_bytes_max": 10 << 20,
        "expansion_ratio_max": 1000,
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject_or_cap",
        "rationale": "gzip bomb: 10 MiB zeros compressed to ~10 KiB",
    },
    "bomb/nested-zip-4levels.zip": {
        "class": "bomb",
        "severity": "critical",
        "expansion_bytes_max": 10 * 1024**3,
        "expansion_ratio_max": None,
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject_or_cap",
        "rationale": "4-level nested zip bomb: 10^4 copies of 1 MiB zeros, ~10 GiB fully expanded",
    },
    "cve-class/gz-btype-11-reserved.gz": {
        "class": "malformed",
        "severity": "high",
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject",
        "rationale": "gzip with DEFLATE BTYPE=11 (reserved), shaped like CVE triggering reserved-block mishandling",
    },
    "cve-class/zst-fcs-uint64max.zst": {
        "class": "malformed",
        "severity": "high",
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject",
        "rationale": "zstd frame with Frame_Content_Size=UINT64_MAX, triggers integer overflow in size allocation",
    },
    "cve-class/zip-slip-path.zip": {
        "class": "malformed",
        "severity": "high",
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject",
        "rationale": "zip with path traversal entry (../../etc/passwd), zip-slip attack vector",
    },
    "cve-class/zip-z64-mismatch.zip": {
        "class": "malformed",
        "severity": "high",
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject",
        "rationale": "zip with mismatched zip32 EOCD and bogus zip64 EOCD locator, triggers parser confusion",
    },
    "cve-class/gzip-fhcrc-truncated-extra.gz": {
        "class": "malformed",
        "severity": "high",
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject",
        "rationale": "gzip with FHCRC+FEXTRA flags set but extra field truncated, shaped like CVE-2022-37434",
    },
    "cve-class/zip-name-mismatch.zip": {
        "class": "malformed",
        "severity": "high",
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject",
        "rationale": "zip with local header filename (a.txt) differing from central directory filename (../evil.txt)",
    },
    "cve-class/zip-overlapping-entries.zip": {
        "class": "malformed",
        "severity": "high",
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject",
        "rationale": "zip with two central directory entries pointing to the same local file offset (overlapping entries)",
    },
    "declared-length/zstd-fcs-10gib-empty.zst": {
        "class": "malformed",
        "severity": "medium",
        "expansion_bytes_max": 10 * 1024**3,
        "safe_to_decode_unbounded": False,
        "expected_decoder_outcome": "reject",
        "rationale": "zstd frame declaring FCS=10 GiB with an empty body, triggers pre-allocation attacks",
    },
    "concat/bzip2-two-streams.bz2": {
        "class": "concat-multi",
        "severity": "none",
        "safe_to_decode_unbounded": True,
        "expected_decoder_outcome": "accept_all_members_or_reject",
        "rationale": "two valid bzip2 streams concatenated; decoders must handle multi-member or reject cleanly",
    },
    "concat/gzip-trailing-garbage.gz": {
        "class": "concat-multi",
        "severity": "none",
        "safe_to_decode_unbounded": True,
        "expected_decoder_outcome": "accept_all_members_or_reject",
        "rationale": "valid gzip stream with a trailing non-zero garbage byte; tests trailing-data handling",
    },
    "concat/xz-two-streams.xz": {
        "class": "concat-multi",
        "severity": "none",
        "safe_to_decode_unbounded": True,
        "expected_decoder_outcome": "accept_all_members_or_reject",
        "rationale": "two valid xz streams concatenated; decoders must handle multi-stream or reject cleanly",
    },
    "valid-empty/valid-empty.gz": {
        "class": "valid-edge",
        "severity": "none",
        "safe_to_decode_unbounded": True,
        "expected_decoder_outcome": "accept",
        "rationale": "minimal valid gzip stream containing zero bytes of content",
    },
    "valid-empty/valid-empty.zst": {
        "class": "valid-edge",
        "severity": "none",
        "safe_to_decode_unbounded": True,
        "expected_decoder_outcome": "accept",
        "rationale": "minimal valid zstd frame containing zero bytes of content",
    },
    "valid-empty/valid-empty.xz": {
        "class": "valid-edge",
        "severity": "none",
        "safe_to_decode_unbounded": True,
        "expected_decoder_outcome": "accept",
        "rationale": "minimal valid xz stream containing zero bytes of content",
    },
    "zstd-skipframe-only/zst-skippable-only.zst": {
        "class": "valid-edge",
        "severity": "none",
        "safe_to_decode_unbounded": True,
        "expected_decoder_outcome": "accept",
        "rationale": "zstd file containing only a skippable frame and no data frame; valid per spec",
    },
    "encrypted-like-1M": {
        "class": "none",
        "severity": "none",
        "safe_to_decode_unbounded": True,
        "expected_decoder_outcome": "accept",
        "rationale": "1 MiB of RC4-keystream-like high-entropy data; no compression magic bytes",
    },
}

HAZARD_BY_DIR: dict[str, dict] = {
    "truncated/":      {"class": "malformed", "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "bitflip/":        {"class": "malformed", "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "concat-mixed/":   {"class": "malformed", "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "declared-length/":{"class": "malformed", "severity": "medium", "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "cve-class/":      {"class": "malformed", "severity": "high",   "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "concat/":         {"class": "concat-multi","severity": "none", "safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept_all_members_or_reject"},
    "valid-empty/":    {"class": "valid-edge", "severity": "none",  "safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept"},
    "zstd-skipframe-only/": {"class": "valid-edge", "severity": "none", "safe_to_decode_unbounded": True, "expected_decoder_outcome": "accept"},
}

# Set by main() before calling generators; used by write_hazard_sidecar.
_negative_root: Path | None = None

def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.rename(path)
    if _negative_root is not None:
        write_hazard_sidecar(path, _negative_root)

def write_hazard_sidecar(path: Path, outdir_root: Path) -> None:
    """Write <path>.hazard.json using HAZARD_CATALOG or HAZARD_BY_DIR."""
    rel = path.relative_to(outdir_root).as_posix()
    hazard = HAZARD_CATALOG.get(rel)
    if hazard is None:
        for prefix, h in HAZARD_BY_DIR.items():
            if rel.startswith(prefix):
                hazard = dict(h)
                hazard["path"] = rel
                break
    if hazard is None:
        return
    sidecar = path.with_name(path.name + ".hazard.json")
    sidecar.write_text(json.dumps({"version": 1, "path": rel, **hazard}, indent=2))

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

# ─── nested zip bomb ──────────────────────────────────────────────────────
def write_zip_bomb_nested(outdir: Path) -> None:
    import zipfile, io as _io, gzip as _gzip

    # Level 0: 1 MB of zeros, gzipped
    level0 = _gzip.compress(b'\x00' * (1024 * 1024), compresslevel=9)

    # Levels 1-3: each zip contains 10 copies of the previous level
    current = level0
    for level in range(1, 4):
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as zf:
            for i in range(10):
                zi = zipfile.ZipInfo(f"level{level-1}_{i:02d}.gz",
                                     date_time=(2024, 1, 1, 0, 0, 0))
                zf.writestr(zi, current)
        current = buf.getvalue()

    # Level 4 (outermost): add __HAZARD__ entry FIRST, then 10 copies of level 3
    hazard_text = (
        "HAZARD: decompression bomb\n"
        "Class: bomb / severity: critical\n"
        "This archive expands to approximately 10 GiB if fully extracted "
        "(4-level nested zip, 10^4 copies of 1 MiB zeros).\n"
        "Apply an output-size cap (recommended: 1 GiB max) and nesting-depth limit "
        "(recommended: 2) before extracting.\n"
        "Source: https://jackdanger.com/squishy/AGENTS.md\n"
    )
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as zf:
        zi_warn = zipfile.ZipInfo("__HAZARD__READ_BEFORE_EXTRACTING.txt",
                                  date_time=(2024, 1, 1, 0, 0, 0))
        zf.writestr(zi_warn, hazard_text.encode())
        for i in range(10):
            zi = zipfile.ZipInfo(f"level3_{i:02d}.zip",
                                 date_time=(2024, 1, 1, 0, 0, 0))
            zf.writestr(zi, current)
    outermost = buf.getvalue()

    write(outdir / "nested-zip-4levels.zip", outermost)
    print(f"  nested-zip-4levels.zip: {len(outermost)} bytes → ~{10**4 * 1024 * 1024 // (1024*1024*1024):.0f} GiB expanded",
          file=sys.stderr)

# ─── CVE-2022-37434: gzip FHCRC + truncated extra field ───────────────────
def write_cve_2022_37434(outdir: Path) -> None:
    # gzip header with FHCRC flag (bit 1 of FLG) set and FEXTRA flag (bit 2) set
    # but extra field length truncated — only 1 byte when 2 are required
    magic = b'\x1f\x8b'           # gzip magic
    method = b'\x08'              # DEFLATE
    flags = bytes([0x02 | 0x04])  # FHCRC=1, FEXTRA=1
    mtime = b'\x00\x00\x00\x00'
    xfl = b'\x00'
    os_byte = b'\x03'             # Unix
    # FEXTRA: XLEN field is 2 bytes, then XLEN bytes of extra data
    # We write XLEN=5 but only include 1 byte of extra data → truncated
    xlen = b'\x05\x00'            # little-endian 5
    extra_truncated = b'\x41'     # only 1 byte instead of 5

    payload = magic + method + flags + mtime + xfl + os_byte + xlen + extra_truncated
    write(outdir / "gzip-fhcrc-truncated-extra.gz", payload)

# ─── ZIP local/central-directory name mismatch ────────────────────────────
def write_zip_name_mismatch(outdir: Path) -> None:
    import struct as _struct

    content = b"harmless content"
    compressed = content  # STORED, no compression

    local_name = b"a.txt"
    central_name = b"../evil.txt"

    # Local file header
    local_hdr = _struct.pack('<IHHHHHIIIHH',
        0x04034b50,        # local file header signature
        20,                # version needed
        0,                 # general purpose bit flag
        0,                 # compression method: STORED
        0,                 # last mod file time
        0,                 # last mod file date
        0xAD29F0A3,        # CRC-32 of content
        len(compressed),   # compressed size
        len(content),      # uncompressed size
        len(local_name),   # file name length
        0,                 # extra field length
    ) + local_name + compressed

    # Central directory entry (different name)
    local_offset = 0
    central_entry = _struct.pack('<IHHHHHHIIIHHHHHII',
        0x02014b50,         # central dir file header signature
        20, 20,             # version made by, version needed
        0,                  # general purpose bit flag
        0,                  # compression method
        0, 0,               # last mod time, date
        0xAD29F0A3,         # CRC-32
        len(compressed),    # compressed size
        len(content),       # uncompressed size
        len(central_name),  # file name length
        0, 0,               # extra field, comment length
        0,                  # disk number start
        0,                  # internal file attributes
        0,                  # external file attributes
        local_offset,       # relative offset of local header
    ) + central_name

    central_offset = len(local_hdr)
    eocd = _struct.pack('<IHHHHIIH',
        0x06054b50,          # EOCD signature
        0, 0,                # disk number, disk with start
        1, 1,                # entries on disk, total entries
        len(central_entry),  # central dir size
        central_offset,      # central dir offset
        0,                   # comment length
    )

    write(outdir / "zip-name-mismatch.zip",
          local_hdr + central_entry + eocd)

# ─── bzip2 multi-stream concatenated ──────────────────────────────────────
def write_bzip2_multistream(outdir: Path) -> None:
    import bz2
    stream_a = bz2.compress(b"stream-A:" + b"a" * 1024)
    stream_b = bz2.compress(b"stream-B:" + b"b" * 1024)
    write(outdir / "bzip2-two-streams.bz2", stream_a + stream_b)

# ─── gzip with trailing garbage byte ──────────────────────────────────────
def write_gzip_trailing_garbage(outdir: Path) -> None:
    import gzip as _gzip
    content = b"valid gzip content for trailing-garbage test"
    valid_gz = _gzip.compress(content, compresslevel=1)
    with_garbage = valid_gz + b'\xAB'  # non-zero trailing byte
    write(outdir / "gzip-trailing-garbage.gz", with_garbage)

# ─── xz multi-stream concatenated ─────────────────────────────────────────
def write_xz_multistream(outdir: Path) -> None:
    import lzma
    stream_a = lzma.compress(b"xz-stream-A:" + b"x" * 1024,
                              format=lzma.FORMAT_XZ)
    stream_b = lzma.compress(b"xz-stream-B:" + b"y" * 1024,
                              format=lzma.FORMAT_XZ)
    write(outdir / "xz-two-streams.xz", stream_a + stream_b)

# ─── ZIP with overlapping local-file entries ──────────────────────────────
def write_zip_overlapping_entries(outdir: Path) -> None:
    import struct as _struct, zlib

    content_a = b"entry-A-data"
    content_b = b"entry-B-HIDDEN"

    crc_a = zlib.crc32(content_a) & 0xFFFFFFFF
    crc_b = zlib.crc32(content_b) & 0xFFFFFFFF

    name_a = b"visible.txt"
    name_b = b"hidden.txt"

    # Entry A local header starts at offset 0
    offset_a = 0
    lhdr_a = _struct.pack('<IHHHHHIIIHH',
        0x04034b50, 20, 0, 0, 0, 0, crc_a,
        len(content_a), len(content_a), len(name_a), 0) + name_a

    # Entry B local header is placed so it OVERLAPS with entry A's data
    # (starts before A's data ends)
    # For simplicity: place B at offset 0 too (they share the same local header offset)
    # This makes the central directory point to two files at the same offset
    offset_b = 0  # same offset as A — two entries claiming the same space

    # Build: only write A's data physically, but central dir has both
    body = lhdr_a + content_a

    def central(name, crc, size, local_offset):
        return _struct.pack('<IHHHHHHIIIHHHHHII',
            0x02014b50, 20, 20, 0, 0, 0, 0, crc, size, size,
            len(name), 0, 0, 0, 0, 0, local_offset) + name

    cd = central(name_a, crc_a, len(content_a), offset_a)
    cd += central(name_b, crc_b, len(content_b), offset_b)  # points to same offset as A

    eocd = _struct.pack('<IHHHHIIH',
        0x06054b50, 0, 0, 2, 2, len(cd), len(body), 0)

    write(outdir / "zip-overlapping-entries.zip", body + cd + eocd)

# ─── zstd frame with implausible Frame_Content_Size ───────────────────────
def write_zstd_huge_fcs(outdir: Path) -> None:
    # Manually craft a zstd frame with FCS=10GiB but tiny body
    # zstd frame format: Magic(4) + FHD(1) + [WindowDescriptor(1)] + [FCS(1-8)] + Blocks + Checksum
    import struct as _struct

    ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'

    # FHD byte: FCS_FLAG=3 (8-byte FCS), Single_Segment_Flag=0, Content_Checksum_Flag=0
    # Bits [7:6]=FCS_FLAG=11 (8 bytes), [5]=Single_Segment=0, [4:2]=reserved=0, [1:0]=Dict_ID_Flag=0
    fhd = bytes([(3 << 6) | 0])  # FCS_FLAG=3

    # Window descriptor (required when Single_Segment=0)
    # Mantissa=0, Exponent=20 → window size = 1 MiB (reasonable)
    window_desc = bytes([(20 << 3) | 0])

    # Frame Content Size: 8 bytes, value = 10 * 1024^3
    fcs = _struct.pack('<Q', 10 * 1024 * 1024 * 1024)

    # Last block: empty block (Block_Last=1, Block_Type=00=Raw_Block, Block_Size=0)
    last_block = _struct.pack('<I', (1 << 0) | (0 << 1))[:3]  # 3-byte block header, little-endian

    frame = ZSTD_MAGIC + fhd + window_desc + fcs + last_block
    write(outdir / "zstd-fcs-10gib-empty.zst", frame)

# ─── encrypted-like high-entropy data ─────────────────────────────────────
def write_encrypted_like(outdir: Path) -> None:
    # RC4-like keystream (40-byte key) — high entropy output, pure stdlib
    key = b"squishy-corpus-encrypted-fixture-seed-v1"
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]

    out_bytes = bytearray(1024 * 1024)
    i = j = 0
    for k in range(len(out_bytes)):
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out_bytes[k] = S[(S[i] + S[j]) % 256]

    write(outdir / "encrypted-like-1M", bytes(out_bytes))

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
    global _negative_root

    ap = argparse.ArgumentParser()
    ap.add_argument("--indiv",  required=True, help="path to build/individual")
    ap.add_argument("--bundle", required=True, help="path to build/bundles")
    ap.add_argument("--out",    required=True, help="path to build/negative")
    args = ap.parse_args()

    indiv = Path(args.indiv); out = Path(args.out)
    _negative_root = out

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

    # New generators
    write_zip_bomb_nested(out / "bomb")
    write_cve_2022_37434(out / "cve-class")
    write_zip_name_mismatch(out / "cve-class")
    write_zip_overlapping_entries(out / "cve-class")
    write_zstd_huge_fcs(out / "declared-length")
    write_bzip2_multistream(out / "concat")
    write_gzip_trailing_garbage(out / "concat")
    write_xz_multistream(out / "concat")
    write_encrypted_like(out)

    catalog = {
        "version": 1,
        "by_path": HAZARD_CATALOG,
        "by_dir_prefix": HAZARD_BY_DIR,
    }
    (out / "hazard-catalog.json").write_text(json.dumps(catalog, indent=2))

    print(f"wrote negative fixtures to {out}", file=sys.stderr)

if __name__ == "__main__":
    main()
