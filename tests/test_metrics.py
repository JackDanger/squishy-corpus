"""Tests for squishy.corpus.metrics.

All test data is programmatically generated — no downloaded corpus files required.
"""
import math
import shutil

import pytest

from squishy.corpus.metrics import (
    byte_entropy,
    lz77_parse,
    lz_stats,
    sigma_h,
    ncd_halves,
    subsample_cis,
    m_greedy_floor,
    m_greedy_norm,
    m_norm_reliable,
    LZ_MIN_LEN,
    LZ_WINDOW,
)


# ── byte_entropy ─────────────────────────────────────────────────────────────

def test_entropy_empty():
    assert byte_entropy(b"") == 0.0


def test_entropy_single_symbol():
    assert byte_entropy(b"\x00" * 1024) == 0.0


def test_entropy_two_equiprobable_symbols():
    data = bytes([i % 2 for i in range(1024)])
    assert abs(byte_entropy(data) - 1.0) < 1e-6


def test_entropy_uniform_256():
    # All 256 byte values equally likely → H = log2(256) = 8
    data = bytes(range(256)) * 4
    assert abs(byte_entropy(data) - 8.0) < 1e-4


def test_entropy_nonnegative():
    import random
    rng = random.Random(0)
    data = bytes(rng.getrandbits(8) for _ in range(4096))
    assert byte_entropy(data) >= 0.0


# ── lz77_parse ───────────────────────────────────────────────────────────────

def test_parse_empty():
    assert lz77_parse(b"") == []


def test_parse_too_short():
    # Shorter than min_len — can never match
    assert lz77_parse(b"AB") == []


def test_parse_no_repetition():
    # bytes(range(256)): every 4-byte key is unique within 256 bytes,
    # so the greedy parse finds no matches.
    data = bytes(range(256))
    matches = lz77_parse(data)
    assert matches == [], f"Expected no matches, got {len(matches)}"


def test_parse_periodic_has_matches():
    # Greedy on "ABCD"*256: the parser finds one match at pos=4 dist=4 covering
    # the rest of the file — greedy extends as far as possible, so M_greedy is
    # very high even though match count is small.
    data = b"ABCD" * 256
    matches = lz77_parse(data)
    assert len(matches) >= 1
    total_covered = sum(l for _, _, l in matches)
    assert total_covered / len(data) > 0.9  # > 90% of bytes are covered


def test_parse_match_correctness():
    data = b"hello world " * 64
    matches = lz77_parse(data)
    for pos, dist, length in matches:
        assert length >= LZ_MIN_LEN, "Match shorter than min_len"
        assert 1 <= dist <= LZ_WINDOW, "Distance out of window"
        src = pos - dist
        assert data[pos:pos + length] == data[src:src + length], (
            f"Match at pos={pos} dist={dist} length={length} is incorrect"
        )


def test_parse_window_bound():
    # Fill buffer: first half random, second half identical to first
    first = bytes(range(128)) * 256   # 32768 bytes, no LZ matches in first half
    second = bytes(range(128)) * 256  # identical, but > window from start
    data = first + second
    matches = lz77_parse(data)
    for _, dist, _ in matches:
        assert dist <= LZ_WINDOW


def test_parse_no_double_counting():
    # M_greedy must be in [0, 1] on arbitrary data
    import random
    rng = random.Random(42)
    data = bytes(rng.getrandbits(8) for _ in range(8192))
    m, _, _, _ = lz_stats(data)
    assert 0.0 <= m <= 1.0


def test_parse_all_same_byte():
    # All-zeros: every position matches position-1 at distance 1, growing match
    # M_greedy should be close to 1; L_median should be large (window-limited)
    data = b"\x00" * 4096
    m, l_med, l_p90, l_geom = lz_stats(data)
    assert m > 0.9, "All-zeros should have very high M_greedy"
    assert l_med >= LZ_MIN_LEN


def test_parse_positions_non_decreasing():
    data = b"the quick brown fox jumps over the lazy dog " * 32
    matches = lz77_parse(data)
    positions = [pos for pos, _, _ in matches]
    assert positions == sorted(positions), "Match positions must be non-decreasing"


# ── lz_stats ─────────────────────────────────────────────────────────────────

def test_lz_stats_no_matches():
    data = bytes(range(256))  # no repetition
    m, l_med, l_p90, l_geom = lz_stats(data)
    assert m == 0.0
    assert math.isnan(l_med)
    assert math.isnan(l_p90)
    assert math.isnan(l_geom)


def test_lz_stats_periodic():
    data = b"XYZW" * 512
    m, l_med, l_p90, l_geom = lz_stats(data)
    assert m > 0.8
    assert l_med >= LZ_MIN_LEN
    assert l_p90 >= l_med
    assert l_geom > 0


# ── sigma_h ──────────────────────────────────────────────────────────────────

def test_sigma_h_uniform_is_zero():
    # Single-byte file: all windows have H=0, stdev=0
    data = b"\x00" * 4096
    assert sigma_h(data, 256) == 0.0


def test_sigma_h_heterogeneous_positive():
    # First half all-zeros (H=0), second half uniform random (H≈8)
    import random
    rng = random.Random(7)
    data = b"\x00" * 2048 + bytes(rng.getrandbits(8) for _ in range(2048))
    s = sigma_h(data, 512)
    assert s > 1.0, "Heterogeneous data should have high sigma_H"


def test_sigma_h_too_few_windows():
    # Less than 2 windows → nan
    data = b"\x00" * 100
    result = sigma_h(data, 512)
    assert math.isnan(result)


# ── ncd_halves ───────────────────────────────────────────────────────────────

@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_ncd_identical_halves_near_zero():
    # A file whose two halves are identical should compress together much better.
    half = bytes(range(256)) * 32  # 8 KB
    data = half + half
    ncd = ncd_halves(data)
    assert 0.0 <= ncd <= 1.0
    # The two halves are identical, so NCD should be quite low
    # (the second half compresses to nearly nothing given the first half's context)
    assert ncd < 0.3, f"Identical halves should give NCD < 0.3, got {ncd:.4f}"


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_ncd_random_halves_near_one():
    import random
    rng = random.Random(99)
    data = bytes(rng.getrandbits(8) for _ in range(16384))
    ncd = ncd_halves(data)
    assert 0.0 <= ncd <= 1.0
    # Two independent random halves: NCD should be near 1
    assert ncd > 0.8, f"Random halves should give NCD > 0.8, got {ncd:.4f}"


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_ncd_bounded():
    # Each half must be ≥ 4096 bytes for ncd_halves to not return NaN
    half_a = b"hello " * 1000  # 6000 bytes
    half_b = bytes(range(200)) * 30  # 6000 bytes, different content
    data = half_a + half_b
    ncd = ncd_halves(data)
    assert 0.0 <= ncd <= 1.0


def test_ncd_too_small_returns_nan():
    result = ncd_halves(b"\x00" * 100)
    assert math.isnan(result)


# ── m_greedy_floor / m_greedy_norm ───────────────────────────────────────────

def test_floor_anchor_points():
    # 4M table anchors (default size)
    assert abs(m_greedy_floor(1.0, 4194304) - 0.9989) < 1e-9
    assert abs(m_greedy_floor(1.7, 4194304) - 0.9952) < 1e-9
    assert abs(m_greedy_floor(4.0, 4194304) - 0.7140) < 1e-9
    assert abs(m_greedy_floor(8.0, 4194304) - 0.000) < 1e-9
    # 256K table anchors
    assert abs(m_greedy_floor(1.0, 262144) - 0.9986) < 1e-9
    assert abs(m_greedy_floor(1.7, 262144) - 0.9941) < 1e-9
    assert abs(m_greedy_floor(4.0, 262144) - 0.6950) < 1e-9
    assert abs(m_greedy_floor(8.0, 262144) - 0.000) < 1e-9


def test_floor_size_specific():
    # Floor at H=1.7 differs measurably between sizes
    floor_4m = m_greedy_floor(1.7, 4194304)
    floor_256k = m_greedy_floor(1.7, 262144)
    assert floor_4m > floor_256k, "4M floor should be higher than 256K at H=1.7"
    assert abs(floor_4m - 0.9952) < 1e-9
    assert abs(floor_256k - 0.9941) < 1e-9


def test_floor_monotone_decreasing():
    for size in [262144, 4194304]:
        hs = [h / 10 for h in range(0, 81)]
        floors = [m_greedy_floor(h, size) for h in hs]
        for i in range(len(floors) - 1):
            assert floors[i] >= floors[i + 1], (
                f"floor not monotone at H={hs[i]:.1f} size={size}: "
                f"{floors[i]} > {floors[i+1]}"
            )


def test_floor_bounded():
    for size in [262144, 4194304]:
        for h in [0.0, 1.5, 3.7, 6.3, 8.0]:
            f = m_greedy_floor(h, size)
            assert 0.0 <= f <= 1.0


def test_norm_at_floor_is_zero():
    # When M_greedy equals the IID floor, norm should be 0
    for size in [262144, 4194304]:
        for h in [5.0, 6.0, 7.0, 8.0]:
            floor = m_greedy_floor(h, size)
            assert abs(m_greedy_norm(floor, h, size)) < 1e-9, (
                f"norm at floor should be 0 for H={h} size={size}"
            )


def test_norm_at_one_is_one():
    # When M_greedy == 1.0, norm should be 1.0
    for size in [262144, 4194304]:
        for h in [5.0, 6.0, 7.0, 8.0]:
            assert abs(m_greedy_norm(1.0, h, size) - 1.0) < 1e-9


def test_norm_nan_when_floor_is_one():
    # H ≤ 0 → floor = 1.0 → norm is undefined (NaN)
    assert math.isnan(m_greedy_norm(0.999, 0.0, 4194304))


def test_norm_clamps_below_floor():
    # M_greedy slightly below floor (measurement noise) → 0, not negative
    for size in [262144, 4194304]:
        floor = m_greedy_floor(6.0, size)
        assert m_greedy_norm(floor - 0.001, 6.0, size) == 0.0


def test_m_norm_reliable():
    # H<4 → not reliable; H≥4 → reliable
    assert not m_norm_reliable(1.0, 4194304)
    assert not m_norm_reliable(1.7, 4194304)
    assert not m_norm_reliable(3.0, 4194304)
    assert m_norm_reliable(4.0, 4194304)
    assert m_norm_reliable(6.0, 4194304)
    assert m_norm_reliable(8.0, 4194304)
    # Same for 256K
    assert not m_norm_reliable(3.0, 262144)
    assert m_norm_reliable(4.0, 262144)


# ── subsample_cis small-file validity ────────────────────────────────────────

def test_subsample_cis_small_file_returns_nan():
    # File smaller than strata * ss (10 MB) should return all NaN
    data = b"hello world " * 1000  # ~12 KB, far below 10 MB threshold
    result = subsample_cis(data)
    for key, val in result.items():
        assert math.isnan(val), f"{key} should be NaN for small file, got {val}"


# ── M_FLOOR_TABLE consistency with LZ_MIN_LEN/LZ_WINDOW (P1-7) ──────────────

@pytest.mark.parametrize("h_target, size, expected_floor, tol", [
    # IID data → M_greedy should match the floor table within tolerance.
    # Tests at both corpus sizes (256K and 4M) and include H=1.7 since it's
    # a direct anchor in the table (not interpolated).
    # If LZ_MIN_LEN or LZ_WINDOW change, these will fail, signaling a stale table.
    (1.0, 4194304, 0.9989, 0.010),
    (1.7, 4194304, 0.9952, 0.010),  # H=1.7 direct anchor — must match tightly
    (1.7, 262144,  0.9941, 0.010),  # 256K-specific floor differs from 4M
    (4.0, 4194304, 0.7140, 0.030),  # steep transition — allow larger tolerance
    (4.0, 262144,  0.6950, 0.030),
    (6.0, 4194304, 0.0249, 0.015),
    (8.0, 4194304, 0.000,  0.005),
])
def test_floor_table_matches_iid_parse(h_target, size, expected_floor, tol):
    """Verify M_FLOOR_TABLE entries match actual greedy-parse output on IID data.

    IID data is constructed using a tilted exponential PMF so we can control H.
    The measured M_greedy on a 256K IID sample should be near expected_floor.
    """
    import random

    # Build byte PMF for target H using tilted exponential (β-bisection lite)
    rng = random.Random(42)
    if h_target >= 7.99:
        # H=8: uniform over all 256 values
        pmf = [1 / 256] * 256
    else:
        # Approximate tilted PMF: p_i ∝ exp(-β·i), β chosen to match H
        lo, hi = 0.0, 30.0
        for _ in range(60):
            beta = (lo + hi) / 2
            raw = [math.exp(-beta * i) for i in range(256)]
            total = sum(raw)
            probs = [p / total for p in raw]
            h_actual = -sum(p * math.log2(p) for p in probs if p > 0)
            if h_actual > h_target:
                lo = beta
            else:
                hi = beta
        raw = [math.exp(-beta * i) for i in range(256)]
        total = sum(raw)
        pmf = [p / total for p in raw]

    # Build CDF for sampling
    cdf = []
    acc = 0.0
    for p in pmf:
        acc += p
        cdf.append(acc)

    # Generate IID bytes at the requested size
    data = bytearray(size)
    for i in range(size):
        x = rng.random()
        lo_i, hi_i = 0, 255
        while lo_i < hi_i:
            mid = (lo_i + hi_i) // 2
            if cdf[mid] < x:
                lo_i = mid + 1
            else:
                hi_i = mid
        data[i] = lo_i
    data = bytes(data)

    m, _, _, _ = lz_stats(data)
    assert abs(m - expected_floor) <= tol, (
        f"H={h_target} size={size}: measured M_greedy={m:.4f}, expected floor={expected_floor} ± {tol}. "
        f"_M_FLOOR_TABLES may be stale for current LZ_MIN_LEN={LZ_MIN_LEN} / LZ_WINDOW={LZ_WINDOW}."
    )
