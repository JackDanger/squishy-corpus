"""Generate periodic-record-structured files for LZMA position-bit coverage.

LZMA's `pb` (position bits) and `lp` (literal position bits) parameters exploit
*positional periodicity*: a file structured as fixed-size records compresses
better with `pb = log2(record_size)` because the codec builds separate literal
models for each byte position within the period.

A file with no positional structure gets no benefit from pb/lp. These files
provide the controlled contrast needed to measure that benefit.

Design:
  Each file is a sequence of fixed-size records. Within each record:
  - Each byte position has its own IID distribution (tilted PMF, β per position).
  - Adjacent positions in the record use different β values, so the marginal
    entropy varies within the record (this makes positional context useful).
  - Across records, the same per-position PMFs are used (stationarity).

  A matching "shuffled" variant exists for each file: same per-position PMFs
  but bytes within each record are randomly permuted → no positional structure.
  The gap in compression ratio between structured and shuffled measures codec
  pb/lp sensitivity.

Corpus axes:
  period P:  4, 8, 16, 32  (bytes per record; pb = log2(P) for LZMA)
  H_profile: "gradient" (H varies linearly across positions),
              "block" (first half H=2, second half H=6)
  sizes:     256K, 4M
  seeds:     s0, s1, s2
  variant:   structured, shuffled

Seed computation:
  sha256(f"periodic:{P}:{profile}:{variant}:{size}:{rep}")[:8] → uint64
"""
from __future__ import annotations

import hashlib
import math
import random

from squishy.core.config import BuildConfig
from squishy.core.fs import write_bytes_atomic
from squishy.generators.calibrated import tilted_pmf

PERIODS: list[int] = [4, 8, 16, 32]
PROFILES: list[str] = ["gradient", "block"]
SIZES: list[tuple[str, int]] = [
    ("256K",  262144),
    ("4M",   4194304),
]
REPLICATES: list[str] = ["s0", "s1", "s2"]
VARIANTS: list[str] = ["structured", "shuffled"]


def _make_seed(tag: str) -> int:
    return int.from_bytes(hashlib.sha256(tag.encode()).digest()[:8], "big")


def _per_position_H(period: int, profile: str) -> list[float]:
    """Return H target for each byte position within the record."""
    if profile == "gradient":
        # Linear from 2.0 (position 0) to 7.0 (last position)
        return [2.0 + 5.0 * i / max(period - 1, 1) for i in range(period)]
    elif profile == "block":
        # First half: H=2.0, second half: H=6.0
        half = period // 2
        return [2.0] * half + [6.0] * (period - half)
    raise ValueError(f"unknown profile: {profile}")


def _build_per_position_pmfs(period: int, profile: str) -> list[list[float]]:
    return [tilted_pmf(H) for H in _per_position_H(period, profile)]


def generate_structured(size: int, period: int, profile: str, seed: int) -> bytes:
    """Generate a periodic-record file with position-dependent PMFs."""
    rng = random.Random(seed)
    pmfs = _build_per_position_pmfs(period, profile)
    alphabet = list(range(256))
    records = (size + period - 1) // period
    buf = bytearray()
    for _ in range(records):
        for pos_in_rec in range(period):
            if len(buf) >= size:
                break
            b = rng.choices(alphabet, weights=pmfs[pos_in_rec])[0]
            buf.append(b)
    return bytes(buf[:size])


def generate_shuffled(size: int, period: int, profile: str, seed: int) -> bytes:
    """Same per-position entropy but bytes within each record are scrambled.

    Each record is generated using the correct per-position PMFs (preserving
    marginal statistics) but then shuffled. This destroys positional structure
    while keeping the per-record entropy the same.
    """
    rng = random.Random(seed)
    pmfs = _build_per_position_pmfs(period, profile)
    alphabet = list(range(256))
    records = (size + period - 1) // period
    buf = bytearray()
    for _ in range(records):
        record = [rng.choices(alphabet, weights=pmfs[i])[0] for i in range(period)]
        rng.shuffle(record)  # destroy positional structure
        for b in record:
            if len(buf) >= size:
                break
            buf.append(b)
    return bytes(buf[:size])


def run(cfg: BuildConfig) -> int:
    """Generate periodic-record files. Returns 0 on success, 1 on failure."""
    try:
        out = cfg.raw_dir / "periodic"
        out.mkdir(parents=True, exist_ok=True)

        for size_label, size in SIZES:
            for period in PERIODS:
                for profile in PROFILES:
                    for variant in VARIANTS:
                        for rep in REPLICATES:
                            tag = f"periodic:{period}:{profile}:{variant}:{size}:{rep}"
                            seed = _make_seed(tag)
                            fname = f"{size_label}-P{period}-{profile}-{variant}-{rep}.bin"
                            path = out / fname
                            if path.exists():
                                print(f"  skip {fname} (exists)")
                                continue
                            if variant == "structured":
                                data = generate_structured(size, period, profile, seed)
                            else:
                                data = generate_shuffled(size, period, profile, seed)
                            write_bytes_atomic(path, data)
                            print(f"  {fname} ({len(data):,} bytes)")

        print(f"  periodic: written to {out}")
        return 0

    except Exception as exc:
        import traceback
        print(f"  ERROR in periodic: {exc}")
        traceback.print_exc()
        return 1
