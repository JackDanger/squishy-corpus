"""R×D×M grid definition — corpus v5.

Three codec-agnostic axes measured from raw bytes:

  R  — Local Repetition   (LZ77 match fraction, 64 KB window)
  D  — Wide-window Bonus  (additional coverage gained with 4 MB vs 64 KB)
  M  — Sequential Texture (per-byte entropy reduction from 1-byte context)

Bins calibrated against the Silesia corpus so natural files cluster in the
middle of the R and M axes.  D extremes (D0, D4) are sparsely populated by
natural files and are filled by synthetic fixtures.

Physics: no cells are unreachable.  Any (R, D, M) combination is achievable
by some generator, unlike the old H×S grid which had 11 forbidden cells.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# R — Local Repetition (64 KB window LZ77 match fraction)
# ---------------------------------------------------------------------------
# Calibrated so Silesia spans R1–R4.  R0 is the "pre-compressed / encrypted"
# zone; natural files rarely land there.
R_BREAKS: list[float] = [0.0, 0.20, 0.55, 0.80, 0.95, 1.01]
R_LABELS: list[str] = [
    "R0",  # [0.00, 0.20)  encrypted, compressed output, true random
    "R1",  # [0.20, 0.55)  sparse: x86 binary, scientific float data
    "R2",  # [0.55, 0.80)  moderate: executables, databases
    "R3",  # [0.80, 0.95)  structured: source code, prose, mixed binary
    "R4",  # [0.95, 1.01)  highly repetitive: XML, text, chemical DBs
]

# ---------------------------------------------------------------------------
# D — Wide-window Bonus  (R_4MB − R_64KB, clamped ≥ 0)
# ---------------------------------------------------------------------------
# Measures how much a large-window codec gains beyond deflate/bzip2.
# Silesia spans D0–D4; extremes dominated by medical imaging (D4) and
# dense local-repeat formats (D0).
D_BREAKS: list[float] = [0.0, 0.020, 0.055, 0.100, 0.250, 1.01]
D_LABELS: list[str] = [
    "D0",  # [0.000, 0.020)  wide window adds nothing — structure is fully local
    "D1",  # [0.020, 0.055)  small bonus — mostly local, some medium-range
    "D2",  # [0.055, 0.100)  moderate bonus — meaningful medium-range structure
    "D3",  # [0.100, 0.250)  large bonus — dominant medium-to-long range
    "D4",  # [0.250, 1.010)  very large bonus — most structure beyond 64 KB
]

# ---------------------------------------------------------------------------
# M — Sequential Texture  (1 − H(X_t|X_{t-1}) / H(X_t))
# ---------------------------------------------------------------------------
# Calibrated so Silesia spans M1–M4.  M0 is the "truly memoryless" zone.
M_BREAKS: list[float] = [0.0, 0.10, 0.21, 0.27, 0.37, 1.01]
M_LABELS: list[str] = [
    "M0",  # [0.00, 0.10)  no texture: encrypted, compressed, white noise
    "M1",  # [0.10, 0.21)  mild: medical images, x86 binary, float arrays
    "M2",  # [0.21, 0.27)  moderate: mixed binary, databases, some prose
    "M3",  # [0.27, 0.37)  structured: source code, dictionaries, markup
    "M4",  # [0.37, 1.01)  rich: natural language, XML, strongly patterned text
]


# ---------------------------------------------------------------------------
# Bin functions
# ---------------------------------------------------------------------------

def _bin(value: float, breaks: list[float]) -> int:
    for i in range(len(breaks) - 1):
        if breaks[i] <= value < breaks[i + 1]:
            return i
    return len(breaks) - 2  # clamp to last bin


def r_bin(r: float) -> int:
    return _bin(r, R_BREAKS)


def d_bin(d: float) -> int:
    return _bin(d, D_BREAKS)


def m_bin(m: float) -> int:
    return _bin(m, M_BREAKS)


def r_label(r: float) -> str:
    return R_LABELS[r_bin(r)]


def d_label(d: float) -> str:
    return D_LABELS[d_bin(d)]


def m_label(m: float) -> str:
    return M_LABELS[m_bin(m)]


def cell_label(r: float, d: float, m: float) -> str:
    return f"{r_label(r)}/{d_label(d)}/{m_label(m)}"


def cell_tuple(r: float, d: float, m: float) -> tuple[int, int, int]:
    return r_bin(r), d_bin(d), m_bin(m)
