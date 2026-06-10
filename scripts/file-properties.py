#!/usr/bin/env python3
"""Intrinsic byte-properties of the core files — measured from the bytes alone,
with NO reference to any compressor (so they can't be circular axes for a
compression benchmark). Writes build/meta/file-properties.json.

Per file:
  entropy        order-0 Shannon entropy of the byte histogram (bits/byte, 0..8)
  coverage       fraction of (non-overlapping K-byte) blocks that exactly recur
                 earlier in the file — how repetitive the string is
  match_distance median byte-distance back to the previous occurrence of a
                 recurring block — local vs long-range structure ("pattern range")

The block-recurrence scan is a plain, deterministic property of the string (the
previous exact occurrence of each K-byte block), not a model of any codec.

  uv run python scripts/file-properties.py     # -> build/meta/file-properties.json
"""
from __future__ import annotations
import importlib.util, json, math, statistics
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Pinned measurement config (changing these changes the numbers; recorded in output).
K = 16                       # block size for the recurrence scan (bytes)
HASH = "block-int128-exact"  # key = int.from_bytes(block) — deterministic & exact
                             # (no hash collisions), NOT Python's randomized hash().


def measure(path: Path) -> dict:
    """Exact, whole-file intrinsic byte properties — every byte, no approximation.

    entropy:        order-0 Shannon entropy over the full byte histogram (bits/byte).
    coverage:       fraction of the file's non-overlapping K-byte blocks that exactly
                    recur earlier in the file.
    match_distance: median (and p90) byte-distance back to the previous occurrence of
                    a recurring block — local vs long-range structure.

    Whole-file: every byte is read. The recurrence is computed exactly via a
    vectorised lexsort over each block's full 128-bit value (two uint64 halves —
    no hashing, no collisions), which costs ~24 B per block instead of the
    ~100 B/entry of a Python dict, so multi-GB files (the large size rungs) measure
    within bounded memory. Falls back to a dict scan only if numpy is unavailable."""
    n = path.stat().st_size
    try:
        import numpy as np
    except Exception:
        return _measure_dict(path)

    # Recurrence key per K=16 byte block. EXACT mode carries the block's full 128-bit
    # value as two uint64 halves (no collisions). For files above EXACT_MAX the two
    # halves + sort index would not fit comfortably in RAM, so we fall back to a single
    # mixed uint64 key (expected collisions ~n^2/2^65 ≈ 0.002 blocks even at 4 GB — i.e.
    # effectively exact) at half the memory. Either way every byte is read (whole-file).
    EXACT_MAX = 2_500_000_000
    exact = n <= EXACT_MAX
    C0 = np.uint64(0x9E3779B97F4A7C15); C1 = np.uint64(0xC2B2AE3D27D4EB4F)
    hist = np.zeros(256, dtype="int64")
    v0_parts: list = []; v1_parts: list = []; key_parts: list = []
    chunk_bytes = (1 << 23) // K * K              # read in whole-block multiples
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_bytes)
            if not chunk:
                break
            arr = np.frombuffer(chunk, dtype="uint8")
            hist += np.bincount(arr, minlength=256)
            nb = len(arr) // K
            if nb:
                halves = arr[:nb * K].reshape(nb, K).view("<u8")   # (nb, 2)
                if exact:
                    v0_parts.append(halves[:, 0].copy()); v1_parts.append(halves[:, 1].copy())
                else:
                    with np.errstate(over="ignore"):
                        key_parts.append((halves[:, 0] * C0) ^ (halves[:, 1] * C1))
    nblocks = int(sum(p.shape[0] for p in (v0_parts if exact else key_parts)))
    counts = hist[hist > 0]
    entropy = -float((counts / n * np.log2(counts / n)).sum()) if n else 0.0

    covered = 0; med = K; p90 = K
    if nblocks:
        if exact:
            v0 = np.concatenate(v0_parts); v1 = np.concatenate(v1_parts); del v0_parts, v1_parts
            order = np.lexsort((v1, v0))              # stable: ties keep original (index) order
            same = (v0[order][1:] == v0[order][:-1]) & (v1[order][1:] == v1[order][:-1])
            del v0, v1
        else:
            keys = np.concatenate(key_parts); del key_parts
            order = np.argsort(keys, kind="stable")
            s = keys[order]; del keys
            same = s[1:] == s[:-1]; del s
        covered = int(same.sum())
        if covered:
            # within a key-group the stable sort preserves increasing original index,
            # so the sorted predecessor IS the immediately preceding occurrence.
            dists = (order[1:][same] - order[:-1][same]).astype("int64") * K
            med = int(np.median(dists))
            p90 = int(np.quantile(dists, 0.9)) if dists.size > 10 else int(dists.max())
    return {
        "size": n,
        "entropy": round(float(entropy), 3),
        "coverage": round(covered / nblocks, 4) if nblocks else 0.0,
        "match_distance": med,
        "match_distance_p90": p90,
        "block_bytes": K, "hash": HASH,
    }


def _measure_dict(path: Path) -> dict:
    """Exact dict-based scan (no numpy). Same result as measure(); higher memory."""
    from collections import Counter
    n = path.stat().st_size
    cnt = Counter(); last: dict[int, int] = {}
    dists, covered, nblocks = [], 0, 0
    chunk_bytes = (1 << 23) // K * K
    with open(path, "rb") as f:
        bi = 0
        while True:
            chunk = f.read(chunk_bytes)
            if not chunk:
                break
            cnt.update(chunk)
            for off in range(0, len(chunk) - K + 1, K):
                key = int.from_bytes(chunk[off:off + K], "little")
                prev = last.get(key)
                if prev is not None:
                    dists.append((bi - prev) * K); covered += 1
                last[key] = bi
                bi += 1
            nblocks = bi
    entropy = -sum((c / n) * math.log2(c / n) for c in cnt.values()) if n else 0.0
    return {
        "size": n,
        "entropy": round(float(entropy), 3),
        "coverage": round(covered / nblocks, 4) if nblocks else 0.0,
        "match_distance": int(statistics.median(dists)) if dists else K,
        # method="inclusive" matches np.quantile's default linear interpolation,
        # so this fallback reports the same p90 as measure()'s numpy path.
        "match_distance_p90": int(statistics.quantiles(dists, n=10, method="inclusive")[8]) if len(dists) > 10 else (max(dists) if dists else K),
        "block_bytes": K, "hash": HASH,
    }


def main() -> int:
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    out = {}
    for cat, files in sq.CORE.items():
        for display, st, name in files:
            p = sq.raw_path(st, name)
            if not p.exists():
                continue
            m = measure(p); m["category"] = cat
            out[display] = m
            print(f"  {display:<9} H={m['entropy']:.2f} bits/B  cover={m['coverage']*100:5.1f}%  "
                  f"dist(med)={m['match_distance']:>10,}B  p90={m['match_distance_p90']:>12,}B")
    dst = REPO / "build" / "meta" / "file-properties.json"
    dst.write_text(json.dumps({"block_bytes": K,
        "note": "intrinsic byte properties; measured from bytes only, no compressor involved",
        "files": out}, indent=2) + "\n")
    print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
