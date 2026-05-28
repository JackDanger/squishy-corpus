"""Per-file measurement: assembles all v4 metrics into a single row dict."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from squishy.corpus.axes import h_bin, h_label, s_bin, s_label
from squishy.corpus.metrics import byte_entropy, lz_stats, ncd_halves, LZ_WINDOW_32K, LZ_WINDOW_256K
from squishy.corpus.s_driver import measure_s

# CSV schema — single source of truth for column order.
# Columns with no value for a given corpus type are written as empty strings.
FIELDNAMES: list[str] = [
    # Identity
    "path", "sha256", "size_bytes", "corpus", "domain",
    # Measured axes
    "H", "S", "H_bin", "S_bin",
    # Per-codec rates (bpb) — free from S driver, replaces H8
    "R_zstd_long27_19", "R_bzip2_9", "R_zpaq_m5",
    # Per-codec raw sizes (for transparency and re-derivation)
    "S_zstd_bytes", "S_bzip2_bytes", "S_zpaq_bytes", "S_min_codec",
    # LZ77 diagnostics (two window sizes)
    "Lp90_lz77_32k", "Lp90_lz77_256k", "M_lz77_32k",
    # Non-stationarity indicator
    "ncd_halves",
    # Construction parameters (synthetic only)
    "construction", "seed", "H_target", "construction_params_json",
    # Provenance (natural only)
    "source_url", "source_sha256", "source_byte_offset", "source_byte_length",
    "license", "license_url",
]


def _r(v, d: int = 4):
    """Round float to d places; return None for NaN/None."""
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) else round(f, d)


def measure_file(
    path: Path,
    *,
    domain: str = "",
    corpus: str = "",
    skip_s: bool = False,
    skip_ncd: bool = False,
    ground_truth: dict | None = None,
    provenance: dict | None = None,
) -> dict:
    """Measure all v4 corpus metrics for one file.

    Args:
        path:         File to measure.
        domain:       Domain label (e.g. 'text-english'). Empty for synthetic.
        corpus:       'synthetic' or 'natural'.
        skip_s:       Skip the 3-codec S driver (fast mode; S columns are None).
        skip_ncd:     Skip NCD halves computation (fast mode).
        ground_truth: Dict with synthetic construction parameters:
                      construction, seed, H_target, construction_params_json.
        provenance:   Dict with natural file provenance:
                      source_url, source_sha256, source_byte_offset,
                      source_byte_length, license, license_url.

    Returns dict with all FIELDNAMES keys (None for missing/inapplicable values).
    """
    data = path.read_bytes()
    n = len(data)
    sha256 = hashlib.sha256(data).hexdigest()

    H = byte_entropy(data)

    _, _, lp90_32k, _ = lz_stats(data, window=LZ_WINDOW_32K)
    m_lz77_32k, _, lp90_256k, _ = lz_stats(data, window=LZ_WINDOW_256K)

    if skip_s:
        S = None
        s_result = None
    else:
        s_result = measure_s(path)
        S = s_result.S

    gt = ground_truth or {}
    prov = provenance or {}

    row: dict = {
        "path":       str(path),
        "sha256":     sha256,
        "size_bytes": n,
        "corpus":     corpus or path.parent.name,
        "domain":     domain,
        "H":          _r(H, 6),
        "S":          _r(S, 6) if S is not None else None,
        "H_bin":      h_label(H),
        "S_bin":      s_label(S) if S is not None else None,
        # Per-codec rates
        "R_zstd_long27_19": s_result.R_zstd_long27_19 if s_result else None,
        "R_bzip2_9":        s_result.R_bzip2_9 if s_result else None,
        "R_zpaq_m5":        s_result.R_zpaq_m5 if s_result else None,
        # Per-codec raw sizes
        "S_zstd_bytes": s_result.zstd_bytes if s_result else None,
        "S_bzip2_bytes": s_result.bzip2_bytes if s_result else None,
        "S_zpaq_bytes": s_result.zpaq_bytes if s_result else None,
        "S_min_codec":  s_result.S_min_codec if s_result else None,
        # LZ77 diagnostics
        "Lp90_lz77_32k":  _r(lp90_32k),
        "Lp90_lz77_256k": _r(lp90_256k),
        "M_lz77_32k":     _r(m_lz77_32k),
        # NCD
        "ncd_halves": _r(ncd_halves(data)) if not skip_ncd else None,
        # Construction parameters (synthetic only)
        "construction":           gt.get("construction"),
        "seed":                   gt.get("seed"),
        "H_target":               _r(gt.get("H_target")),
        "construction_params_json": gt.get("construction_params_json"),
        # Provenance (natural only)
        "source_url":         prov.get("source_url"),
        "source_sha256":      prov.get("source_sha256"),
        "source_byte_offset": prov.get("source_byte_offset"),
        "source_byte_length": prov.get("source_byte_length"),
        "license":            prov.get("license"),
        "license_url":        prov.get("license_url"),
    }
    return row
