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
# Two independent sources of physics-emptiness:
#
# 1. Greedy LZ77 IID floor at low H.
#    The IID model already predicts very high match density at low entropy:
#      H=1.0 → floor ≈ 0.999;  H=1.7 → floor ≈ 0.995;  H=3.0 → floor ≈ 0.94
#    After IID-floor correction (M_greedy_norm), any 4MB natural file with H<3
#    is forced to M_norm ≈ 1.0 because the metric's dynamic range collapses.
#    squishy/corpus/metrics.py (m_norm_reliable flag, lines ~97-104) documents
#    this: reliability is flagged False below floor=0.90 (roughly H<3.5).
#    Empirical confirmation: chr1 arm (200–204 Mb, SINE/LINE-rich, deliberately
#    chosen as the least-repetitive available genomic region) measured
#    M_greedy_norm=0.997.  The metric, not the data, is doing the binding.
#
# 2. Entropy-M anti-correlation at high H.
#    Near-max-entropy (H>7.5) data with moderate M_greedy_norm (0.20–0.80) does
#    not arise naturally: the repetition that drives M_norm pulls entropy below
#    7.5.  The only natural H>7.5 data type with non-trivial M is uncompressed
#    video luma, which tops out around M_norm≈0.55.
#
# Calibrated-generator cells in these regions remain valid for bench coverage;
# they are just not fillable with natural reference files.
KNOWN_EMPTY_HM: set[tuple[int, int]] = {
    # H<0.5: H≈0 implies all-same byte → only M0.80+ is reachable naturally
    (0, 0), (0, 1), (0, 2), (0, 3), (0, 4),
    # H0.5-1.5: IID floor ≈ 0.999 at H=1.0 → M_norm collapses; M<0.80 unreachable
    (1, 0), (1, 1), (1, 2), (1, 3), (1, 4),
    # H1.5-1.86: IID floor ≈ 0.995 at H=1.7 → M_norm collapses; M<0.80 unreachable
    (2, 0), (2, 1), (2, 2), (2, 3), (2, 4),
    # H1.86-3.0: IID floor ≈ 0.94–0.99 → M_norm unreliable; M<0.60 unreachable
    (3, 0), (3, 1), (3, 2), (3, 3),
    # H3.0-4.5 / M<0.05: at H≈3.5 the IID floor is ~0.82, so any 4MB natural file
    # (with genuine repetition) will have M_norm > 0.82.  Anti-correlated signals
    # (M_norm < IID floor) do not arise in natural data.
    (4, 0),
    # H4.5-6.0 / M<0.05: IID floor ≈ 0.50 at H=5.0.  M_norm < 0.05 requires a signal
    # that is less self-similar than IID white noise — not natural.
    (5, 0),
    # H6.0-7.5 / M0.80+: extreme match density (M≥0.80) at near-binary entropy (H≥6)
    # is self-contradictory.  The repetition that drives M to 0.80+ pulls H below 6.
    (6, 5),
    # H7.5+ / M0.20-0.80: near-max entropy + moderate M is self-contradictory in
    # natural data (repetition that drives M pulls H below 7.5)
    (7, 2), (7, 3), (7, 4),
    # H7.5+ / M0.80+: calibrated generator cap (M_target=0.75 → M_norm≈0.69)
    (7, 5),
}

# L-axis physics emptiness: cells that are unreachable even though (h, m) is reachable.
#
# Source 1 — long-match / high-M entanglement (H ≥ 3.0):
# L_p90 ≥ 60 requires record-aligned or boilerplate data.  At H ≥ 3.0, data with
# 60-byte exact matches (SQL/XML/GFF tags, boilerplate) also produces extreme match
# density (M ≥ 0.80).  L-long + M < 0.80 + H ≥ 3.0 is therefore construction-only.
# Empirical: all natural L-long files are in M0.80+ or are degenerate (chr2-pericent).
# Exception: H4.5-6.0 / M0.80+ / L-long IS reachable — GENCODE GFF3 measured L_p90=154.
#
# Source 2 — IID-floor quantization at low H makes L-short unreachable (H < 1.86):
# When H < 1.86, the data is nearly constant (2-letter alphabet or single symbol).
# LZ77 greedy on near-constant data finds one huge match spanning the entire file
# rather than many short matches.  L_p90 is therefore >> 60 (L-long) regardless of
# the repeating unit.  L-short (L_p90 < 10) cannot arise naturally at M0.80+, H<1.86.
# Empirical: chrY alpha-sat (H=2.18, L_p90=6), MNIST (H=1.94, L_p90=24 → L-medium).
#
# Source 3 — M-axis quantization at H 1.86-3.0:
# M_norm_reliable is False for H < ~4 (IID floor ≈ 0.93-0.99).  The M0.60-0.80 band
# requires M_greedy_norm to land in a 0.20-wide window on a metric whose dynamic
# range collapses to < 0.10 at H=2.5.  Natural data cannot reliably hit this band.
KNOWN_EMPTY_HML: set[tuple[int, int, int]] = {
    # H<0.5 / M0.80+ / L-short: near-constant → LZ77 finds one huge match, not short ones
    (0, 5, 0),
    # H<0.5 / M0.80+ / L-medium: H<0.5 requires ≥92% single-byte dominance.  LZ77 finds
    # either one giant match (→ L-long) or sub-10-byte noise matches between the rare
    # non-dominant bytes (→ L-short).  Regular 10-60 byte spacing of non-dominant bytes
    # does not arise in natural data; any sparse-bitmap source with row stride in [10,60)
    # also has H between 0.5 and 1.0.  Empirical: closest natural file (ptt5 fax) lands
    # at H=1.21, L_p90=65 (L-long, different M band).  Construction-only.
    (0, 5, 1),
    # H0.5-1.5 / M0.80+ / L-short: same — single-symbol dominance → L-short impossible
    (1, 5, 0),
    # H0.5-1.5 / M0.80+ / L-medium: H0.5-1.5 requires 2-symbol dominance (2-letter
    # alphabet, e.g. AT-only DNA or binary sensor stream).  LZ77 on such data finds
    # either one giant match spanning the file (→ L-long) or sub-10-byte noise matches
    # between the rare third/fourth symbol (→ L-short).  Regular 10-60 byte spacing of
    # minority symbols does not arise in natural 2-symbol-dominant data.  The VCF
    # genotype string (H=1.54, L_p90=304) demonstrates the reachable region is L-long,
    # not L-medium.  Confirmed across 6 different attempts in Rounds 7-8: all satellite
    # DNA giving H<1.86 produced L_p90=5-9.  Construction-only.
    (1, 5, 1),
    # H1.5-1.86 / M0.80+ / L-short: EMPIRICALLY REACHABLE.
    # T2T-CHM13 chrY alpha-sat 256K measured H=1.799, M=1.000, L_p90=9 → (2,5,0).
    # The diverged 171bp monomers give short exact-match stretches (≈9bp) even at
    # near-random entropy.  Removed from physics-empty: natural file exists.
    # (2, 5, 0),  ← REMOVED: see t2t_alphasat 256K measurement
    # H1.86-3.0 / M0.60-0.80 / all L: M-axis dynamic range collapses; band unreachable
    (3, 4, 0), (3, 4, 1), (3, 4, 2),
    # H3.0-4.5 / M 0.05-0.60 / L-long: long matches require record alignment → M rises
    (4, 1, 2), (4, 2, 2), (4, 3, 2), (4, 4, 2),
    # H4.5-6.0 / M 0.05-0.60 / L-long: same physics
    (5, 1, 2), (5, 2, 2), (5, 3, 2), (5, 4, 2),
    # H6.0-7.5 / all M < 0.80 / L-long: near-binary entropy + 60-byte exact matches
    # is construction-only (calibrated generators can do it; natural data cannot)
    (6, 0, 2), (6, 1, 2), (6, 2, 2), (6, 3, 2), (6, 4, 2),
    # H7.5+ / M<0.20 / L-long: same
    (7, 0, 2), (7, 1, 2),
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
