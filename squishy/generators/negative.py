"""Generate negative-path fixtures: corrupted/malformed inputs that a
decompressor must reject without crashing, OOMing, or producing wrong output.

Categories:
  truncated/          — input chopped at decoder-sensitive offsets
  bitflip/            — single byte flipped at magic/header/body/checksum/last
  declared-length/    — header claims wrong decompressed size
  concat-mixed/       — first member valid, second corrupt
  valid-empty/        — minimal valid empty stream (sanity)
  zstd-skipframe-only/— only a skippable frame, no data frame
  cve-class/          — fixtures shaped like real-world decoder CVEs
  bomb/               — small input, huge expansion (decompression-bomb test)
  concat/             — multi-member streams (valid but tricky)

These fixtures depend on the individual compressed files produced by the
compress stage; they read from cfg.individual_dir / "pathological".

Sidecar .hazard.json files are written alongside each fixture.
A hazard-catalog.json is written to the negative root.
"""
from __future__ import annotations

import bz2
import gzip
import io
import json
import lzma
import struct
import zipfile
import zlib
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import write_bytes_atomic

# ── hazard metadata ───────────────────────────────────────────────────────────

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
    "truncated/":           {"class": "malformed",    "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "bitflip/":             {"class": "malformed",    "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "concat-mixed/":        {"class": "malformed",    "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "declared-length/":     {"class": "malformed",    "severity": "medium", "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "cve-class/":           {"class": "malformed",    "severity": "high",   "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "concat/":              {"class": "concat-multi", "severity": "none",   "safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept_all_members_or_reject"},
    "valid-empty/":         {"class": "valid-edge",   "severity": "none",   "safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept"},
    "zstd-skipframe-only/": {"class": "valid-edge",   "severity": "none",   "safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept"},
}

# Module-level reference to negative root dir, set in run() before generators.
_negative_root: Path | None = None


def _write(path: Path, data: bytes) -> None:
    write_bytes_atomic(path, data)
    print(f"  {path.relative_to(_negative_root) if _negative_root else path.name} ({len(data)} bytes)")
    if _negative_root is not None:
        _write_hazard_sidecar(path, _negative_root)


def _write_hazard_sidecar(path: Path, outdir_root: Path) -> None:
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


# ── helpers ───────────────────────────────────────────────────────────────────

def _flip(data: bytes, offset: int, mask: int = 0xFF) -> bytes:
    if offset >= len(data):
        return data
    b = bytearray(data)
    b[offset] ^= mask
    return bytes(b)


# ── truncation ────────────────────────────────────────────────────────────────

def _make_truncations(src: Path, outdir: Path) -> None:
    data = src.read_bytes()
    base = src.name
    for n, label in [
        (0, "trunc-0B"),
        (8, "trunc-8B"),
        (32, "trunc-32B"),
        (1024, "trunc-1024B"),
        (len(data) // 2, "trunc-mid"),
        (max(0, len(data) - 1), "trunc-tailless"),
    ]:
        _write(outdir / f"{base}.{label}", data[:n])


# ── bit-flip ──────────────────────────────────────────────────────────────────

def _make_bitflips(src: Path, outdir: Path) -> None:
    data = src.read_bytes()
    base = src.name
    flips = {
        "flip-magic-byte0": _flip(data, 0),
        "flip-magic-byte1": _flip(data, 1) if len(data) > 1 else data,
        "flip-header-mid":  _flip(data, min(8, len(data) - 1)),
        "flip-body-mid":    _flip(data, len(data) // 2) if len(data) > 16 else data,
        "flip-last":        _flip(data, len(data) - 1) if data else data,
    }
    for label, mutated in flips.items():
        _write(outdir / f"{base}.{label}", mutated)


# ── declared-length ───────────────────────────────────────────────────────────

def _make_declared_length(src: Path, outdir: Path) -> None:
    """Mutate the gzip ISIZE footer to lie about uncompressed size."""
    data = src.read_bytes()
    base = src.name
    if base.endswith(".gz") and len(data) >= 4:
        too_large = data[:-4] + struct.pack("<I", 0xFFFFFFFF)
        too_small = data[:-4] + struct.pack("<I", 0x00000001)
        _write(outdir / f"{base}.isize-toolarge", too_large)
        _write(outdir / f"{base}.isize-toosmall", too_small)


# ── concat-mixed ──────────────────────────────────────────────────────────────

def _make_concat_mixed(src: Path, outdir: Path) -> None:
    data = src.read_bytes()
    base = src.name
    _write(outdir / f"{base}.good-then-trunc", data + data[: max(8, len(data) // 4)])


# ── valid-empty streams ───────────────────────────────────────────────────────

def _write_valid_empties(outdir: Path) -> None:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(b"")
    _write(outdir / "valid-empty.gz", buf.getvalue())

    # zstd empty: magic + FHD + raw empty last block
    _write(outdir / "valid-empty.zst", bytes.fromhex("28b52ffd20000100 00".replace(" ", "")))

    # xz empty stream
    _write(outdir / "valid-empty.xz", bytes.fromhex(
        "fd377a585a000004e6d6b446"
        "0000"
        "0000000000"
        "00000000"
        "0000"
        "595a"
    ))


# ── zstd skippable-frame-only ──────────────────────────────────────────────────

def _write_zstd_skipframe_only(outdir: Path) -> None:
    payload = b"this is metadata, not data"
    frame = struct.pack("<II", 0x184D2A50, len(payload)) + payload
    _write(outdir / "zst-skippable-only.zst", frame)


# ── decompression bombs ───────────────────────────────────────────────────────

def _write_bombs(outdir: Path) -> None:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(b"\x00" * (1 << 20))
    _write(outdir / "bomb-gz-1MiB-to-zeros.gz", buf.getvalue())

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(b"\x00" * (10 << 20))
    _write(outdir / "bomb-gz-10MiB-to-zeros.gz", buf.getvalue())


# ── nested zip bomb ───────────────────────────────────────────────────────────

def _write_zip_bomb_nested(outdir: Path) -> None:
    level0 = gzip.compress(b"\x00" * (1024 * 1024), compresslevel=9)

    current = level0
    for level in range(1, 4):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for i in range(10):
                zi = zipfile.ZipInfo(
                    f"level{level - 1}_{i:02d}.gz",
                    date_time=(2024, 1, 1, 0, 0, 0),
                )
                zf.writestr(zi, current)
        current = buf.getvalue()

    hazard_text = (
        "HAZARD: decompression bomb\n"
        "Class: bomb / severity: critical\n"
        "This archive expands to approximately 10 GiB if fully extracted "
        "(4-level nested zip, 10^4 copies of 1 MiB zeros).\n"
        "Apply an output-size cap (recommended: 1 GiB max) and nesting-depth limit "
        "(recommended: 2) before extracting.\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zi_warn = zipfile.ZipInfo(
            "__HAZARD__READ_BEFORE_EXTRACTING.txt",
            date_time=(2024, 1, 1, 0, 0, 0),
        )
        zf.writestr(zi_warn, hazard_text.encode())
        for i in range(10):
            zi = zipfile.ZipInfo(f"level3_{i:02d}.zip", date_time=(2024, 1, 1, 0, 0, 0))
            zf.writestr(zi, current)
    _write(outdir / "nested-zip-4levels.zip", buf.getvalue())


# ── CVE-2022-37434: FHCRC + truncated extra ───────────────────────────────────

def _write_cve_2022_37434(outdir: Path) -> None:
    magic   = b"\x1F\x8B"
    method  = b"\x08"
    flags   = bytes([0x02 | 0x04])  # FHCRC | FEXTRA
    mtime   = b"\x00\x00\x00\x00"
    xfl     = b"\x00"
    os_byte = b"\x03"
    xlen    = b"\x05\x00"           # claims 5 bytes extra
    extra_truncated = b"\x41"       # only 1 byte provided
    payload = magic + method + flags + mtime + xfl + os_byte + xlen + extra_truncated
    _write(outdir / "gzip-fhcrc-truncated-extra.gz", payload)


# ── ZIP local/central-directory name mismatch ────────────────────────────────

def _write_zip_name_mismatch(outdir: Path) -> None:
    content    = b"harmless content"
    compressed = content
    crc        = zlib.crc32(content) & 0xFFFFFFFF

    local_name   = b"a.txt"
    central_name = b"../evil.txt"

    local_hdr = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50, 20, 0, 0, 0, 0,
        crc, len(compressed), len(content),
        len(local_name), 0,
    ) + local_name + compressed

    central_entry = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50, 20, 20, 0, 0, 0, 0,
        crc, len(compressed), len(content),
        len(central_name), 0, 0, 0, 0, 0,
        0,  # local header offset
    ) + central_name

    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50, 0, 0, 1, 1,
        len(central_entry), len(local_hdr), 0,
    )

    _write(outdir / "zip-name-mismatch.zip", local_hdr + central_entry + eocd)


# ── bzip2 multi-stream ────────────────────────────────────────────────────────

def _write_bzip2_multistream(outdir: Path) -> None:
    stream_a = bz2.compress(b"stream-A:" + b"a" * 1024)
    stream_b = bz2.compress(b"stream-B:" + b"b" * 1024)
    _write(outdir / "bzip2-two-streams.bz2", stream_a + stream_b)


# ── gzip with trailing garbage ────────────────────────────────────────────────

def _write_gzip_trailing_garbage(outdir: Path) -> None:
    content  = b"valid gzip content for trailing-garbage test"
    valid_gz = gzip.compress(content, compresslevel=1)
    _write(outdir / "gzip-trailing-garbage.gz", valid_gz + b"\xAB")


# ── xz multi-stream ───────────────────────────────────────────────────────────

def _write_xz_multistream(outdir: Path) -> None:
    stream_a = lzma.compress(b"xz-stream-A:" + b"x" * 1024, format=lzma.FORMAT_XZ)
    stream_b = lzma.compress(b"xz-stream-B:" + b"y" * 1024, format=lzma.FORMAT_XZ)
    _write(outdir / "xz-two-streams.xz", stream_a + stream_b)


# ── ZIP overlapping entries ───────────────────────────────────────────────────

def _write_zip_overlapping_entries(outdir: Path) -> None:
    content_a = b"entry-A-data"
    content_b = b"entry-B-HIDDEN"
    crc_a = zlib.crc32(content_a) & 0xFFFFFFFF
    crc_b = zlib.crc32(content_b) & 0xFFFFFFFF
    name_a = b"visible.txt"
    name_b = b"hidden.txt"

    lhdr_a = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50, 20, 0, 0, 0, 0,
        crc_a, len(content_a), len(content_a),
        len(name_a), 0,
    ) + name_a
    body = lhdr_a + content_a

    def central(name: bytes, crc: int, size: int, local_offset: int) -> bytes:
        return struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50, 20, 20, 0, 0, 0, 0,
            crc, size, size,
            len(name), 0, 0, 0, 0, 0,
            local_offset,
        ) + name

    cd = central(name_a, crc_a, len(content_a), 0)
    # Second entry points to the same offset as A — overlapping
    cd += central(name_b, crc_b, len(content_b), 0)

    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 2, 2, len(cd), len(body), 0)
    _write(outdir / "zip-overlapping-entries.zip", body + cd + eocd)


# ── zstd huge Frame_Content_Size ──────────────────────────────────────────────

def _write_zstd_huge_fcs(outdir: Path) -> None:
    ZSTD_MAGIC = b"\x28\xB5\x2F\xFD"
    # FCS_FLAG=3 (8-byte FCS), Single_Segment=0
    fhd = bytes([(3 << 6) | 0])
    # Window descriptor: exponent=20 → 1 MiB window
    window_desc = bytes([(20 << 3) | 0])
    fcs = struct.pack("<Q", 10 * 1024 * 1024 * 1024)  # 10 GiB
    # Last raw block, size=0: 3-byte header (Block_Last=1, Block_Type=0, Block_Size=0)
    last_block = struct.pack("<I", (1 << 0) | (0 << 1))[:3]
    _write(outdir / "zstd-fcs-10gib-empty.zst", ZSTD_MAGIC + fhd + window_desc + fcs + last_block)


# ── encrypted-like high-entropy data ─────────────────────────────────────────

def _write_encrypted_like(outdir: Path) -> None:
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

    _write(outdir / "encrypted-like-1M", bytes(out_bytes))


# ── CVE-class hand-crafted shapes ─────────────────────────────────────────────

def _write_cve_class(outdir: Path) -> None:
    # gzip BTYPE=11 (reserved deflate block type)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=0) as gz:
        gz.write(b"A")
    data = bytearray(buf.getvalue())
    if len(data) > 11:
        data[10] |= 0b00000110  # set btype bits to 11
        _write(outdir / "gz-btype-11-reserved.gz", bytes(data))

    # zstd: FCS=UINT64_MAX with single_segment flag
    fhd = 0b11_0_0_0_0_00  # size_format=3 (FCS=8 bytes), single_segment=1
    frame = struct.pack("<I", 0xFD2FB528) + bytes([fhd])
    frame += struct.pack("<Q", 0xFFFFFFFFFFFFFFFF)
    frame += bytes([0x01, 0x00, 0x00])  # raw block, last=1, size=0
    _write(outdir / "zst-fcs-uint64max.zst", frame)

    # zip-slip path traversal
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo(filename="../../etc/passwd")
        zi.date_time = (1980, 1, 1, 0, 0, 0)
        zf.writestr(zi, b"root::0:0:root:/root:/bin/sh\n")
    _write(outdir / "zip-slip-path.zip", buf.getvalue())

    # zip with mismatched zip32/zip64 EOCD
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hello")
    z = buf.getvalue()
    fake_locator = struct.pack("<IIQI", 0x07064B50, 0, 0xFFFFFFFFFFFFFFFF, 1)
    _write(outdir / "zip-z64-mismatch.zip", z[:-22] + fake_locator + z[-22:])


# ── main orchestrator ─────────────────────────────────────────────────────────

def run(cfg: BuildConfig) -> int:
    """Generate all negative fixtures. Returns 0 on success, 1 on failure."""
    global _negative_root

    try:
        out = cfg.negative_dir
        out.mkdir(parents=True, exist_ok=True)
        _negative_root = out

        # Mutation sources: small-256B compressed with each codec
        indiv = cfg.individual_dir
        sources: list[Path] = []
        for codec_ext in ["gz", "bz2", "xz", "zst", "lz4", "br", "lzma"]:
            cand = indiv / "pathological" / f"small-256B.{codec_ext}"
            if cand.exists():
                sources.append(cand)

        for src in sources:
            _make_truncations(src, out / "truncated")
            _make_bitflips(src, out / "bitflip")
            _make_declared_length(src, out / "declared-length")
            _make_concat_mixed(src, out / "concat-mixed")

        _write_valid_empties(out / "valid-empty")
        _write_zstd_skipframe_only(out / "zstd-skipframe-only")
        _write_bombs(out / "bomb")
        _write_cve_class(out / "cve-class")

        _write_zip_bomb_nested(out / "bomb")
        _write_cve_2022_37434(out / "cve-class")
        _write_zip_name_mismatch(out / "cve-class")
        _write_zip_overlapping_entries(out / "cve-class")
        _write_zstd_huge_fcs(out / "declared-length")
        _write_bzip2_multistream(out / "concat")
        _write_gzip_trailing_garbage(out / "concat")
        _write_xz_multistream(out / "concat")
        _write_encrypted_like(out)

        catalog = {
            "version": 1,
            "by_path": HAZARD_CATALOG,
            "by_dir_prefix": HAZARD_BY_DIR,
        }
        catalog_path = out / "hazard-catalog.json"
        catalog_path.write_text(json.dumps(catalog, indent=2))
        print(f"  hazard-catalog.json written")

        print(f"  negative: all fixtures written to {out}")
        return 0

    except Exception as exc:
        print(f"  ERROR in negative: {exc}")
        import traceback; traceback.print_exc()
        return 1
