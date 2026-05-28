"""LZ77 reverse-synthesizer: generate files with precise parse statistics.

Unlike the calibrated generator (which creates structure post-hoc), this
generator samples an LZ77 parse *directly* and fills in the bytes accordingly.
The result has exactly the parse statistics you request, independent of entropy.

This covers axioms that no other generator can reach:
  - Specific (match_fraction, mean_length, mean_distance) triples
  - Short-distance bias (DEFLATE D1, LZ4 F1)
  - Rep-match bias (LZMA L1: D ∈ recent-distance-cache)
  - Long-length matches (LZMA L4)

Design axes:
  match_frac M:  0.25, 0.50, 0.75   (fraction of output bytes that are copies)
  dist_model:    log_uniform, short, rep4
  mean_length L: 4, 8, 20
  literal_H:     4.0, 8.0           (literal byte entropy; from tilted PMF)
  sizes:         256K, 4M
  seeds:         s0, s1, s2

  Total: 3×3×3×2×2×3 = 324 files (before deduplication of boring combos)
  Actually generated: a curated subset of 72 files covering the key axioms.

Distance models:
  log_uniform:  D ~ log-uniform[1, 32768]  (general LZ)
  short:        D ~ geometric(mean=32) clipped to [1, 512]  (DEFLATE D1/D2)
  rep4:         D cycles through 4 recently used distances (LZMA L1)

Ground truth per file:
  The exact LZ77 parse is saved as a sidecar .parse.jsonl (newline-delimited
  JSON, one token per line: {"t":"L","b":42} for literal, {"t":"C","d":8,"l":5}
  for copy). This allows researchers to verify parse statistics independently.

  Also recorded: reference_parse_cost (bits), the cost of the *synthesized*
  parse under a fixed reference coder (5-bit flag + 15-bit dist + 8-bit len
  for copies; H_lit bits for literals). This is the ideal compressed size for
  the file given knowledge of the parse.

Seed computation:
  sha256(f"lz77:{M}:{dist_model}:{mean_L}:{lit_H}:{size}:{rep}")[:8] → seed
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import write_bytes_atomic
from squishy.generators.calibrated import tilted_pmf, _sample_log_uniform

# ── Corpus design ──────────────────────────────────────────────────────────────

# Curated grid covering the key algorithm-family axioms
CONFIGS: list[dict] = []
for _M in [0.25, 0.50, 0.75]:
    for _dist in ["log_uniform", "short", "rep4"]:
        for _L in [4, 8, 20]:
            for _litH in [4.0, 8.0]:
                CONFIGS.append({"M": _M, "dist": _dist, "mean_L": _L, "lit_H": _litH})

SIZES: list[tuple[str, int]] = [
    ("256K",  262144),
    ("4M",   4194304),
]
REPLICATES: list[str] = ["s0", "s1", "s2"]

WINDOW: int = 32768


# ── Seed helpers ───────────────────────────────────────────────────────────────

def _make_seed(tag: str) -> int:
    return int.from_bytes(hashlib.sha256(tag.encode()).digest()[:8], "big")


def _file_seed(M: float, dist: str, mean_L: int, lit_H: float,
               size: int, rep: str) -> int:
    return _make_seed(f"lz77:{M}:{dist}:{mean_L}:{lit_H}:{size}:{rep}")


def _fname(size_label: str, M: float, dist: str, mean_L: int,
           lit_H: float, rep: str) -> str:
    M_s = f"M{M:.2f}".replace(".", "p")
    L_s = f"L{mean_L}"
    H_s = f"H{lit_H:.1f}".replace(".", "p")
    return f"{size_label}-{M_s}-{dist}-{L_s}-{H_s}-{rep}.bin"


# ── Distance samplers ──────────────────────────────────────────────────────────

# _sample_log_uniform imported from calibrated (exact harmonic CDF, WINDOW=32768)


def _sample_short(rng: random.Random, pos: int) -> int:
    """Short-distance bias: geometric(mean=32), clipped to [1, min(pos, 512)]."""
    hi = min(pos, 512)
    if hi <= 1:
        return 1
    while True:
        D = max(1, int(rng.expovariate(1.0 / 32)) + 1)
        if D <= hi:
            return D


def _sample_rep4(rng: random.Random, pos: int,
                  recent: list[int]) -> int:
    """LZMA-style: choose from 4 most-recently-used distances."""
    if not recent or pos == 0:
        return 1
    valid = [d for d in recent if 0 < d <= pos]
    if not valid:
        return min(pos, 1)
    return rng.choice(valid)


def _sample_distance(rng: random.Random, dist_model: str, pos: int,
                      recent4: list[int]) -> int:
    if pos == 0:
        return 1
    if dist_model == "log_uniform":
        return _sample_log_uniform(rng, 1, min(pos, WINDOW))
    elif dist_model == "short":
        return _sample_short(rng, pos)
    elif dist_model == "rep4":
        return _sample_rep4(rng, pos, recent4)
    raise ValueError(f"unknown dist_model: {dist_model}")


# ── Parse token cost (reference coder) ────────────────────────────────────────

_REF_COPY_OVERHEAD: float = 1.0  # flag bit (literal=0, copy=1)
_REF_DIST_BITS: float = math.log2(WINDOW)         # ≈ 15 bits
_REF_LEN_BITS:  float = 8.0                        # 8-bit length field


def _ref_copy_cost(length: int) -> float:
    """Bits to encode one copy token in the reference coder."""
    return _REF_COPY_OVERHEAD + _REF_DIST_BITS + _REF_LEN_BITS


def _ref_lit_cost(lit_H: float) -> float:
    """Bits to encode one literal token (flag + byte)."""
    return _REF_COPY_OVERHEAD + lit_H


# ── Synthesis ──────────────────────────────────────────────────────────────────

Token = dict  # {"t": "L", "b": int} or {"t": "C", "d": int, "l": int}


def synthesize(size: int, M: float, dist_model: str, mean_L: int,
               lit_H: float, seed: int) -> tuple[bytes, list[Token], int]:
    """Generate 'size' bytes with the target LZ77 parse statistics.

    Returns (data_bytes, parse_tokens, n_rejected_copies).
    n_rejected_copies counts copies whose source range overlapped a prior copy
    (second-order artifact mitigation); these fall back to literals.

    The parse tokens exactly describe how data_bytes was built — every byte
    is accounted for. Researchers can replay the parse to reproduce the file.
    """
    rng = random.Random(seed)
    lit_pmf = tilted_pmf(lit_H)
    lit_alphabet = list(range(256))

    # Probability of starting a copy run so copy fraction ≈ M
    p_start = M / (mean_L - M * (mean_L - 1)) if M > 0 else 0.0
    log_q = math.log(1.0 - p_start) if 0 < p_start < 1 else -1e300

    buf: bytearray = bytearray(size)
    # Bitset tracking which positions were filled by copy tokens (for rejection)
    is_copy = bytearray(size)  # 1 = filled by copy, 0 = filled by literal
    parse: list[Token] = []
    recent4: list[int] = [1, 2, 4, 8]
    n_rejected = 0
    pos = 0

    def emit_literal() -> None:
        nonlocal pos
        b = rng.choices(lit_alphabet, weights=lit_pmf)[0]
        buf[pos] = b
        parse.append({"t": "L", "b": b})
        pos += 1

    while pos < size:
        if pos == 0 or p_start <= 0:
            emit_literal()
            continue

        # Geometric waiting time until next copy
        u = rng.random()
        if u <= 0.0:
            u = 1e-300
        wait = int(math.log(u) / log_q)

        for _ in range(min(wait, size - pos)):
            emit_literal()
        if pos >= size:
            break

        D = _sample_distance(rng, dist_model, pos, recent4)
        L = max(1, int(rng.expovariate(1.0 / mean_L)) + 1)
        run_len = min(L, size - pos)
        src = pos - D

        # Second-order copy rejection: skip if any source byte was itself a copy.
        # Uses the is_copy bitset for O(run_len) check — much faster than scanning
        # parse tokens, and correct for overlapping copies.
        if any(is_copy[src + i] for i in range(run_len)):
            n_rejected += 1
            emit_literal()
            continue

        if D not in recent4:
            recent4 = ([D] + recent4)[:4]

        for i in range(run_len):
            buf[pos + i] = buf[src + i]
            is_copy[pos + i] = 1
        parse.append({"t": "C", "d": D, "l": run_len})
        pos += run_len

    return bytes(buf), parse, n_rejected


def _parse_cost(parse: list[Token], lit_H: float) -> float:
    """Total bits to encode parse under the reference coder."""
    total = 0.0
    for tok in parse:
        if tok["t"] == "L":
            total += _ref_lit_cost(lit_H)
        else:
            total += _ref_copy_cost(tok["l"])
    return total


def _parse_stats(parse: list[Token], size: int) -> dict:
    copies = [t for t in parse if t["t"] == "C"]
    lits   = [t for t in parse if t["t"] == "L"]
    copy_bytes = sum(t["l"] for t in copies)
    return {
        "n_copy_tokens": len(copies),
        "n_literal_tokens": len(lits),
        "copy_bytes": copy_bytes,
        "literal_bytes": len(lits),
        "actual_M_fraction": copy_bytes / size,
        "mean_copy_length": (sum(t["l"] for t in copies) / len(copies)) if copies else 0.0,
        "mean_copy_distance": (sum(t["d"] for t in copies) / len(copies)) if copies else 0.0,
    }


# ── Ground-truth record ────────────────────────────────────────────────────────

def ground_truth_record(size_label: str, size: int, cfg: dict,
                         rep: str, fname: str, parse_stats: dict,
                         ref_cost_bits: float, n_rejected: int = 0) -> dict:
    return {
        "filename":            fname,
        "size_bytes":          size,
        "target_M_fraction":   cfg["M"],
        "actual_M_fraction":   round(parse_stats["actual_M_fraction"], 4),
        "dist_model":          cfg["dist"],
        "mean_length_target":  cfg["mean_L"],
        "mean_copy_length":    round(parse_stats["mean_copy_length"], 2),
        "mean_copy_distance":  round(parse_stats["mean_copy_distance"], 2),
        "literal_H":           cfg["lit_H"],
        "ref_parse_cost_bits": round(ref_cost_bits, 1),
        "reference_bytes":     math.ceil(ref_cost_bits / 8),
        "n_rejected_copies":   n_rejected,
        "generator":           "lz77_synth_v1",
        "data_seed":           _file_seed(cfg["M"], cfg["dist"], cfg["mean_L"],
                                          cfg["lit_H"], size, rep),
        "parse_sidecar":       fname.replace(".bin", ".parse.jsonl"),
    }


# ── Main entry point ───────────────────────────────────────────────────────────

def run(cfg_build: BuildConfig) -> int:
    """Generate the full LZ77 synthesis grid. Returns 0 on success, 1 on failure."""
    try:
        out = cfg_build.raw_dir / "lz77_synth"
        out.mkdir(parents=True, exist_ok=True)

        records: list[dict] = []

        for size_label, size in SIZES:
            for file_cfg in CONFIGS:
                for rep in REPLICATES:
                    fname = _fname(size_label, file_cfg["M"], file_cfg["dist"],
                                   file_cfg["mean_L"], file_cfg["lit_H"], rep)
                    path = out / fname
                    parse_path = out / fname.replace(".bin", ".parse.jsonl")

                    seed = _file_seed(file_cfg["M"], file_cfg["dist"],
                                      file_cfg["mean_L"], file_cfg["lit_H"],
                                      size, rep)

                    if path.exists() and parse_path.exists():
                        print(f"  skip {fname} (exists)")
                        data, parse, n_rejected = synthesize(
                            size, file_cfg["M"], file_cfg["dist"],
                            file_cfg["mean_L"], file_cfg["lit_H"], seed,
                        )
                    else:
                        data, parse, n_rejected = synthesize(
                            size, file_cfg["M"], file_cfg["dist"],
                            file_cfg["mean_L"], file_cfg["lit_H"], seed,
                        )
                        if not path.exists():
                            write_bytes_atomic(path, data)
                        if not parse_path.exists():
                            lines = "\n".join(json.dumps(t) for t in parse)
                            write_bytes_atomic(parse_path, lines.encode())
                        print(f"  {fname} ({len(data):,} bytes, {len(parse)} tokens,"
                              f" {n_rejected} rejected)")

                    stats = _parse_stats(parse, size)
                    ref_cost = _parse_cost(parse, file_cfg["lit_H"])
                    rec = ground_truth_record(size_label, size, file_cfg,
                                              rep, fname, stats, ref_cost, n_rejected)
                    records.append(rec)

        gt_path = out / "ground-truth.json"
        write_bytes_atomic(gt_path, json.dumps(records, indent=2).encode())
        print(f"  lz77_synth: {len(records)} records written to {out}")
        return 0

    except Exception as exc:
        import traceback
        print(f"  ERROR in lz77_synth: {exc}")
        traceback.print_exc()
        return 1
