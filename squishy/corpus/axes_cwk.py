"""C×W×K grid definition — corpus redesign (supersedes axes_rdm.py).

Three codec-agnostic axes measured by parse_cwk.measure_cwk:

  C — Coverage       (full-window LZ77 match fraction)
  W — Window-scale   (length-weighted mean log-offset of matches; None if C<0.05)
  K — Context depth  (gain of deep context over order-1, prequential)

Design and the orthogonality argument: plans/corpus-cwk-spec.md.

Breaks calibrated against the 12 Silesia files' first-4 MB measurements so that
natural files span the middle bins on each axis and synthetic fixtures fill the
extremes:

  C: Silesia 0.27–0.99 (top-heavy; synthetics fill C0–C2)
  W: Silesia 0.43–0.68 (narrow; synthetics fill W0 very-local and W4 long-range)
  K: Silesia 0.07–0.52 (gap at 0.10–0.21; synthetics fill K1)

Provisional until regenerated with the v-next corpus; recompute with
scripts/measure-cwk if the Silesia sample or measurement changes.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# C — Coverage (full-window LZ77 match fraction)
# ---------------------------------------------------------------------------
C_BREAKS: list[float] = [0.0, 0.20, 0.55, 0.85, 0.97, 1.01]
C_LABELS: list[str] = [
    "C0",  # [0.00, 0.20)  encrypted / compressed / random — no recoverable bytes
    "C1",  # [0.20, 0.55)  sparse: x86 binary, scientific float data
    "C2",  # [0.55, 0.85)  moderate: executables, databases, imaging
    "C3",  # [0.85, 0.97)  structured: source, mixed binary
    "C4",  # [0.97, 1.01)  highly repetitive: text, markup, chemical DBs
]

# ---------------------------------------------------------------------------
# W — Window-scale (length-weighted mean log2(offset) / 22; None if C<0.05)
# ---------------------------------------------------------------------------
W_BREAKS: list[float] = [0.0, 0.35, 0.50, 0.60, 0.75, 1.01]
W_LABELS: list[str] = [
    "W0",  # [0.00, 0.35)  very local — captured by any small-window codec
    "W1",  # [0.35, 0.50)  mostly local, some medium-range
    "W2",  # [0.50, 0.60)  medium-range (typical natural files)
    "W3",  # [0.60, 0.75)  long-range — rewards large-window codecs
    "W4",  # [0.75, 1.01)  dominant long-range — only wide-window codecs find it
]

# ---------------------------------------------------------------------------
# K — Context depth ((H1 - min(H2,H3)) / H1, prequential)
# ---------------------------------------------------------------------------
K_BREAKS: list[float] = [0.0, 0.10, 0.21, 0.30, 0.42, 1.01]
K_LABELS: list[str] = [
    "K0",  # [0.00, 0.10)  no deep context: random, float arrays, x86 binary
    "K1",  # [0.10, 0.21)  mild deep context
    "K2",  # [0.21, 0.30)  moderate: prose, imaging
    "K3",  # [0.30, 0.42)  structured: source, dictionaries, databases
    "K4",  # [0.42, 1.01)  rich deep context: markup, strongly patterned text
]

# Sentinel label for files whose W is undefined (C below parse_cwk.C_FLOOR).
W_UNDEFINED_LABEL = "W—"


# ---------------------------------------------------------------------------
# Bin functions
# ---------------------------------------------------------------------------

def _bin(value: float, breaks: list[float]) -> int:
    for i in range(len(breaks) - 1):
        if breaks[i] <= value < breaks[i + 1]:
            return i
    return len(breaks) - 2  # clamp to last bin


def c_bin(c: float) -> int:
    return _bin(c, C_BREAKS)


def w_bin(w: float | None) -> int | None:
    """Bin for W.  Returns None when W is undefined (C below the coverage floor)."""
    if w is None:
        return None
    return _bin(w, W_BREAKS)


def k_bin(k: float) -> int:
    return _bin(k, K_BREAKS)


def c_label(c: float) -> str:
    return C_LABELS[c_bin(c)]


def w_label(w: float | None) -> str:
    return W_UNDEFINED_LABEL if w is None else W_LABELS[_bin(w, W_BREAKS)]


def k_label(k: float) -> str:
    return K_LABELS[k_bin(k)]


def cell_label(c: float, w: float | None, k: float) -> str:
    """Human-readable cell, e.g. 'C4/W2/K3'.  W shows 'W—' when undefined."""
    return f"{c_label(c)}/{w_label(w)}/{k_label(k)}"


def cell_tuple(c: float, w: float | None, k: float) -> tuple[int, int | None, int]:
    """Integer cell coordinates; the W index is None when W is undefined."""
    return c_bin(c), w_bin(w), k_bin(k)
