"""LZ77-based file measurement for the R×D×M corpus grid.

Three measurements, all computed from raw bytes with no codec:

  R  — local repetition: LZ77 match coverage with 64 KB window
  D  — wide-window bonus: additional coverage gained with 4 MB vs 64 KB window
  M  — sequential texture: fraction of per-byte entropy eliminated by 1-byte context
"""
from __future__ import annotations

import collections
import math

SAMPLE_BYTES = 4 * 1024 * 1024   # 4 MB sample cap
W_LOCAL      = 64 * 1024          # 64 KB  — bzip2 / deflate territory
W_WIDE       = 4  * 1024 * 1024   # 4 MB   — zstd-19 territory
MIN_MATCH    = 4                   # minimum back-reference length


def _lz77_match_fraction(data: bytes | memoryview, window: int) -> float:
    """Greedy single-entry-per-hash LZ77. Returns matched_bytes / len(data)."""
    n = len(data)
    if n < MIN_MATCH + 1:
        return 0.0
    table: dict[int, int] = {}
    matched = 0
    i = 0
    while i < n - MIN_MATCH:
        h = data[i] | (data[i+1] << 8) | (data[i+2] << 16) | (data[i+3] << 24)
        prev = table.get(h)
        table[h] = i
        if prev is not None:
            off = i - prev
            if 0 < off <= window:
                j = MIN_MATCH
                limit = min(n - i, n - prev, 65536)
                while j < limit and data[prev + j] == data[i + j]:
                    j += 1
                matched += j
                i += j
                continue
        i += 1
    return matched / n


def measure(data: bytes, sample: int = SAMPLE_BYTES) -> tuple[float, float, float]:
    """Return (R, D, M) for up to `sample` bytes of `data`.

    R  in [0, 1]   — local-window match fraction
    D  in [0, 1]   — wide-window bonus  (R_wide − R_local, clamped ≥ 0)
    M  in [0, 1]   — memory / sequential texture
    """
    chunk = data[:sample]
    r_local = _lz77_match_fraction(chunk, W_LOCAL)
    r_wide  = _lz77_match_fraction(chunk, W_WIDE)
    d       = max(0.0, r_wide - r_local)
    m       = _memory(chunk)
    return r_local, d, m


def _memory(data: bytes) -> float:
    """1 − H(X_t | X_{t-1}) / H(X_t).  Returns 0 for constant or empty input.

    Requires ≥ ~256 KB for reliable estimates: the bigram alphabet has 256²=65 536
    categories, so small samples produce sparse bigram distributions with downward-
    biased entropy, inflating M toward 0.5.  At 4 MB (default sample), bias < 0.01.
    """
    n = len(data)
    if n < 2:
        return 0.0
    c0 = collections.Counter(data)
    h0 = -sum(c / n * math.log2(c / n) for c in c0.values())
    if h0 == 0.0:
        return 0.0
    m = n - 1
    c1 = collections.Counter(zip(data, data[1:]))
    h_joint = -sum(c / m * math.log2(c / m) for c in c1.values())
    h_cond  = h_joint - h0
    return max(0.0, 1.0 - h_cond / h0)
