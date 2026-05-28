"""Core corpus metrics: byte entropy, LZ77 parse, NCD."""
from __future__ import annotations

import concurrent.futures
import math
import subprocess

import numpy as np

LZ_MIN_LEN: int = 4
LZ_WINDOW_32K: int = 32768
LZ_WINDOW_256K: int = 262144
NCD_LEVEL: int = 19


# ── Entropy ──────────────────────────────────────────────────────────────────

def byte_entropy(data: bytes | np.ndarray) -> float:
    """Shannon entropy of the byte distribution, bits/byte."""
    if len(data) == 0:
        return 0.0
    arr = np.frombuffer(data, dtype=np.uint8) if isinstance(data, (bytes, bytearray)) else data
    counts = np.bincount(arr, minlength=256).astype(np.float64)
    probs = counts[counts > 0] / len(arr)
    return float(-np.sum(probs * np.log2(probs)))


# ── LZ77 parse ───────────────────────────────────────────────────────────────

def lz77_parse(data: bytes, min_len: int = LZ_MIN_LEN,
               window: int = LZ_WINDOW_32K) -> list[tuple[int, int, int]]:
    """Greedy LZ77 parse. Returns list of (pos, distance, length) for each match.

    Correctness invariants:
    - data[pos:pos+length] == data[pos-dist:pos-dist+length] for every match
    - dist ∈ [1, window], length ∈ [min_len, window]
    - positions are non-decreasing; pos advances by match_len after each match

    Hash-table updates inside the match loop ("full insert") enable run-length
    matches like dist=1, len=N on all-same-byte data.
    """
    n = len(data)
    pos_table: dict[bytes, int] = {}
    matches: list[tuple[int, int, int]] = []
    pos = 0
    while pos < n:
        if pos + min_len > n:
            pos += 1
            continue
        key = data[pos:pos + min_len]
        prev = pos_table.get(key)
        if prev is not None and pos - prev <= window:
            dist = pos - prev
            match_len = min_len
            src = prev
            while (pos + match_len < n
                   and match_len < window
                   and data[src + match_len] == data[pos + match_len]):
                match_len += 1
            matches.append((pos, dist, match_len))
            for i in range(match_len):
                if pos + i + min_len <= n:
                    pos_table[data[pos + i:pos + i + min_len]] = pos + i
            pos += match_len
        else:
            pos_table[key] = pos
            pos += 1
    return matches


def lz_stats(data: bytes, window: int = LZ_WINDOW_32K) -> tuple[float, float, float, float]:
    """Return (M_greedy, L_median, L_p90, L_geomean) from a single greedy parse.

    Returns (0.0, nan, nan, nan) when there are no matches.
    """
    n = len(data)
    if n < LZ_MIN_LEN:
        nan = float("nan")
        return 0.0, nan, nan, nan
    matches = lz77_parse(data, window=window)
    if not matches:
        nan = float("nan")
        return 0.0, nan, nan, nan
    lengths = sorted(m[2] for m in matches)
    k = len(lengths)
    m_greedy = sum(lengths) / n
    l_median = (float(lengths[k // 2]) if k % 2 == 1
                else (lengths[k // 2 - 1] + lengths[k // 2]) / 2.0)
    l_p90 = float(lengths[int(0.9 * (k - 1))])
    l_geomean = math.exp(sum(math.log(l) for l in lengths) / k)
    return m_greedy, l_median, l_p90, l_geomean


# ── NCD halves ───────────────────────────────────────────────────────────────

def _zstd_compressed_size(data: bytes, level: int = NCD_LEVEL) -> int:
    result = subprocess.run(
        ["zstd", f"-{level}", "--no-progress", "-c", "-"],
        input=data, capture_output=True, check=True,
    )
    return len(result.stdout)


def ncd_halves(data: bytes) -> float:
    """NCD(first_half, second_half) under zstd-NCD_LEVEL (Cilibrasi-Vitányi 2005).

    Near 0: halves compress together much better than separately (stationary).
    Near 1: no cross-half compression benefit (independent halves).
    Clipped to [0, 1].
    """
    mid = len(data) // 2
    if mid < 4096:
        return float("nan")
    a, b = data[:mid], data[mid:]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        fa = pool.submit(_zstd_compressed_size, a)
        fb = pool.submit(_zstd_compressed_size, b)
        fab = pool.submit(_zstd_compressed_size, a + b)
        ca, cb, cab = fa.result(), fb.result(), fab.result()
    return max(0.0, min(1.0, (cab - min(ca, cb)) / max(ca, cb)))
