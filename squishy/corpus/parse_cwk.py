"""C×W×K file measurement for the corpus grid (supersedes parse.py's R/D/M).

Three codec-agnostic axes measured from raw bytes, no codec run:

  C  — Coverage:      greedy LZ77 match fraction over the full (4 MB) window.
  W  — Window-scale:  length-weighted mean log2(match offset) / 22; a *shape*
                      statistic of where redundancy lives, independent of C.
  K  — Context depth: prequential (adaptive, exact-context) log-loss gain of a
                      deep context model over order-1 — the context-mixing signal.

Design rationale and the empirical validation behind every constant live in
plans/corpus-cwk-spec.md.  The measurement is fully deterministic: no RNG, and
contexts are exact `bytes` keys (no salted `hash()`), so K is reproducible.
"""
from __future__ import annotations

import math

SAMPLE_BYTES = 4 * 1024 * 1024   # 4 MB sample cap — all coordinates describe this prefix
MIN_MATCH    = 4                  # minimum back-reference length for the parse
MIN_MATCH_W  = 8                  # only matches this long count toward W (drop chance hits)
MAX_MATCH    = 65536              # cap on a single match length (parse speed)
C_FLOOR      = 0.05               # below this coverage, W is undefined (excluded)
K_MAX_ORDER  = 3                  # deepest context order (4 MB can't learn deeper at 256-alphabet)
KT_ALPHA     = 0.5                # Krichevsky–Trofimov / Laplace smoothing constant


def _lz77_parse(data: bytes | memoryview, window: int) -> tuple[int, list[tuple[int, int]]]:
    """Greedy single-entry-per-hash LZ77.

    Returns (matched_bytes, matches) where each match is (length, offset).
    """
    n = len(data)
    if n < MIN_MATCH + 1:
        return 0, []
    table: dict[int, int] = {}
    matches: list[tuple[int, int]] = []
    matched = 0
    i = 0
    while i < n - MIN_MATCH:
        h = data[i] | (data[i+1] << 8) | (data[i+2] << 16) | (data[i+3] << 24)
        prev = table.get(h)
        table[h] = i
        if prev is not None:
            off = i - prev
            if 0 < off <= window:
                j = MIN_MATCH
                limit = min(n - i, n - prev, MAX_MATCH)
                while j < limit and data[prev + j] == data[i + j]:
                    j += 1
                matched += j
                matches.append((j, off))
                i += j
                continue
        i += 1
    return matched, matches


def _coverage_and_range(data: bytes) -> tuple[float, float | None]:
    """Return (C, W).  W is None when C < C_FLOOR (redundancy too sparse to locate)."""
    n = len(data)
    matched, matches = _lz77_parse(data, n)
    c = matched / n if n else 0.0
    if c < C_FLOOR:
        return c, None
    num = 0.0
    den = 0.0
    for length, off in matches:
        if length >= MIN_MATCH_W and off >= 1:
            num += length * math.log2(off)
            den += length
    if den == 0.0:
        return c, None
    w = (num / den) / math.log2(n)
    return c, max(0.0, min(1.0, w))


def _context_profile(data: bytes, max_order: int = K_MAX_ORDER,
                     alpha: float = KT_ALPHA) -> list[float]:
    """Prequential per-byte log-loss (bpb) for orders 0..max_order.

    Exact contexts (the literal previous `order` bytes as a `bytes` key) — no
    hashing, so deterministic and collision-free.  Each byte is scored against
    counts accumulated only from its past (adaptive coding), so the estimate is
    the honest predictive cost of an order-`order` model and is well-defined even
    where contexts are sparse (sparse → high loss, correctly).
    """
    n = len(data)
    orders = list(range(max_order + 1))
    if n < 2:
        return [0.0] * len(orders)
    # counts[order]: ctx_bytes -> {symbol: count};  totals[order]: ctx_bytes -> total
    counts: list[dict[bytes, dict[int, int]]] = [dict() for _ in orders]
    totals: list[dict[bytes, int]] = [dict() for _ in orders]
    loss = [0.0] * len(orders)
    inv_alpha_den = 256 * alpha
    for t in range(n):
        x = data[t]
        for o in orders:
            ctx = data[max(0, t - o):t]
            csym = counts[o].get(ctx)
            if csym is None:
                # unseen context: p = alpha / (alpha * 256)
                loss[o] += -math.log2(alpha / inv_alpha_den)
                counts[o][ctx] = {x: 1}
                totals[o][ctx] = 1
            else:
                tot = totals[o][ctx]
                cx = csym.get(x, 0)
                p = (cx + alpha) / (tot + inv_alpha_den)
                loss[o] += -math.log2(p)
                csym[x] = cx + 1
                totals[o][ctx] = tot + 1
    return [l / n for l in loss]


def _context_K(data: bytes) -> tuple[list[float], float]:
    """Return (H_profile, K).  K = clamp01((H1 - min(H2,H3)) / H1)."""
    h = _context_profile(data)
    if len(h) < 4 or h[1] <= 0.0:
        return h, 0.0
    h_deep = min(h[2], h[3])
    k = (h[1] - h_deep) / h[1]
    return h, max(0.0, min(1.0, k))


def measure_cwk(data: bytes, sample: int = SAMPLE_BYTES) -> tuple[float, float | None, float]:
    """Return (C, W, K) for up to `sample` bytes of `data`.

    C in [0, 1]    — coverage (full-window LZ77 match fraction)
    W in [0, 1]    — window-scale, or None when C < C_FLOOR
    K in [0, 1]    — context depth (gain of deep context over order-1)
    """
    chunk = data[:sample]
    c, w = _coverage_and_range(chunk)
    _, k = _context_K(chunk)
    return c, w, k
