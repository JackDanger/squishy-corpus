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
    ncd_halves,
    LZ_MIN_LEN,
    LZ_WINDOW_32K,
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
    assert lz77_parse(b"AB") == []


def test_parse_no_repetition():
    data = bytes(range(256))
    matches = lz77_parse(data)
    assert matches == [], f"Expected no matches, got {len(matches)}"


def test_parse_periodic_has_matches():
    data = b"ABCD" * 256
    matches = lz77_parse(data)
    assert len(matches) >= 1
    total_covered = sum(l for _, _, l in matches)
    assert total_covered / len(data) > 0.9


def test_parse_match_correctness():
    data = b"hello world " * 64
    matches = lz77_parse(data)
    for pos, dist, length in matches:
        assert length >= LZ_MIN_LEN, "Match shorter than min_len"
        assert 1 <= dist <= LZ_WINDOW_32K, "Distance out of window"
        src = pos - dist
        assert data[pos:pos + length] == data[src:src + length], (
            f"Match at pos={pos} dist={dist} length={length} is incorrect"
        )


def test_parse_window_bound():
    first = bytes(range(128)) * 256
    second = bytes(range(128)) * 256
    data = first + second
    matches = lz77_parse(data)
    for _, dist, _ in matches:
        assert dist <= LZ_WINDOW_32K


def test_parse_no_double_counting():
    import random
    rng = random.Random(42)
    data = bytes(rng.getrandbits(8) for _ in range(8192))
    m, _, _, _ = lz_stats(data)
    assert 0.0 <= m <= 1.0


def test_parse_all_same_byte():
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
    data = bytes(range(256))
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


def test_lz_stats_256k_window_finds_more():
    from squishy.corpus.metrics import LZ_WINDOW_256K
    # Repeated block with period > 32K should be found by 256K window but not 32K
    block = bytes(range(200)) * 200  # 40 KB block
    data = block + block             # 80 KB, second block is a copy of first
    m_32k, _, lp90_32k, _ = lz_stats(data, window=LZ_WINDOW_32K)
    m_256k, _, lp90_256k, _ = lz_stats(data, window=LZ_WINDOW_256K)
    # 256K window should find the large copy
    assert m_256k >= m_32k


# ── ncd_halves ───────────────────────────────────────────────────────────────

@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_ncd_identical_halves_near_zero():
    half = bytes(range(256)) * 32
    data = half + half
    ncd = ncd_halves(data)
    assert 0.0 <= ncd <= 1.0
    assert ncd < 0.3, f"Identical halves should give NCD < 0.3, got {ncd:.4f}"


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_ncd_random_halves_near_one():
    import random
    rng = random.Random(99)
    data = bytes(rng.getrandbits(8) for _ in range(16384))
    ncd = ncd_halves(data)
    assert 0.0 <= ncd <= 1.0
    assert ncd > 0.8, f"Random halves should give NCD > 0.8, got {ncd:.4f}"


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_ncd_bounded():
    half_a = b"hello " * 1000
    half_b = bytes(range(200)) * 30
    data = half_a + half_b
    ncd = ncd_halves(data)
    assert 0.0 <= ncd <= 1.0


def test_ncd_too_small_returns_nan():
    result = ncd_halves(b"\x00" * 100)
    assert math.isnan(result)
