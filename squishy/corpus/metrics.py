"""Core corpus metrics: byte entropy, LZ77 parse, sliding-window σ_H, NCD."""
from __future__ import annotations

import concurrent.futures
import math
import random
import statistics
import subprocess

import numpy as np

LZ_MIN_LEN: int = 4
LZ_WINDOW: int = 32768   # caps both distance and match length (research parser)
NCD_LEVEL: int = 19
SUBSAMPLE_N: int = 50
SUBSAMPLE_SIZE: int = 1024 * 1024  # 1 MB
SUBSAMPLE_STRATA: int = 10         # stratified subsampling: file split into this many strata

# Empirical M_greedy floor for IID (construction M=0) data.
# Measured from the M=0 calibrated files in the corpus at each size tier.
# Size-specific because birthday-paradox match rates differ between 256K and 4M.
# H=1.7 is anchored directly (not interpolated) since that H value appears in
# the corpus grid and the H=1.7 empirical floor deviates significantly from the
# linear interpolation between H=1.0 and H=2.0 (0.9934 interpolated vs 0.9952
# measured at 4M — a 0.0018 error that inflates to +0.27 in normalized space).
#
# RELIABILITY: M_greedy_norm is only a useful signal when (1 - floor) is large
# enough to dominate measurement noise.  Use m_norm_reliable() to check.
# At H<4 (floor > 0.70), the normalized range is < 30%; at H<3 (floor > 0.93),
# it is < 7%.  For calibrated files at H<4, prefer M_target for cell binning.
_M_FLOOR_TABLES: dict[int, list[tuple[float, float]]] = {
    262144: [   # 256K — measured from M=0 calibrated files (mean of s0/s1/s2)
        (0.0, 1.000),
        (1.0, 0.9986),
        (1.7, 0.9941),
        (2.0, 0.9892),
        (3.0, 0.9332),
        (4.0, 0.6950),
        (5.0, 0.2360),
        (6.0, 0.0228),
        (7.0, 0.0014),
        (8.0, 0.000),
    ],
    4194304: [  # 4M — measured from M=0 calibrated files (mean of s0/s1/s2)
        (0.0, 1.000),
        (1.0, 0.9989),
        (1.7, 0.9952),
        (2.0, 0.9911),
        (3.0, 0.9408),
        (4.0, 0.7140),
        (5.0, 0.2479),
        (6.0, 0.0249),
        (7.0, 0.0015),
        (8.0, 0.000),
    ],
}
# Reliability threshold: floor below this → normalized range large enough to use.
_M_NORM_RELIABLE_FLOOR_THRESHOLD: float = 0.90


def _select_floor_table(size_bytes: int) -> list[tuple[float, float]]:
    """Select the floor table for the given file size (log-distance nearest)."""
    if not _M_FLOOR_TABLES:
        return []
    return min(
        _M_FLOOR_TABLES.values(),
        key=lambda _tbl: abs(math.log(max(size_bytes, 1) / _find_table_size(size_bytes))),
    )


def _find_table_size(size_bytes: int) -> int:
    """Return the key in _M_FLOOR_TABLES closest to size_bytes in log space."""
    return min(_M_FLOOR_TABLES, key=lambda s: abs(math.log(max(s, 1)) - math.log(max(size_bytes, 1))))


def m_greedy_floor(h: float, size_bytes: int = 4194304) -> float:
    """Linearly-interpolated IID floor for M_greedy at entropy H bits/byte.

    size_bytes selects the calibration table (256K or 4M).  For other sizes
    the nearest table in log-byte space is used; calibration is only measured
    at 256K and 4M and extrapolation is unvalidated.
    """
    table = _M_FLOOR_TABLES[_find_table_size(size_bytes)]
    if h <= table[0][0]:
        return table[0][1]
    if h >= table[-1][0]:
        return table[-1][1]
    for i in range(len(table) - 1):
        h0, f0 = table[i]
        h1, f1 = table[i + 1]
        if h0 <= h <= h1:
            t = (h - h0) / (h1 - h0)
            return f0 + t * (f1 - f0)
    return 0.0


def m_norm_reliable(h: float, size_bytes: int = 4194304) -> bool:
    """Return True when M_greedy_norm is a reliable signal at this (H, size).

    False (unreliable) when the IID floor is ≥ 0.90, leaving < 10% dynamic
    range.  In that regime measurement noise dominates.  For calibrated files,
    use M_target instead of M_greedy_norm for cell binning.
    """
    return m_greedy_floor(h, size_bytes) < _M_NORM_RELIABLE_FLOOR_THRESHOLD


def m_greedy_norm(m_greedy: float, h: float, size_bytes: int = 4194304) -> float:
    """Normalize M_greedy to remove the IID-floor instrument artifact.

    M_greedy_norm = (M_greedy - floor(H, size)) / (1 - floor(H, size))

    Returns 0 when M_greedy equals the IID floor (no real repetition),
    approaches 1 for highly repetitive content at a given entropy.
    Returns NaN when floor == 1.0 (H too low for normalization to be defined)
    or when M_greedy < floor (measurement noise below the floor).

    Reliability: only meaningful when m_norm_reliable(h, size_bytes) is True.
    At H<4 the normalized range is < 30%; at H<3 it is < 7%.
    """
    floor = m_greedy_floor(h, size_bytes)
    if floor >= 1.0:
        return float("nan")
    return max(0.0, (m_greedy - floor) / (1.0 - floor))


# ── Entropy ──────────────────────────────────────────────────────────────────

def byte_entropy(data: bytes | np.ndarray) -> float:
    """Shannon entropy of the byte distribution, bits/byte.

    Uses numpy for O(n) speed — ~10x faster than a pure-Python loop on large files.
    """
    if len(data) == 0:
        return 0.0
    arr = np.frombuffer(data, dtype=np.uint8) if isinstance(data, (bytes, bytearray)) else data
    counts = np.bincount(arr, minlength=256).astype(np.float64)
    probs = counts[counts > 0] / len(arr)
    return float(-np.sum(probs * np.log2(probs)))


# ── LZ77 parse ───────────────────────────────────────────────────────────────

def lz77_parse(data: bytes, min_len: int = LZ_MIN_LEN,
               window: int = LZ_WINDOW) -> list[tuple[int, int, int]]:
    """Greedy LZ77 parse. Returns list of (pos, distance, length) for each match.

    Correctness invariants:
    - data[pos:pos+length] == data[pos-dist:pos-dist+length] for every match
    - dist ∈ [1, window], length ∈ [min_len, window]
    - positions are non-decreasing; pos advances by match_len after each match

    Match length is capped at `window` (same as distance bound). This differs from
    real codecs (zstd caps at ~131K), but is sufficient for M/L estimation.

    Hash-table updates inside the match loop ("full insert") enable run-length
    matches like dist=1, len=N on all-same-byte data. This is O(match_len) per
    match but produces the most faithful greedy parse.
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


def lz_stats(data: bytes) -> tuple[float, float, float, float]:
    """Return (M_greedy, L_median, L_p90, L_geomean) from a single greedy parse.

    Returns (0.0, nan, nan, nan) when there are no matches (M_greedy=0 is
    a real value; L_* are undefined when there are no matches).
    """
    n = len(data)
    if n < LZ_MIN_LEN:
        nan = float("nan")
        return 0.0, nan, nan, nan
    matches = lz77_parse(data)
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


# ── Sliding-window σ_H ───────────────────────────────────────────────────────

def sigma_h(data: bytes, window_size: int) -> float:
    """Standard deviation of byte entropy over non-overlapping windows of given size."""
    entropies = [
        byte_entropy(data[i:i + window_size])
        for i in range(0, len(data) - window_size + 1, window_size)
    ]
    return statistics.stdev(entropies) if len(entropies) >= 2 else float("nan")


# ── NCD halves ───────────────────────────────────────────────────────────────

def _zstd_compressed_size(data: bytes, level: int = NCD_LEVEL) -> int:
    result = subprocess.run(
        ["zstd", f"-{level}", "--no-progress", "-c", "-"],
        input=data, capture_output=True, check=True,
    )
    return len(result.stdout)


def ncd_halves(data: bytes) -> float:
    """NCD(first_half, second_half) under zstd-NCD_LEVEL (Cilibrasi-Vitányi 2005).

    Near 0: halves compress together much better than separately (statistically similar).
    Near 1: no cross-half compression benefit (information-theoretically independent).
    Clipped to [0, 1]: values slightly above 1 occur with small inputs due to
    compressor frame overhead exceeding any cross-half savings.

    The three compressions run in parallel (independent zstd subprocesses).
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


# ── Subsample CIs ────────────────────────────────────────────────────────────

def _percentile(arr: list[float], p: float) -> float:
    valid = sorted(x for x in arr if not math.isnan(x))
    if not valid:
        return float("nan")
    idx = (len(valid) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(valid) - 1)
    return valid[lo] + (valid[hi] - valid[lo]) * (idx - lo)


def subsample_cis(data: bytes, n: int = SUBSAMPLE_N,
                  ss: int = SUBSAMPLE_SIZE, seed: int = 42,
                  strata: int = SUBSAMPLE_STRATA) -> dict[str, float]:
    """Variability CIs for H, M_greedy, L_median via stratified subsampling.

    Divides the file into `strata` equal-length regions and draws n//strata
    random 1 MB windows from each, capturing locality effects across the file.
    This is a subsampling estimator (Politis-Romano), not a resampling bootstrap —
    it measures how much the statistics vary across different regions of the file.

    A large CI width means the metric varies significantly across the file,
    which is itself informative about heterogeneity.

    H_vals, M_vals, L_vals may contain NaN (if a 1MB chunk has zero LZ matches);
    _percentile filters NaN independently for each metric.
    """
    data_len = len(data)
    ss = min(ss, data_len)
    nan = float("nan")
    nan_result = dict(H_ci95_lo=nan, H_ci95_hi=nan, M_ci95_lo=nan, M_ci95_hi=nan,
                      L_ci95_lo=nan, L_ci95_hi=nan, L_ci_rel=nan)
    if data_len < LZ_MIN_LEN:
        return nan_result
    # CIs require each stratum to contain at least one distinct ss-size window.
    # Below strata * ss all strata collapse to the same window → false-tight CIs.
    if data_len < strata * ss:
        return nan_result

    rng = random.Random(seed)
    per_stratum = max(1, n // strata)
    H_vals, M_vals, L_vals = [], [], []

    for s in range(strata):
        # Each stratum spans [stratum_start, stratum_end)
        stratum_start = s * data_len // strata
        stratum_end = (s + 1) * data_len // strata
        # Adjust so a full ss-size window fits within this stratum
        avail = stratum_end - stratum_start - ss
        for _ in range(per_stratum):
            if avail > 0:
                start = stratum_start + rng.randint(0, avail)
            else:
                start = stratum_start
            chunk = data[start:start + ss]
            H_vals.append(byte_entropy(chunk))
            mg, lm, _, _ = lz_stats(chunk)
            M_vals.append(mg)
            L_vals.append(lm)  # may be NaN if no matches in chunk

    H_lo = _percentile(H_vals, 0.025)
    H_hi = _percentile(H_vals, 0.975)
    M_lo = _percentile(M_vals, 0.025)
    M_hi = _percentile(M_vals, 0.975)
    L_lo = _percentile(L_vals, 0.025)
    L_hi = _percentile(L_vals, 0.975)
    l_center = _percentile(L_vals, 0.5)
    l_ci_rel = (L_hi - L_lo) / 2.0 / l_center if l_center > 0 else nan

    return dict(H_ci95_lo=H_lo, H_ci95_hi=H_hi,
                M_ci95_lo=M_lo, M_ci95_hi=M_hi,
                L_ci95_lo=L_lo, L_ci95_hi=L_hi,
                L_ci_rel=l_ci_rel)
