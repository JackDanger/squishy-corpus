"""Tests for squishy.corpus.axes_rdm — R×D×M grid binning."""
import pytest

from squishy.corpus.axes_rdm import (
    r_bin, d_bin, m_bin,
    r_label, d_label, m_label,
    cell_label, cell_tuple,
    R_LABELS, D_LABELS, M_LABELS,
    R_BREAKS, D_BREAKS, M_BREAKS,
)


# ── bin boundary coverage ─────────────────────────────────────────────────────

@pytest.mark.parametrize("r, expected", [
    (0.00,  "R0"),
    (0.10,  "R0"),
    (0.19,  "R0"),
    (0.20,  "R1"),
    (0.40,  "R1"),
    (0.54,  "R1"),
    (0.55,  "R2"),
    (0.70,  "R2"),
    (0.79,  "R2"),
    (0.80,  "R3"),
    (0.90,  "R3"),
    (0.94,  "R3"),
    (0.95,  "R4"),
    (0.99,  "R4"),
    (1.00,  "R4"),
])
def test_r_bin_labels(r, expected):
    assert r_label(r) == expected, f"r={r}: expected {expected!r}, got {r_label(r)!r}"


@pytest.mark.parametrize("d, expected", [
    (0.000, "D0"),
    (0.010, "D0"),
    (0.019, "D0"),
    (0.020, "D1"),
    (0.040, "D1"),
    (0.054, "D1"),
    (0.055, "D2"),
    (0.080, "D2"),
    (0.099, "D2"),
    (0.100, "D3"),
    (0.200, "D3"),
    (0.249, "D3"),
    (0.250, "D4"),
    (0.500, "D4"),
    (1.000, "D4"),
])
def test_d_bin_labels(d, expected):
    assert d_label(d) == expected, f"d={d}: expected {expected!r}, got {d_label(d)!r}"


@pytest.mark.parametrize("m, expected", [
    (0.00,  "M0"),
    (0.05,  "M0"),
    (0.09,  "M0"),
    (0.10,  "M1"),
    (0.15,  "M1"),
    (0.20,  "M1"),
    (0.21,  "M2"),
    (0.24,  "M2"),
    (0.26,  "M2"),
    (0.27,  "M3"),
    (0.30,  "M3"),
    (0.36,  "M3"),
    (0.37,  "M4"),
    (0.44,  "M4"),
    (1.00,  "M4"),
])
def test_m_bin_labels(m, expected):
    assert m_label(m) == expected, f"m={m}: expected {expected!r}, got {m_label(m)!r}"


# ── full range coverage ───────────────────────────────────────────────────────

def test_r_covers_full_range():
    for i in range(101):
        r = i / 100
        idx = r_bin(r)
        assert 0 <= idx < len(R_LABELS), f"r={r} out of range: {idx}"


def test_d_covers_full_range():
    for i in range(101):
        d = i / 100
        idx = d_bin(d)
        assert 0 <= idx < len(D_LABELS), f"d={d} out of range: {idx}"


def test_m_covers_full_range():
    for i in range(101):
        m = i / 100
        idx = m_bin(m)
        assert 0 <= idx < len(M_LABELS), f"m={m} out of range: {idx}"


# ── break monotonicity ────────────────────────────────────────────────────────

def test_breaks_monotone():
    for breaks, name in [(R_BREAKS, "R"), (D_BREAKS, "D"), (M_BREAKS, "M")]:
        for i in range(len(breaks) - 1):
            assert breaks[i] < breaks[i + 1], f"{name}_BREAKS not monotone at {i}"


def test_label_counts_match_breaks():
    assert len(R_LABELS) == len(R_BREAKS) - 1
    assert len(D_LABELS) == len(D_BREAKS) - 1
    assert len(M_LABELS) == len(M_BREAKS) - 1


# ── cell helpers ──────────────────────────────────────────────────────────────

def test_cell_label():
    label = cell_label(0.90, 0.06, 0.30)
    assert "R3" in label
    assert "D2" in label
    assert "M3" in label


def test_cell_tuple():
    rb, db, mb = cell_tuple(0.90, 0.06, 0.30)
    assert R_LABELS[rb] == "R3"
    assert D_LABELS[db] == "D2"
    assert M_LABELS[mb] == "M3"


def test_cell_label_extremes():
    # Encrypted / random
    label = cell_label(0.05, 0.0, 0.01)
    assert label == "R0/D0/M0"

    # XML-like: high local repetition, no wide-window bonus, rich texture
    label = cell_label(0.98, 0.01, 0.42)
    assert label == "R4/D0/M4"

    # Medical X-ray-like: sparse local, huge wide-window bonus, mild texture
    label = cell_label(0.41, 0.45, 0.16)
    assert label == "R1/D4/M1"


# ── parse.measure smoke tests ─────────────────────────────────────────────────

from squishy.corpus.parse import measure


def test_measure_zeros():
    r, d, m = measure(bytes(4096))
    assert r > 0.90, "file of zeros should have very high R"
    assert d < 0.05, "all structure within 64 KB window for zeros"


def test_measure_random():
    import os
    # M is a bigram estimator: needs ≥ ~256KB for reliable results with 256-symbol alphabet.
    data = os.urandom(256 * 1024)
    r, d, m = measure(data)
    assert r < 0.20, f"random bytes should have low R, got {r:.3f}"
    assert m < 0.10, f"random bytes should have low M, got {m:.3f}"


def test_measure_repeated_block():
    block = b"The quick brown fox jumps over the lazy dog. " * 100
    data = block * 20
    r, d, m = measure(data)
    assert r > 0.80, f"highly repetitive data should have high R, got {r:.3f}"


def test_measure_returns_unit_interval():
    import os
    for _ in range(5):
        data = os.urandom(8192)
        r, d, m = measure(data)
        assert 0.0 <= r <= 1.0
        assert 0.0 <= d <= 1.0
        assert 0.0 <= m <= 1.0
