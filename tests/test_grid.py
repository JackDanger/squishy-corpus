"""Tests for squishy.corpus.grid cell binning."""
import pytest

from squishy.corpus.grid import (
    h_bin, m_bin, l_bin,
    H_LABELS, M_LABELS, L_LABELS,
    H_BREAKS, M_BREAKS,
    cell_label, cell_tuple,
)


# ── h_bin ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("h, expected_label", [
    (0.0,   "H<0.5"),
    (0.25,  "H<0.5"),
    (0.5,   "H0.5-1.5"),
    (1.0,   "H0.5-1.5"),
    (1.5,   "H1.5-1.86"),
    (1.86,  "H1.86-3.0"),
    (2.429, "H1.86-3.0"),  # Silesia nci
    (3.684, "H3.0-4.5"),   # Silesia mr
    (4.532, "H4.5-6.0"),   # Silesia dickens
    (6.222, "H6.0-7.5"),   # Silesia mozilla
    (7.525, "H7.5+"),      # Silesia sao
    (7.999, "H7.5+"),
])
def test_h_bin_labels(h, expected_label):
    idx = h_bin(h)
    assert H_LABELS[idx] == expected_label, f"h={h}: expected {expected_label}, got {H_LABELS[idx]}"


# ── m_bin ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("m, expected_label", [
    (0.0,  "M<0.05"),
    (0.03, "M<0.05"),
    (0.05, "M0.05-0.20"),
    (0.10, "M0.05-0.20"),
    (0.20, "M0.20-0.40"),
    (0.324,"M0.20-0.40"),  # Silesia x-ray
    (0.40, "M0.40-0.60"),
    (0.458,"M0.40-0.60"),  # Silesia sao
    (0.60, "M0.60-0.80"),
    (0.767,"M0.60-0.80"),  # Silesia mozilla
    (0.80, "M0.80+"),
    (0.978,"M0.80+"),      # Silesia nci
    (1.0,  "M0.80+"),
])
def test_m_bin_labels(m, expected_label):
    idx = m_bin(m)
    assert M_LABELS[idx] == expected_label, f"m={m}: expected {expected_label}, got {M_LABELS[idx]}"


# ── l_bin ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("l_p90, expected_label", [
    (None, "L-short"),
    (4.0,  "L-short"),
    (5.0,  "L-short"),
    (9.99, "L-short"),
    (10.0, "L-medium"),
    (12.0, "L-medium"),
    (59.9, "L-medium"),
    (60.0, "L-long"),
    (100.0,"L-long"),
])
def test_l_bin_labels(l_p90, expected_label):
    idx = l_bin(l_p90)
    assert L_LABELS[idx] == expected_label, f"l={l_p90}: expected {expected_label}, got {L_LABELS[idx]}"


# ── coverage ──────────────────────────────────────────────────────────────────

def test_h_bins_cover_all_values():
    # Every value in [0, 8) should map to a valid bin
    for h in [x / 100 for x in range(800)]:
        idx = h_bin(h)
        assert 0 <= idx < len(H_LABELS)


def test_m_bins_cover_all_values():
    for m_int in range(101):
        m = m_int / 100
        idx = m_bin(m)
        assert 0 <= idx < len(M_LABELS)


def test_cell_label():
    # Silesia sao: H=7.525, M=0.458, L_p90=4.0
    label = cell_label(7.525, 0.458, 4.0)
    assert "H7.5+" in label
    assert "M0.40-0.60" in label
    assert "L-short" in label


def test_cell_tuple():
    hi, mi, li = cell_tuple(2.429, 0.978, 10.0)
    assert H_LABELS[hi] == "H1.86-3.0"
    assert M_LABELS[mi] == "M0.80+"
    assert L_LABELS[li] == "L-medium"
