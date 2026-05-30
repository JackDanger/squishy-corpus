"""Version-stable, reproducible PRNG for synthetic corpus generation.

Python's `random.Random` stream is NOT guaranteed stable across CPython versions,
which would break "regenerate-from-seed → identical sha256" for the
synthesize-on-demand corpus files. This is a counter-based SHAKE-256 stream:
deterministic, portable, and language-agnostic (anyone can reimplement it from
the spec below and get identical bytes).

Spec (reference vectors in tests/test_prng.py):
  block(i) = SHAKE-256( seed_bytes ‖ uint64_be(i) ) → 64 bytes
  the byte stream is block(0) ‖ block(1) ‖ ...
  seed_bytes = the seed string UTF-8 encoded, or int seeds as their decimal ASCII.

All higher-level draws (floats, ints, choices) are defined on top of this byte
stream so they are fully specified by (seed, call sequence).
"""
from __future__ import annotations

import hashlib
import struct


class SquishyPRNG:
    """Deterministic SHAKE-256 counter-stream PRNG (version-stable, portable)."""

    BLOCK = 64

    def __init__(self, seed: str | int | bytes) -> None:
        if isinstance(seed, bytes):
            self._seed = seed
        elif isinstance(seed, int):
            self._seed = str(seed).encode()
        else:
            self._seed = str(seed).encode()
        self._counter = 0
        self._buf = b""

    def _refill(self) -> None:
        block = hashlib.shake_256(self._seed + struct.pack(">Q", self._counter)).digest(self.BLOCK)
        self._counter += 1
        self._buf += block

    def randbytes(self, n: int) -> bytes:
        while len(self._buf) < n:
            self._refill()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def random(self) -> float:
        """Uniform float in [0, 1) from 53 bits (IEEE-double precision)."""
        x = int.from_bytes(self.randbytes(8), "big") >> 11   # top 53 bits
        return x / (1 << 53)

    def randint(self, lo: int, hi: int) -> int:
        """Uniform integer in [lo, hi] inclusive, rejection-sampled (unbiased)."""
        span = hi - lo + 1
        if span <= 0:
            raise ValueError("empty range")
        k = (span - 1).bit_length()
        nbytes = (k + 7) // 8 or 1
        mask = (1 << k) - 1
        while True:
            v = int.from_bytes(self.randbytes(nbytes), "big") & mask
            if v < span:
                return lo + v

    def choice(self, seq):
        return seq[self.randint(0, len(seq) - 1)]

    def choices(self, population, weights, k: int = 1) -> list:
        """Weighted draw with replacement (cumulative + random())."""
        total = float(sum(weights))
        cum, acc = [], 0.0
        for w in weights:
            acc += w
            cum.append(acc / total)
        out = []
        for _ in range(k):
            r = self.random()
            lo, hi = 0, len(cum) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if r < cum[mid]:
                    hi = mid
                else:
                    lo = mid + 1
            out.append(population[lo])
        return out
