"""Tests for squishy.corpus.axes_cwk (C×W×K binning) and parse_cwk (measurement)."""
import hashlib
import math

import pytest

from squishy.corpus.axes_cwk import (
    c_bin, w_bin, k_bin,
    c_label, w_label, k_label,
    cell_label, cell_tuple,
    C_LABELS, W_LABELS, K_LABELS,
    C_BREAKS, W_BREAKS, K_BREAKS,
    W_UNDEFINED_LABEL,
)
from squishy.corpus import parse_cwk
from squishy.corpus.parse_cwk import measure_cwk, C_FLOOR


# ── bin boundary coverage ─────────────────────────────────────────────────────

@pytest.mark.parametrize("c, expected", [
    (0.00, "C0"), (0.19, "C0"),
    (0.20, "C1"), (0.54, "C1"),
    (0.55, "C2"), (0.84, "C2"),
    (0.85, "C3"), (0.96, "C3"),
    (0.97, "C4"), (1.00, "C4"),
])
def test_c_bin_labels(c, expected):
    assert c_label(c) == expected


@pytest.mark.parametrize("w, expected", [
    (0.00, "W0"), (0.34, "W0"),
    (0.35, "W1"), (0.49, "W1"),
    (0.50, "W2"), (0.59, "W2"),
    (0.60, "W3"), (0.74, "W3"),
    (0.75, "W4"), (1.00, "W4"),
])
def test_w_bin_labels(w, expected):
    assert w_label(w) == expected


@pytest.mark.parametrize("k, expected", [
    (0.00, "K0"), (0.09, "K0"),
    (0.10, "K1"), (0.20, "K1"),
    (0.21, "K2"), (0.29, "K2"),
    (0.30, "K3"), (0.41, "K3"),
    (0.42, "K4"), (1.00, "K4"),
])
def test_k_bin_labels(k, expected):
    assert k_label(k) == expected


# ── break / label invariants ──────────────────────────────────────────────────

@pytest.mark.parametrize("breaks, labels", [
    (C_BREAKS, C_LABELS), (W_BREAKS, W_LABELS), (K_BREAKS, K_LABELS),
])
def test_breaks_are_sorted_and_aligned(breaks, labels):
    assert breaks == sorted(breaks)
    assert len(breaks) == len(labels) + 1
    assert breaks[0] == 0.0
    assert breaks[-1] > 1.0  # last break above 1.0 so value 1.0 bins correctly


@pytest.mark.parametrize("bin_fn, breaks", [
    (c_bin, C_BREAKS), (k_bin, K_BREAKS),
])
def test_bins_monotonic_nondecreasing(bin_fn, breaks):
    prev = 0
    x = 0.0
    while x <= 1.0:
        b = bin_fn(x)
        assert b >= prev
        prev = b
        x += 0.01


# ── W undefined handling ──────────────────────────────────────────────────────

def test_w_undefined_is_none_not_zero():
    assert w_bin(None) is None
    assert w_label(None) == W_UNDEFINED_LABEL


def test_cell_label_with_undefined_w():
    assert cell_label(0.10, None, 0.05) == f"C0/{W_UNDEFINED_LABEL}/K0"
    assert cell_tuple(0.10, None, 0.05) == (0, None, 0)


def test_cell_label_normal():
    assert cell_label(0.98, 0.55, 0.50) == "C4/W2/K4"
    assert cell_tuple(0.98, 0.55, 0.50) == (4, 2, 4)


# ── measurement smoke tests ───────────────────────────────────────────────────

def _shake(seed: bytes, n: int) -> bytes:
    return hashlib.shake_256(seed).digest(n)


def test_random_data_low_coverage_undefined_w_low_k():
    """Uniform random: C≈0, W undefined (below floor), K≈0. The negative control."""
    data = _shake(b"neg-control", 512 * 1024)
    c, w, k = measure_cwk(data)
    assert c < C_FLOOR, f"random data should have C<{C_FLOOR}, got {c}"
    assert w is None, "random data must not land in a W bin (the mis-binning the floor fixes)"
    assert k < 0.10, f"random data should have K≈0, got {k}"


def test_constant_data():
    """All-zeros: fully covered (C≈1), no deep-context gain (K≈0)."""
    data = bytes(256 * 1024)
    c, w, k = measure_cwk(data)
    assert c > 0.95
    assert k < 0.10


def test_repeated_block_high_coverage():
    """A short block tiled to fill the file is almost entirely matchable."""
    block = _shake(b"blk", 1024)
    data = (block * 256)[: 256 * 1024]
    c, w, k = measure_cwk(data)
    assert c > 0.95, f"tiled block should have high C, got {c}"
    assert w is not None


def test_small_alphabet_high_order_has_high_k():
    """An order-3 source over a small alphabet has deep context K can detect.

    This is the K-lever class from the spec; validates K actually moves.
    """
    import random
    rng = random.Random(1234)
    A, order, peak = 6, 3, 0.85
    out = bytearray()
    state = bytes([0] * order)
    for _ in range(512 * 1024):
        h = _shake(b"k|" + state, A)
        ranked = sorted(range(A), key=lambda i: h[i], reverse=True)
        weights = [(1 - peak) / (A - 1)] * A
        weights[ranked[0]] = peak
        b = rng.choices(range(A), weights=weights)[0]
        out.append(b)
        state = state[1:] + bytes([b])
    _, _, k = measure_cwk(bytes(out))
    assert k > 0.25, f"order-3 small-alphabet source should have high K, got {k}"


def test_measure_is_deterministic():
    """No RNG, exact contexts: identical input → identical output."""
    data = _shake(b"determinism", 128 * 1024)
    assert measure_cwk(data) == measure_cwk(data)


def test_k_profile_monotonicity_property():
    """H_h can be non-monotonic in h — the reason K uses min(H2,H3) not a fixed h.

    Documents/guards the spec decision: assert min(H2,H3) ≤ H1 is the quantity used.
    """
    import random
    rng = random.Random(7)
    # order-2 source: H2 should be the deepest useful order; H3 may rise again.
    A, order, peak = 6, 2, 0.85
    out = bytearray()
    state = bytes([0] * order)
    for _ in range(256 * 1024):
        h = _shake(b"k2|" + state, A)
        ranked = sorted(range(A), key=lambda i: h[i], reverse=True)
        weights = [(1 - peak) / (A - 1)] * A
        weights[ranked[0]] = peak
        b = rng.choices(range(A), weights=weights)[0]
        out.append(b)
        state = state[1:] + bytes([b])
    profile, k = parse_cwk._context_K(bytes(out))
    # deep model improves on order-1
    assert min(profile[2], profile[3]) < profile[1]
    assert 0.0 <= k <= 1.0
