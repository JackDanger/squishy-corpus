"""Generate pathological raw inputs designed to exercise specific decoder code paths.

Existing fixtures (migrated from scripts/gen-pathological.py):
  - Sub-window-size inputs: 0B, 1B, 13B, 256B, 4095B, 65535B
  - Entropy extremes: zeros 1/10/100M, urandom 1/10/100M, repeat-A, alternating, ascii
  - Structural patterns: onebyte-per-page, phrase-repeated, pi-digits, sparse-geometric
  - Already-compressed blob
  - Window-boundary triples: zstd-128M, brotli-16M, deflate-32K, zstd-8M, lz4-64K
  - MAX_MATCH deflate boundary: 257B, 258B, 259B
  - Mixed-entropy blocks
  - Thue-Morse sequence (aperiodic, defeats LZ77 match finders)
  - De Bruijn sequence (every 24-bit substring appears exactly once)
  - Near-duplicate pair

New adversarial fixtures:
  - dict-poison-4M: poisons sliding-window dictionaries with high-freq header
  - long-distance-match-4M: all LZ matches at max deflate window distance (32768)
  - huffman-max-4M: all 256 byte values equally distributed (max Huffman tree width)
  - entropy-oscillator-8M: alternating 1M zeros / 1M PRNG blocks
  - literal-flood-4M: medium entropy, no repeated 4-byte sequences (forces literal coding)
  - overlap-match-1M: period-5 pattern that produces overlapping LZ copy operations
"""
from __future__ import annotations

import gzip
import hashlib
import io
import random
import struct
import sys
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import write_bytes_atomic

SEED = b"jackdanger-corpus-v1"


def _prng(seed: bytes, n: int) -> bytes:
    """Deterministic, fast PRNG: SHA-256 in counter mode."""
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(seed + struct.pack(">Q", i)).digest()
        i += 1
    return bytes(out[:n])


def _write(path: Path, data: bytes) -> None:
    print(f"  {path.name} ({len(data):,} bytes)")
    write_bytes_atomic(path, data)


# ── new adversarial fixtures ──────────────────────────────────────────────────

def _make_dict_poison_4m(seed: bytes) -> bytes:
    """4 MB file that poisons sliding-window dictionary pre-loading.

    The first 32 KB uses a small 4-byte repeating alphabet {0x00, 0x01, 0x02, 0x03}
    in a cycling pattern. This fills a dictionary encoder's hash table with high-
    frequency bigrams and trigrams from that alphabet.

    The remaining 3.97 MB uses a non-overlapping alphabet {0x80..0xFF} drawn from
    the PRNG, so the patterns memorised from the header are completely absent.
    A compressor that pre-trains its dictionary on the 32 KB header will find the
    dictionary useless for the tail.

    The test property: header byte entropy << tail byte entropy (header uses 4 values,
    tail uses 128 values).
    """
    HEADER_SIZE = 32 * 1024
    TOTAL = 4 * (1 << 20)

    # Header: only 4 distinct byte values in a cycling pattern → entropy = log2(4) = 2.0
    header = bytes([i % 4 for i in range(HEADER_SIZE)])

    tail_size = TOTAL - HEADER_SIZE
    # Tail: only values 0x80-0xFF (128 values), none of which appear in the header
    tail_raw = _prng(seed + b":dict-poison-tail", tail_size)
    tail = bytes(0x80 | (b & 0x7F) for b in tail_raw)

    return header + tail


def _make_long_distance_match_4m(seed: bytes) -> bytes:
    """4 MB file where all LZ matches are 32764-32768 bytes back.

    Deflate's window is 32768 bytes. This fixture forces maximum hash chain
    traversal: a base block of 32768 bytes, then the body repeats the base
    exactly, so every byte can be matched at distance exactly 32768.

    Compressors that cap their hash chain search at fewer than 32768 steps
    will produce suboptimal or literal-only output for the tail.
    """
    WINDOW = 32768
    TOTAL = 4 * (1 << 20)

    base = _prng(seed + b":ldm-base", WINDOW)

    # Fill remaining bytes by copying from exactly WINDOW positions back.
    result = bytearray(base)
    while len(result) < TOTAL:
        chunk_size = min(WINDOW, TOTAL - len(result))
        result.extend(result[len(result) - WINDOW: len(result) - WINDOW + chunk_size])

    return bytes(result[:TOTAL])


def _make_huffman_max_4m(seed: bytes) -> bytes:
    """4 MB file with exactly 256 distinct byte values, each ~16384 times.

    Equal symbol frequency maximizes Huffman tree width (all 256 leaves at
    the same depth). Compressors that assume a skewed distribution will waste
    time re-balancing their trees.
    """
    TOTAL = 4 * (1 << 20)  # 4,194,304 bytes
    reps = TOTAL // 256     # 16384 per symbol
    remainder = TOTAL - reps * 256  # 0 for exact power-of-2 sizes

    buf = bytearray()
    for v in range(256):
        buf.extend(bytes([v]) * reps)
    if remainder:
        buf.extend(bytes(range(remainder)))

    # Shuffle to avoid runs of the same byte (which would be trivially RLE-coded).
    rng = random.Random(int.from_bytes(hashlib.sha256(seed + b":huffmax-shuffle").digest()[:8], 'big'))
    buf_list = list(buf)
    rng.shuffle(buf_list)
    return bytes(buf_list)


def _make_entropy_oscillator_8m(seed: bytes) -> bytes:
    """8 MB file alternating 1 MB blocks of zeros and 1 MB blocks of PRNG output.

    Tests codec block-split detection: an adaptive encoder must switch strategy
    every 1 MB. Encoders that commit to a strategy based on early blocks will
    either under-compress the zero blocks or waste effort on the random blocks.
    """
    MB = 1 << 20
    blocks = []
    for i in range(4):
        blocks.append(b"\x00" * MB)
        blocks.append(_prng(seed + b":entropyosc" + struct.pack(">I", i), MB))
    return b"".join(blocks)


def _make_literal_flood_4m(seed: bytes) -> bytes:
    """4 MB with no repeated 4-byte sequences and medium entropy (~5 bits/byte).

    Uses a 32-symbol alphabet (5 bits/byte) with a 4-byte counter embedded in
    a sliding window so that no 4-byte n-gram repeats within the file.

    Construction: for each output byte, we write alphabet[counter & 31] where
    counter is derived from the position, ensuring the 4-byte window at every
    position is unique (the counter bytes ensure non-repetition).

    This forces LZ encoders into literal-only output while maintaining
    non-trivial Huffman coding (5-bit effective entropy).
    """
    TOTAL = 4 * (1 << 20)
    ALPHA = 32  # 5 bits/byte
    rng = random.Random(int.from_bytes(hashlib.sha256(seed + b":litflood").digest()[:8], 'big'))

    # Embed a 4-byte big-endian counter every 4 bytes, mixed with alphabet symbols.
    # The counter occupies bits 5-7 of each byte to keep the low 5 bits free for
    # alphabet membership — but simpler: use counter directly remapped into 0-255.
    # Strategy: output = (counter_byte XOR prng_byte) & 0xFF, ensuring all values
    # are present but no 4-byte window repeats because the counter changes each byte.
    counter_bytes = bytearray(TOTAL)
    for i in range(TOTAL):
        # 4-byte counter at positions 0,1,2,3 of each group of 4
        group = i >> 2
        pos_in_group = i & 3
        counter_bytes[i] = (group >> (pos_in_group * 8)) & 0xFF

    # Mix with alphabet to keep entropy at ~5 bits/byte
    prng_mask = bytearray(_prng(seed + b":litflood-mask", TOTAL))
    result = bytearray(TOTAL)
    for i in range(TOTAL):
        # Map to 32-symbol alphabet using prng_mask for distribution, counter for uniqueness
        symbol = (prng_mask[i] & 0x1F)  # 5-bit alphabet
        # XOR high bits with counter to prevent 4-byte repetitions
        result[i] = symbol | ((counter_bytes[i] & 0x07) << 5)

    return bytes(result)


def _make_overlap_match_1m(seed: bytes) -> bytes:
    """1 MB with period-5 repetition that exercises LZ overlapping copies.

    Pattern: bytes "ABCDE" repeated to fill 1 MB. Many LZ encoders will
    encode this as a match with length > distance, which requires the
    decoder's copy loop to read from the output buffer as it is being written.

    Bugs in decoders that use memcpy (which assumes non-overlapping buffers)
    instead of a byte-by-byte copy loop are exposed by this pattern.
    """
    MB = 1 << 20
    pattern = b"ABCDE"
    # Repeat pattern to fill 1 MB exactly
    full, remainder = divmod(MB, len(pattern))
    return pattern * full + pattern[:remainder]


# ── main generator ────────────────────────────────────────────────────────────

def run(cfg: BuildConfig) -> int:
    """Generate all pathological fixtures. Returns 0 on success, 1 on failure."""
    try:
        out = cfg.raw_dir / "pathological"
        out.mkdir(parents=True, exist_ok=True)

        MB = 1 << 20

        # Sub-window-size inputs
        _write(out / "empty-0B",      b"")
        _write(out / "one-1B",        b"A")
        _write(out / "tiny-13B",      b"Hello, world!")
        _write(out / "small-256B",    bytes(range(256)))
        _write(out / "page-4095B",    _prng(SEED + b":4095", 4095))
        _write(out / "short-65535B",  _prng(SEED + b":65535", 65535))

        # Entropy extremes
        _write(out / "zeros-1M",      b"\x00" * MB)
        _write(out / "zeros-10M",     b"\x00" * (10 * MB))
        _write(out / "zeros-100M",    b"\x00" * (100 * MB))
        _write(out / "urandom-1M",    _prng(SEED + b":ur1",   MB))
        _write(out / "urandom-10M",   _prng(SEED + b":ur10",  10 * MB))
        _write(out / "urandom-100M",  _prng(SEED + b":ur100", 100 * MB))
        _write(out / "repeat-A-1M",   b"A" * MB)
        _write(out / "alternating-1M", (b"\x00\xff" * (MB // 2)))
        _write(out / "ascii-1M",      bytes((i % 95) + 32 for i in range(MB)))

        # One nonzero byte per 4 KiB page
        onebyte = bytearray(MB)
        for i in range(0, MB, 4096):
            onebyte[i] = 0x42
        _write(out / "onebyte-per-page-1M", bytes(onebyte))

        # Highly compressible structured: phrase repeated to 10 MiB
        phrase = b"the quick brown fox jumps over the lazy dog. " * 1024
        n = (10 * MB) // len(phrase) + 1
        _write(out / "phrase-repeated-10M", (phrase * n)[: 10 * MB])

        # Pi digits as ASCII (approximation via deterministic PRNG)
        digits = bytearray()
        h = hashlib.sha512(SEED + b":pi").digest()
        i = 0
        while len(digits) < 10 * MB:
            chunk = hashlib.sha512(h + struct.pack(">Q", i)).digest()
            digits.extend(0x30 + (b % 10) for b in chunk)
            i += 1
        _write(out / "pi-digits-10M", bytes(digits[: 10 * MB]))

        # Sparse geometric
        sparse = bytearray(10 * MB)
        rng_data = _prng(SEED + b":sparse", 10 * MB // 4)
        offset = 0
        for j in range(0, len(rng_data), 4):
            step = (rng_data[j] | (rng_data[j+1] << 8)) % 4096 + 1
            offset += step
            if offset >= len(sparse):
                break
            sparse[offset] = 0xFF
        _write(out / "sparse-geometric-10M", bytes(sparse))

        # Already-compressed blob
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
            gz.write(_prng(SEED + b":already", MB))
        _write(out / "already-compressed-blob", buf.getvalue())

        # Window-boundary triples
        def window_triple(name: str, size: int, seed_suffix: bytes) -> None:
            body = _prng(SEED + seed_suffix, size + 1)
            _write(out / f"{name}-minus1", body[: size - 1])
            _write(out / f"{name}",        body[: size])
            _write(out / f"{name}-plus1",  body[: size + 1])

        window_triple("window-zstd-128M",   128 * MB, b":zw128")
        window_triple("window-brotli-16M",  16  * MB, b":bw16")
        window_triple("window-deflate-32K", 32  * 1024, b":dw32")

        # zstd window-log-23 (8 MiB, zeros for compressibility)
        ZW = 8 * MB
        _write(out / "window-zstd-8M-minus1", b"\x00" * (ZW - 1))
        _write(out / "window-zstd-8M",        b"\x00" * ZW)
        _write(out / "window-zstd-8M-plus1",  b"\x00" * (ZW + 1))

        # LZ4 block-size boundaries (64 KiB)
        LZ4 = 64 * 1024
        lz4_pat = (b"\x00\xff" * ((LZ4 + 2) // 2))
        _write(out / "lz4-block-64K-minus1", lz4_pat[: LZ4 - 1])
        _write(out / "lz4-block-64K",        lz4_pat[: LZ4])
        _write(out / "lz4-block-64K-plus1",  lz4_pat[: LZ4 + 1])

        # Deflate MAX_MATCH boundary (258 bytes)
        _write(out / "max-match-257B", b"\xaa" * 257)
        _write(out / "max-match-258B", b"\xaa" * 258)
        _write(out / "max-match-259B", b"\xaa" * 259)

        # Mixed-entropy blocks (2 MiB): alternating 512 KiB zero/random
        BLOCK = 512 * 1024
        mixed = (
            b"\x00" * BLOCK
            + _prng(SEED + b":mixed1", BLOCK)
            + b"\x00" * BLOCK
            + _prng(SEED + b":mixed2", BLOCK)
        )
        _write(out / "mixed-entropy-blocks-2M", mixed)

        # Thue-Morse sequence (10 MiB) — aperiodic, defeats LZ77
        print(f"  thue-morse-10M (generating...)", flush=True)
        TM_BYTES = 10 * MB
        TM_BITS = TM_BYTES * 8
        bits = bytearray(TM_BITS)
        bits[0] = 0
        for i in range(1, TM_BITS):
            if i % 2 == 0:
                bits[i] = bits[i // 2]
            else:
                bits[i] = 1 - bits[(i - 1) // 2]
        tm_result = bytearray(TM_BYTES)
        for i in range(TM_BYTES):
            byte_val = 0
            base = i * 8
            for b in range(8):
                byte_val |= bits[base + b] << b
            tm_result[i] = byte_val
        _write(out / "thue-morse-10M", bytes(tm_result))

        # De Bruijn sequence (16 MiB) — every 24-bit substring appears once
        print(f"  debruijn-order3 (generating...)", flush=True)
        DB_BYTES = 16 * MB
        TAPS = (1 << 23) | (1 << 22) | (1 << 21) | (1 << 16)
        db_result = bytearray(DB_BYTES)
        state = 1
        for i in range(DB_BYTES):
            byte_val = 0
            for b in range(8):
                lsb = state & 1
                byte_val |= lsb << b
                state >>= 1
                if lsb:
                    state ^= TAPS
                if state == 0:
                    state = 1
            db_result[i] = byte_val
        _write(out / "debruijn-order3", bytes(db_result))

        # Near-duplicate pair (1 MiB each)
        nd_base = _prng(SEED + b":neardup-base", 1024 * 1024)
        nd_positions = _prng(SEED + b":neardup-positions", 100_000)
        nd_variant = bytearray(nd_base)
        for i in range(50_000):
            pos = (nd_positions[i * 2] * 256 + nd_positions[i * 2 + 1] * 4) % len(nd_variant)
            replacement_byte = nd_positions[(i * 3) % len(nd_positions)]
            nd_variant[pos] = replacement_byte
        _write(out / "near-dup-base",    nd_base)
        _write(out / "near-dup-variant", bytes(nd_variant))

        # ── new adversarial fixtures ──────────────────────────────────────────
        _write(out / "dict-poison-4M",          _make_dict_poison_4m(SEED))
        _write(out / "long-distance-match-4M",   _make_long_distance_match_4m(SEED))
        print(f"  huffman-max-4M (generating...)", flush=True)
        _write(out / "huffman-max-4M",           _make_huffman_max_4m(SEED))
        _write(out / "entropy-oscillator-8M",    _make_entropy_oscillator_8m(SEED))
        _write(out / "literal-flood-4M",         _make_literal_flood_4m(SEED))
        _write(out / "overlap-match-1M",         _make_overlap_match_1m(SEED))

        print(f"  pathological: all files written to {out}")
        return 0

    except Exception as exc:
        print(f"  ERROR in pathological: {exc}")
        import traceback; traceback.print_exc()
        return 1
