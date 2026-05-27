#!/usr/bin/env python3
"""Generate all pathological raw inputs deterministically.

Inputs are designed to exercise specific decoder code paths:
  - sub-window-size inputs (0..65535 bytes) -> stored-block fallback
  - window-boundary sizes -> off-by-one in window-wrap logic
  - entropy extremes -> RLE fast-path, literal-only blocks, incompressible
  - already-compressed -> codec faces unyielding entropy
"""
from __future__ import annotations
import hashlib, os, struct, sys
from pathlib import Path

SEED = b"jackdanger-corpus-v1"

def prng(seed: bytes, n: int) -> bytes:
    """Deterministic, fast PRNG: SHA-256 in counter mode."""
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(seed + struct.pack(">Q", i)).digest()
        i += 1
    return bytes(out[:n])

def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.rename(path)

def main(outdir: str) -> None:
    out = Path(outdir)

    # Sub-window-size inputs — most decoder bugs live near these sizes
    write(out / "empty-0B",      b"")
    write(out / "one-1B",        b"A")
    write(out / "tiny-13B",      b"Hello, world!")
    write(out / "small-256B",    bytes(range(256)))
    write(out / "page-4095B",    prng(SEED + b":4095", 4095))
    write(out / "short-65535B",  prng(SEED + b":65535", 65535))

    # Entropy extremes
    MB = 1 << 20
    write(out / "zeros-1M",      b"\x00" * MB)
    write(out / "zeros-10M",     b"\x00" * (10 * MB))
    write(out / "zeros-100M",    b"\x00" * (100 * MB))
    write(out / "urandom-1M",    prng(SEED + b":ur1",   MB))
    write(out / "urandom-10M",   prng(SEED + b":ur10",  10 * MB))
    write(out / "urandom-100M",  prng(SEED + b":ur100", 100 * MB))
    write(out / "repeat-A-1M",   b"A" * MB)
    write(out / "alternating-1M", (b"\x00\xff" * (MB // 2)))
    write(out / "ascii-1M",      bytes((i % 95) + 32 for i in range(MB)))

    # One nonzero byte per 4 KiB page — exercises window-edge behaviour
    onebyte = bytearray(MB)
    for i in range(0, MB, 4096):
        onebyte[i] = 0x42
    write(out / "onebyte-per-page-1M", bytes(onebyte))

    # Highly compressible structured: phrase repeated to 10 MiB
    phrase = b"the quick brown fox jumps over the lazy dog. " * 1024  # ~46 KiB
    n = (10 * MB) // len(phrase) + 1
    write(out / "phrase-repeated-10M", (phrase * n)[: 10 * MB])

    # Pi digits as ASCII (approximation via deterministic PRNG of digits)
    # Real pi is not random but this gives ascii-digit-only entropy.
    digits = bytearray()
    h = hashlib.sha512(SEED + b":pi").digest()
    i = 0
    while len(digits) < 10 * MB:
        chunk = hashlib.sha512(h + struct.pack(">Q", i)).digest()
        digits.extend(0x30 + (b % 10) for b in chunk)  # ASCII '0' + digit
        i += 1
    write(out / "pi-digits-10M", bytes(digits[: 10 * MB]))

    # Sparse geometric: most bytes 0, rare 0xFF in geometric distribution
    sparse = bytearray(10 * MB)
    rng = prng(SEED + b":sparse", 10 * MB // 4)
    offset = 0
    for j in range(0, len(rng), 4):
        # geometric-ish step size: 1..256
        step = (rng[j] | (rng[j+1] << 8)) % 4096 + 1
        offset += step
        if offset >= len(sparse):
            break
        sparse[offset] = 0xFF
    write(out / "sparse-geometric-10M", bytes(sparse))

    # Already-compressed blob: gzip the urandom-1M to feed an entropy wall
    import gzip, io
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(prng(SEED + b":already", MB))
    write(out / "already-compressed-blob", buf.getvalue())

    # Window-boundary inputs (exact window size and ±1)
    # zstd default decoder window log can be 27 (128 MiB); we use 27 here as
    # the canonical test point. Smaller window-log codecs (deflate 32 KiB,
    # brotli 16 MiB) also get triples.
    def window_triple(name: str, size: int, seed_suffix: bytes) -> None:
        body = prng(SEED + seed_suffix, size + 1)
        write(out / f"{name}-minus1", body[: size - 1])
        write(out / f"{name}",        body[: size])
        write(out / f"{name}-plus1",  body[: size + 1])

    window_triple("window-zstd-128M",   128 * MB, b":zw128")
    window_triple("window-brotli-16M",  16  * MB, b":bw16")
    window_triple("window-deflate-32K", 32  * 1024, b":dw32")

    # --- New generators ---

    # Window-boundary triples: zstd window-log-23 (8 MiB default at -3)
    # zstd's default window at -3 is 2^23 = 8,388,608 bytes.
    # Use zeros (highly compressible) so the window boundary is the variable.
    ZW = 8 * MB  # 8,388,608
    write(out / "window-zstd-8M-minus1", b"\x00" * (ZW - 1))
    write(out / "window-zstd-8M",        b"\x00" * ZW)
    write(out / "window-zstd-8M-plus1",  b"\x00" * (ZW + 1))

    # LZ4 block-size boundaries (64 KiB = 65,536 bytes)
    # LZ4 decoders have had bugs at block boundaries.
    # Content: alternating \x00\xFF — low entropy but not pure zeros.
    LZ4 = 64 * 1024  # 65,536
    lz4_pat = (b"\x00\xff" * ((LZ4 + 2) // 2))
    write(out / "lz4-block-64K-minus1", lz4_pat[: LZ4 - 1])
    write(out / "lz4-block-64K",        lz4_pat[: LZ4])
    write(out / "lz4-block-64K-plus1",  lz4_pat[: LZ4 + 1])

    # Deflate MAX_MATCH boundary inputs (258 bytes)
    # MAX_MATCH = 258 is the longest match deflate can encode.
    # Related to zlib CVE-2018-25032.
    write(out / "max-match-257B", b"\xaa" * 257)
    write(out / "max-match-258B", b"\xaa" * 258)
    write(out / "max-match-259B", b"\xaa" * 259)

    # Mixed-entropy blocks (2 MiB): alternating 512 KiB zero/random blocks.
    # Tests adaptive encoders that adjust strategy mid-stream.
    BLOCK = 512 * 1024
    mixed = (
        b"\x00" * BLOCK
        + prng(SEED + b":mixed1", BLOCK)
        + b"\x00" * BLOCK
        + prng(SEED + b":mixed2", BLOCK)
    )
    write(out / "mixed-entropy-blocks-2M", mixed)

    # Thue-Morse sequence (10 MiB)
    # Aperiodic sequence that defeats LZ77 match finders.
    # Uses the recurrence: T[2n]=T[n], T[2n+1]=1-T[n].
    print("generating thue-morse-10M ...", file=sys.stderr)
    TM_BYTES = 10 * MB
    TM_BITS = TM_BYTES * 8
    bits = bytearray(TM_BITS)  # 0 or 1 per element
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
    write(out / "thue-morse-10M", bytes(tm_result))

    # De Bruijn sequence (16 MiB) using a 24-bit Galois LFSR.
    # A 24-bit maximal LFSR produces 2^24-1 = 16,777,215 bits ~= 16 MiB.
    # Every 24-bit substring appears exactly once, defeating match finders.
    # Primitive polynomial: x^24 + x^23 + x^22 + x^17 + 1
    print("generating debruijn-order3 ...", file=sys.stderr)
    DB_BYTES = 16 * MB
    DB_BITS = DB_BYTES * 8  # 134,217,728 bits
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
                state = 1  # prevent all-zero lockup (wrap around)
        db_result[i] = byte_val
    write(out / "debruijn-order3", bytes(db_result))

    # Near-duplicate pair (1 MiB each)
    # Base + variant with ~5% bytes flipped at deterministic positions.
    # Tests zstd --long, brotli long-window, solid-mode archivers.
    nd_base = prng(SEED + b":neardup-base", 1024 * 1024)
    nd_positions = prng(SEED + b":neardup-positions", 100_000)
    nd_variant = bytearray(nd_base)
    for i in range(50_000):
        pos = (nd_positions[i * 2] * 256 + nd_positions[i * 2 + 1] * 4) % len(nd_variant)
        replacement_byte = nd_positions[(i * 3) % len(nd_positions)]
        nd_variant[pos] = replacement_byte
    write(out / "near-dup-base",    nd_base)
    write(out / "near-dup-variant", bytes(nd_variant))

    print(f"wrote pathological inputs to {out}", file=sys.stderr)

if __name__ == "__main__":
    main(sys.argv[1])
