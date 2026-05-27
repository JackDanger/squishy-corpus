"""H×M×L grid definition for the v4 corpus.

Non-linear breakpoints anchored to compression-literature regime boundaries.
See plans/corpus-v4.md for the rationale behind each breakpoint.
"""
from __future__ import annotations

# H breakpoints (bits/byte) — anchored to literature
# 1.86 = copy-cost crossover: below this, LZ copying is unprofitable vs literals
H_BREAKS: list[float] = [0.0, 0.5, 1.5, 1.86, 3.0, 4.5, 6.0, 7.5, 8.0]
H_LABELS: list[str] = [
    "H<0.5",       # [0.0, 0.5)   very low entropy: 2-bit encoded sequences
    "H0.5-1.5",    # [0.5, 1.5)   low entropy: run-length, FAX raster
    "H1.5-1.86",   # [1.5, 1.86)  below copy-cost threshold
    "H1.86-3.0",   # [1.86, 3.0)  genomic (4-letter DNA), structured logs
    "H3.0-4.5",    # [3.0, 4.5)   natural language post-BWT approx
    "H4.5-6.0",    # [4.5, 6.0)   typical English text, C source code
    "H6.0-7.5",    # [6.0, 7.5)   x86 machine code, compressed-adjacent binary
    "H7.5+",       # [7.5, 8.0]   near-random: JPEG entropy-coded, AES output
]

# M breakpoints (fraction of bytes covered by greedy LZ matches)
M_BREAKS: list[float] = [0.0, 0.05, 0.20, 0.40, 0.60, 0.80, 1.01]
M_LABELS: list[str] = [
    "M<0.05",      # [0.0, 0.05)  noise floor: near-random, coincidental matches
    "M0.05-0.20",  # [0.05, 0.20) light structure: audio, scientific data
    "M0.20-0.40",  # [0.20, 0.40) moderate: source code, JSON/CSV with repeated keys
    "M0.40-0.60",  # [0.40, 0.60) medium: executables, sensor logs
    "M0.60-0.80",  # [0.60, 0.80) heavy: natural language, binaries
    "M0.80+",      # [0.80, 1.01) extreme: XML, database records, log files
]

# L bins by L_p90 (90th-percentile greedy match length)
# L_p90 is more discriminating than L_median since most files cluster at the
# min-len floor (4) — the 90th percentile reveals genuine long-match structure.
#
# Known L→M confound: longer construction mean_L produces higher M_greedy_norm
# even at the same M_target.  Calibration at H=3, M_target=0.50:
#   mean_L=8  → M_greedy_norm ≈ 0.13  (L-short cell)
#   mean_L=128 → M_greedy_norm ≈ 0.25  (L-medium cell)
# Magnitude: ~0.12 M_greedy_norm units per L tier at H=3.  At H≥4 the effect
# persists but is smaller relative to the wider M axis dynamic range.
# For calibrated files at H<4, M_target is used for M-axis binning (not
# M_greedy_norm), so these files are correctly placed regardless of L.
L_BREAKS: list[float] = [0.0, 10.0, 60.0, float("inf")]
L_LABELS: list[str] = [
    "L-short",     # L_p90 < 10    short-pattern repetition, typical text
    "L-medium",    # 10 ≤ L_p90 < 60  phrase-level, structured text
    "L-long",      # L_p90 ≥ 60   record-level: XML boilerplate, DB records
]

# Cells that are unreachable in the H×M_greedy_norm space.
#
# Since coverage.py now bins on M_greedy_norm (IID floor removed), the old
# floor-driven exclusions for H<4.5 are lifted — calibrated IID files at H=1
# now land in M<0.05 as expected.
#
# Remaining physics-empty cells with M_greedy_norm:
# - H<0.5 / M<0.80+: zeros-like data has M_greedy_norm≈1 (all matches),
#   so only M0.80+ is reachable. Lower-M cells require deliberate un-copies
#   of zeros, which isn't meaningful at H≈0.
# - H7.5+ / M0.80+: at near-random entropy, even the calibrated generator
#   cannot achieve M_greedy_norm > 0.80 (max construction M_target=0.75
#   yields M_greedy_norm≈0.69 at H=8). May be generator gap rather than
#   physics; revisit if M_VALUES extended to 0.85+.
KNOWN_EMPTY_HM: set[tuple[int, int]] = {
    # H<0.5: zeros-like data — only M0.80+ reachable (H≈0 implies all-same byte)
    (0, 0), (0, 1), (0, 2), (0, 3), (0, 4),
    # H7.5+ / M0.80+: current generator max (M_target=0.75) falls in M0.60-0.80
    (7, 5),
}


def h_bin(h: float) -> int:
    """Return H bin index (0 = H<0.5, 7 = H7.5+)."""
    for i in range(len(H_BREAKS) - 1):
        if H_BREAKS[i] <= h < H_BREAKS[i + 1]:
            return i
    return len(H_LABELS) - 1


def m_bin(m: float) -> int:
    """Return M bin index (0 = M<0.05, 5 = M0.80+)."""
    for i in range(len(M_BREAKS) - 1):
        if M_BREAKS[i] <= m < M_BREAKS[i + 1]:
            return i
    return len(M_LABELS) - 1


def l_bin(l_p90) -> int:
    """Return L bin index (0 = short, 1 = medium, 2 = long). None → 0."""
    if l_p90 is None:
        return 0
    try:
        v = float(l_p90)
    except (TypeError, ValueError):
        return 0
    if v < 10:
        return 0
    elif v < 60:
        return 1
    else:
        return 2


def cell_label(h: float, m: float, l_p90) -> str:
    """Human-readable cell label for a file with given H, M_greedy, L_p90."""
    return f"{H_LABELS[h_bin(h)]}/{M_LABELS[m_bin(m)]}/{L_LABELS[l_bin(l_p90)]}"


def cell_tuple(h: float, m: float, l_p90) -> tuple[int, int, int]:
    """(h_bin, m_bin, l_bin) index triple."""
    return h_bin(h), m_bin(m), l_bin(l_p90)
