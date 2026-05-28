"""H×S grid definition for the v4 corpus.

H = marginal byte entropy (bits/byte), 7 bins H0–H6.
S = structural compressibility = 1 − min(3-family compressed size) / raw size.

See plans/corpus-v4.md for the axis definitions and bin rationale.
"""
from __future__ import annotations

# H bins — anchored to compression-literature regime boundaries
H_BREAKS: list[float] = [0.0, 1.0, 2.0, 3.5, 5.0, 6.5, 7.7, 8.0]
H_LABELS: list[str] = [
    "H0",  # [0.0, 1.0)   sparse / near-constant
    "H1",  # [1.0, 2.0)   DNA, base-4 sensors
    "H2",  # [2.0, 3.5)   structured logs
    "H3",  # [3.5, 5.0)   English text, source code
    "H4",  # [5.0, 6.5)   x86 binaries, structured archives
    "H5",  # [6.5, 7.7)   compressed text streams
    "H6",  # [7.7, 8.0]   near-random: JPEG entropy-coded, AES
]

# S bins — structural compressibility breakpoints
S_BREAKS: list[float] = [0.0, 0.05, 0.25, 0.50, 0.75, 1.01]
S_LABELS: list[str] = [
    "S0",  # [0.00, 0.05)  incompressible by all three families
    "S1",  # [0.05, 0.25)  marginal structure
    "S2",  # [0.25, 0.50)  moderate structure
    "S3",  # [0.50, 0.75)  strong structure
    "S4",  # [0.75, 1.01)  near-fully compressible
]


def h_bin(h: float) -> int:
    """Return H bin index (0 = H0, 6 = H6). Clamps to valid range."""
    for i in range(len(H_BREAKS) - 1):
        if H_BREAKS[i] <= h < H_BREAKS[i + 1]:
            return i
    return len(H_LABELS) - 1


def s_bin(s: float) -> int:
    """Return S bin index (0 = S0, 4 = S4). Clamps to valid range."""
    if s < S_BREAKS[0]:
        return 0  # compressors expanded the file → incompressible
    for i in range(len(S_BREAKS) - 1):
        if S_BREAKS[i] <= s < S_BREAKS[i + 1]:
            return i
    return len(S_LABELS) - 1


def h_label(h: float) -> str:
    """Human-readable H bin label."""
    return H_LABELS[h_bin(h)]


def s_label(s: float) -> str:
    """Human-readable S bin label."""
    return S_LABELS[s_bin(s)]


def cell_label(h: float, s: float) -> str:
    """Human-readable cell label for a file with given H and S."""
    return f"{h_label(h)}/{s_label(s)}"


def cell_tuple(h: float, s: float) -> tuple[int, int]:
    """(h_bin, s_bin) index pair."""
    return h_bin(h), s_bin(s)


def cell_is_physics_empty(h_idx: int, s_idx: int) -> bool:
    """Return True if the (H_bin, S_bin) cell is physically unreachable.

    A file in H_bin h_idx has marginal entropy ≥ H_BREAKS[h_idx] bits/byte.
    Shannon's source-coding bound forces min_compressed_size ≥ H_BREAKS[h_idx]/8
    of raw size, so S ≤ 1 − H_BREAKS[h_idx]/8. Any cell requiring
    S > 1 − H_BREAKS[h_idx]/8 is unreachable.
    """
    max_s = 1.0 - H_BREAKS[h_idx] / 8.0
    return S_BREAKS[s_idx] >= max_s
