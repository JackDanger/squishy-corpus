"""Per-file measurement: assembles all metrics into a single row dict."""
from __future__ import annotations

import math
from pathlib import Path

from squishy.corpus.metrics import (
    byte_entropy, lz_stats, sigma_h, ncd_halves, subsample_cis,
    m_greedy_norm, m_norm_reliable,
)

# Bits-per-byte threshold below which copying exceeds literal coding.
# Files with H_marginal < this threshold have R_ref clamped to H_marginal
# regardless of M_target (see calibrated.py reference_rate()).
_COPY_COST_THRESHOLD_BPB: float = 1.86

# CSV schema — single source of truth for column order
FIELDNAMES: list[str] = [
    "corpus", "filename", "size_bytes",
    # Construction metadata (calibrated files only; None for natural corpora)
    "generator", "H_target", "M_target", "R_ref", "reference_bytes",
    # Reliability and clamping flags
    "M_norm_reliable",   # True when M_greedy_norm has adequate dynamic range (H≥4)
    "R_ref_clamped",     # True when R_ref = H_marginal (copying not profitable at H<1.86)
    # Measured metrics
    "H_marginal",
    "M_greedy", "M_greedy_norm",
    "L_median", "L_p90", "L_geomean",
    "sigma_H_1k", "sigma_H_16k", "sigma_H_256k",
    "ncd_halves",
    "H_ci95_lo", "H_ci95_hi",
    "M_ci95_lo", "M_ci95_hi",
    "L_ci95_lo", "L_ci95_hi",
    "L_ci_rel",
]


def _r(v, d: int = 4):
    """Round a float to d decimal places; return None for NaN/None.

    Raises TypeError if v is non-numeric — research code should be loud
    about type confusion, not silently pass strings into CSV float fields.
    """
    if v is None:
        return None
    f = float(v)   # raises TypeError on non-numeric
    return None if math.isnan(f) else round(f, d)


def measure_file(path: Path, *, bootstrap: bool = True,
                 bootstrap_seed: int = 42,
                 ground_truth: dict | None = None) -> dict:
    """Measure all v4 corpus metrics for one file.

    Args:
        path:           File to measure.
        bootstrap:      If False, skip subsample CIs, sigma_H, and NCD (fast mode).
        bootstrap_seed: RNG seed for the stratified subsampler.
        ground_truth:   Dict from ground-truth.json for this filename, or None.
                        When provided, copies generator, H_target, M_target,
                        R_ref, and reference_bytes into the output row.

    Returns dict with all FIELDNAMES keys (None for missing/invalid values).
    The CIs are stratified subsample intervals, not resampling bootstrap CIs —
    they measure per-region variability across the file.
    """
    data = path.read_bytes()
    n = len(data)

    H = byte_entropy(data)
    M_greedy, L_median, L_p90, L_geomean = lz_stats(data)

    gt = ground_truth or {}
    base = {
        "corpus":     path.parent.name,
        "filename":   path.name,
        "size_bytes": n,
        "generator":       gt.get("generator"),
        "H_target":        _r(gt.get("H_marginal")),
        "M_target":        _r(gt.get("M_fraction")),
        "R_ref":           _r(gt.get("R_ref")),
        "reference_bytes": gt.get("reference_bytes"),
        "M_norm_reliable": m_norm_reliable(H, n),
        "R_ref_clamped":   (
            bool(gt.get("R_ref_clamped"))
            if "R_ref_clamped" in gt
            else (
                H < _COPY_COST_THRESHOLD_BPB and (gt.get("M_fraction") or 0.0) > 0.0
                if "M_fraction" in gt
                else None
            )
        ),
        "H_marginal":      _r(H),
        "M_greedy":        _r(M_greedy),
        "M_greedy_norm":   _r(m_greedy_norm(M_greedy, H, n)),
        "L_median":        _r(L_median),
        "L_p90":           _r(L_p90),
        "L_geomean":       _r(L_geomean),
    }

    if not bootstrap:
        return {
            **base,
            "sigma_H_1k": None, "sigma_H_16k": None, "sigma_H_256k": None,
            "ncd_halves": None,
            "H_ci95_lo": None, "H_ci95_hi": None,
            "M_ci95_lo": None, "M_ci95_hi": None,
            "L_ci95_lo": None, "L_ci95_hi": None,
            "L_ci_rel": None,
        }

    cis = subsample_cis(data, seed=bootstrap_seed)
    return {
        **base,
        "sigma_H_1k":  _r(sigma_h(data, 1024)),
        "sigma_H_16k": _r(sigma_h(data, 16384)),
        "sigma_H_256k":_r(sigma_h(data, 262144)),
        "ncd_halves":  _r(ncd_halves(data)),
        "H_ci95_lo":   _r(cis["H_ci95_lo"]),
        "H_ci95_hi":   _r(cis["H_ci95_hi"]),
        "M_ci95_lo":   _r(cis["M_ci95_lo"]),
        "M_ci95_hi":   _r(cis["M_ci95_hi"]),
        "L_ci95_lo":   _r(cis["L_ci95_lo"]),
        "L_ci95_hi":   _r(cis["L_ci95_hi"]),
        "L_ci_rel":    _r(cis["L_ci_rel"]),
    }
