"""Tests for squishy.corpus.axes — H×S grid binning."""
import pytest

from squishy.corpus.axes import (
    h_bin, s_bin, h_label, s_label, cell_label, cell_tuple,
    H_LABELS, S_LABELS, H_BREAKS, S_BREAKS,
)


# ── h_bin ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("h, expected_label", [
    (0.0,   "H0"),
    (0.5,   "H0"),
    (0.99,  "H0"),
    (1.0,   "H1"),
    (1.5,   "H1"),
    (1.999, "H1"),
    (2.0,   "H2"),
    (3.0,   "H2"),
    (3.499, "H2"),
    (3.5,   "H3"),
    (4.0,   "H3"),
    (4.999, "H3"),
    (5.0,   "H4"),
    (6.0,   "H4"),
    (6.499, "H4"),
    (6.5,   "H5"),
    (7.0,   "H5"),
    (7.699, "H5"),
    (7.7,   "H6"),
    (7.99,  "H6"),
    (8.0,   "H6"),
])
def test_h_bin_labels(h, expected_label):
    idx = h_bin(h)
    assert H_LABELS[idx] == expected_label, (
        f"h={h}: expected {expected_label!r}, got {H_LABELS[idx]!r}"
    )


# ── s_bin ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("s, expected_label", [
    (0.0,   "S0"),
    (0.04,  "S0"),
    (0.05,  "S1"),
    (0.10,  "S1"),
    (0.249, "S1"),
    (0.25,  "S2"),
    (0.40,  "S2"),
    (0.499, "S2"),
    (0.50,  "S3"),
    (0.60,  "S3"),
    (0.749, "S3"),
    (0.75,  "S4"),
    (0.90,  "S4"),
    (1.00,  "S4"),
])
def test_s_bin_labels(s, expected_label):
    idx = s_bin(s)
    assert S_LABELS[idx] == expected_label, (
        f"s={s}: expected {expected_label!r}, got {S_LABELS[idx]!r}"
    )


# ── boundary coverage ─────────────────────────────────────────────────────────

def test_h_bins_cover_all_values():
    for h_int in range(800):
        h = h_int / 100
        idx = h_bin(h)
        assert 0 <= idx < len(H_LABELS), f"h={h} mapped to out-of-range bin {idx}"


def test_s_bins_cover_all_values():
    for s_int in range(101):
        s = s_int / 100
        idx = s_bin(s)
        assert 0 <= idx < len(S_LABELS), f"s={s} mapped to out-of-range bin {idx}"


def test_h_breaks_monotone():
    for i in range(len(H_BREAKS) - 1):
        assert H_BREAKS[i] < H_BREAKS[i + 1], f"H_BREAKS not monotone at index {i}"


def test_s_breaks_monotone():
    for i in range(len(S_BREAKS) - 1):
        assert S_BREAKS[i] < S_BREAKS[i + 1], f"S_BREAKS not monotone at index {i}"


def test_label_counts_match_breaks():
    assert len(H_LABELS) == len(H_BREAKS) - 1
    assert len(S_LABELS) == len(S_BREAKS) - 1


# ── cell helpers ──────────────────────────────────────────────────────────────

def test_cell_label():
    # H=5.2 → H4, S=0.6 → S3
    label = cell_label(5.2, 0.6)
    assert "H4" in label
    assert "S3" in label


def test_cell_tuple():
    hi, si = cell_tuple(3.5, 0.25)
    assert H_LABELS[hi] == "H3"
    assert S_LABELS[si] == "S2"


def test_cell_label_extremes():
    # Near-zero entropy, near-incompressible
    label = cell_label(0.1, 0.02)
    assert "H0" in label
    assert "S0" in label

    # Near-random
    label = cell_label(7.9, 0.03)
    assert "H6" in label
    assert "S0" in label
