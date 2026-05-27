"""Compute aggregate statistics and the canonical codec comparison table.

Reads:
  cfg.meta_dir/manifest.json
  cfg.meta_dir/profile.json  (optional)

Writes:
  cfg.meta_dir/stats.json
  cfg.meta_dir/baselines.json

Public interface: run(cfg: BuildConfig) -> int
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import write_bytes_atomic

# Canonical codec set for baselines table
_BASELINE_CODECS = ["gzip-9", "zstd-3", "zstd-19", "lz4"]


# ── statistics helpers ────────────────────────────────────────────────────────


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Interpolated p-th percentile (0 ≤ p ≤ 100) of a sorted list."""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    rank = p / 100.0 * (n - 1)
    lo = int(rank)
    hi = lo + 1
    frac = rank - lo
    if hi >= n:
        return sorted_vals[-1]
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _entropy_histogram(entropies: list[float], bins: int = 16) -> list[list]:
    """Fixed-width histogram over [0, 8] bits per byte."""
    if not entropies:
        return []
    bin_width = 8.0 / bins
    counts = [0] * bins
    for e in entropies:
        idx = int(e / bin_width)
        if idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    return [[round(i * bin_width, 3), counts[i]] for i in range(bins)]


def _log2_size_histogram(sizes: list[int]) -> list[list]:
    """Histogram of log2(size), bucketed to integer log2 values."""
    if not sizes:
        return []
    bucket_counts: dict[int, int] = {}
    for s in sizes:
        if s <= 0:
            b = 0
        else:
            b = int(math.log2(s))
        bucket_counts[b] = bucket_counts.get(b, 0) + 1
    return [[b, bucket_counts[b]] for b in sorted(bucket_counts)]


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _median(sorted_vals: list[float]) -> float:
    return _percentile(sorted_vals, 50)


# ── main entry point ──────────────────────────────────────────────────────────


def run(cfg: BuildConfig) -> int:
    """Compute stats.json and baselines.json from manifest and profile data."""
    meta_dir = cfg.meta_dir
    manifest_path = meta_dir / "manifest.json"

    if not manifest_path.exists():
        print(f"stats: {manifest_path} not found — run 'build manifest' first", file=sys.stderr)
        return 1

    with manifest_path.open() as f:
        manifest = json.load(f)

    artifacts: list[dict] = manifest.get("artifacts", [])
    sources_block: dict = manifest.get("sources", {})

    # Load profile if available
    profile_sources: dict = {}
    profile_path = meta_dir / "profile.json"
    if profile_path.exists():
        try:
            with profile_path.open() as f:
                profile_data = json.load(f)
                profile_sources = profile_data.get("sources", {})
        except Exception as e:
            print(f"stats: warning — could not load profile.json: {e}", file=sys.stderr)

    # ── aggregate counts ──────────────────────────────────────────────────────

    total_artifacts = len(artifacts)
    total_compressed_bytes = sum(a.get("size", 0) for a in artifacts)

    # Compute total_uncompressed_bytes from profile sources if available
    total_uncompressed_bytes = sum(
        s.get("size_uncompressed", 0) for s in profile_sources.values()
    )

    # Per-compression-class aggregation (from profile sources)
    by_compression_class: dict[str, dict] = {}
    for src_key, src in profile_sources.items():
        cls = src.get("compression_class", "unknown")
        sz = src.get("size_uncompressed", 0)
        if cls not in by_compression_class:
            by_compression_class[cls] = {"count": 0, "bytes": 0}
        by_compression_class[cls]["count"] += 1
        by_compression_class[cls]["bytes"] += sz

    # Per-set aggregation (from artifacts; use individual/ paths to avoid double-counting)
    by_set: dict[str, dict] = {}
    for a in artifacts:
        path = a.get("path", "")
        if not path.startswith("individual/"):
            continue
        parts = path.split("/")
        if len(parts) < 2:
            continue
        set_name = parts[1]
        sz = a.get("size", 0)
        if set_name not in by_set:
            by_set[set_name] = {"count": 0, "bytes": 0}
        by_set[set_name]["count"] += 1
        by_set[set_name]["bytes"] += sz

    # Per-tier count
    by_tier: dict[str, int] = {}
    for a in artifacts:
        t = a.get("tier", "full")
        by_tier[t] = by_tier.get(t, 0) + 1

    # ── entropy distribution (from profile) ──────────────────────────────────

    entropies = sorted(
        s["entropy_bits_per_byte"]
        for s in profile_sources.values()
        if "entropy_bits_per_byte" in s
    )
    entropy_dist: dict = {}
    if entropies:
        entropy_dist = {
            "mean":      round(_mean(entropies), 4),
            "median":    round(_median(entropies), 4),
            "p10":       round(_percentile(entropies, 10), 4),
            "p25":       round(_percentile(entropies, 25), 4),
            "p75":       round(_percentile(entropies, 75), 4),
            "p90":       round(_percentile(entropies, 90), 4),
            "histogram": _entropy_histogram(entropies),
        }

    # ── size distribution (from profile) ─────────────────────────────────────

    sizes = sorted(
        s["size_uncompressed"]
        for s in profile_sources.values()
        if "size_uncompressed" in s
    )
    size_dist: dict = {}
    if sizes:
        float_sizes = [float(s) for s in sizes]
        size_dist = {
            "min":             sizes[0],
            "max":             sizes[-1],
            "mean":            int(_mean(float_sizes)),
            "median":          int(_median(float_sizes)),
            "histogram_log2":  _log2_size_histogram(sizes),
        }

    # ── top compressible / least compressible ─────────────────────────────────

    ratio_rows: list[tuple[float, str, str]] = []
    for src_key, src in profile_sources.items():
        ratios = src.get("representative_ratios", {})
        for codec, ratio in ratios.items():
            ratio_rows.append((ratio, src_key, codec))

    ratio_rows.sort(key=lambda x: x[0])
    top_most = [
        {"source": src_key, "codec": codec, "ratio": round(ratio, 6)}
        for ratio, src_key, codec in ratio_rows[:10]
    ]
    top_least = [
        {"source": src_key, "codec": codec, "ratio": round(ratio, 6)}
        for ratio, src_key, codec in ratio_rows[-10:][::-1]
    ]

    # ── total sources count ───────────────────────────────────────────────────

    total_sources = len(profile_sources) if profile_sources else len(sources_block)

    stats: dict = {
        "version":                  1,
        "total_sources":            total_sources,
        "total_artifacts":          total_artifacts,
        "total_uncompressed_bytes": total_uncompressed_bytes,
        "total_compressed_bytes":   total_compressed_bytes,
        "by_compression_class":     by_compression_class,
        "by_set":                   by_set,
        "by_tier":                  by_tier,
        "entropy_distribution":     entropy_dist,
        "size_distribution":        size_dist,
        "top_most_compressible":    top_most,
        "top_least_compressible":   top_least,
    }

    write_bytes_atomic(
        meta_dir / "stats.json",
        (json.dumps(stats, indent=2) + "\n").encode(),
    )

    # ── baselines.json ────────────────────────────────────────────────────────

    # Only include sources that have all four canonical codec ratios
    baseline_sources: dict[str, dict] = {}
    for src_key, src in profile_sources.items():
        ratios = src.get("representative_ratios", {})
        if not all(k in ratios for k in _BASELINE_CODECS):
            continue
        entry: dict = {
            "size_uncompressed": src.get("size_uncompressed", 0),
            "compression_class": src.get("compression_class", "unknown"),
        }
        for codec in _BASELINE_CODECS:
            ratio = ratios[codec]
            raw_size = src.get("size_uncompressed", 0)
            sample_bytes = src.get("sample_bytes")
            denom = sample_bytes if sample_bytes else raw_size
            entry[codec] = {
                "compressed_bytes": int(round(ratio * denom)) if denom else 0,
                "ratio":            round(ratio, 6),
            }
        baseline_sources[src_key] = entry

    baselines: dict = {
        "version": 1,
        "note":    "Table 1: canonical codec comparison. Only sources with all representative_ratios included.",
        "codecs":  _BASELINE_CODECS,
        "sources": baseline_sources,
    }

    write_bytes_atomic(
        meta_dir / "baselines.json",
        (json.dumps(baselines, indent=2) + "\n").encode(),
    )

    print(
        f"stats: {total_artifacts} artifacts, {total_sources} sources, "
        f"{len(baseline_sources)} in baselines table",
        file=sys.stderr,
    )
    return 0
