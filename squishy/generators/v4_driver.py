"""v4 synthetic corpus driver: calibration sweep → cell-targeted generation.

Workflow:
  1. Calibration sweep (4 MB files only): run each generator over a coarse
     parameter grid and measure (H, S) for each file. Builds an empirical map
     from params → (H_bin, S_bin).
  2. Target-cell inversion: for each (H_bin, S_bin) cell, find parameter points
     whose calibration measurements landed in or near the cell.
  3. Generate + measure + accept: produce the file; if (H, S) falls in the
     target cell, keep it. Otherwise retry with a nudged parameter.
  4. Scale up: generate 64 MB and 1 GB with the same parameters and seed-family.

Output directory structure:
  build/raw/synthetic/<H_bin>_<S_bin>/<generator>-<params>-<size>-<seed>.bin

Ground truth JSON records are written to
  build/raw/synthetic/ground-truth.json

Usage:
    uv run scripts/gen-synthetic.py            # full run
    uv run scripts/gen-synthetic.py --calibrate-only
    uv run scripts/gen-synthetic.py --cell H3 S2
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from squishy.corpus.axes import h_bin, h_label, s_bin, s_label, H_LABELS, S_LABELS
from squishy.corpus.metrics import byte_entropy, lz_stats
from squishy.corpus.s_driver import measure_s
from squishy.generators.markov import MarkovGenerator, _geometric_rank_entropy
from squishy.generators.lz77_synth import synthesize as lz77_synthesize
from squishy.generators.periodic import (
    generate_structured, generate_shuffled, _build_per_position_pmfs
)

# ── Size tiers ────────────────────────────────────────────────────────────────

V4_SIZES: list[tuple[str, int]] = [
    ("4M",   4 * 1024 * 1024),
    ("64M",  64 * 1024 * 1024),
    ("1G",   1024 * 1024 * 1024),
]
CALIBRATION_SIZE = 4 * 1024 * 1024  # always 4 MB for calibration sweep

# ── Seed helpers ──────────────────────────────────────────────────────────────

def _make_seed(tag: str) -> int:
    return int.from_bytes(hashlib.sha256(tag.encode()).digest()[:8], "big")


# ── Generator parameter spaces ────────────────────────────────────────────────

# markov: k ∈ {1, 2, 4}, tau values sweep entropy
MARKOV_K_VALUES = [1, 2, 4]
MARKOV_TAU_VALUES = [0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 4.0, 8.0]

# lz77-synth: M, mean_L, W (window), H_target
LZ77_M_VALUES = [0.0, 0.3, 0.6, 0.85]
LZ77_L_VALUES = [4, 16, 64, 256]
LZ77_W_VALUES = [4096, 32768, 262144, 4194304]
LZ77_H_VALUES = [2.0, 4.0, 6.0, 8.0]

# periodic: period, profile
PERIODIC_P_VALUES = [4, 8, 16, 32, 256]
PERIODIC_PROFILES = ["gradient", "block"]


# ── Per-generator file generation ─────────────────────────────────────────────

def gen_markov(size: int, k: int, tau: float, seed: int) -> bytes:
    gen = MarkovGenerator(k, tau, seed)
    return gen.generate(size)


def gen_lz77(size: int, M: float, mean_L: int, window: int,
             lit_H: float, seed: int) -> bytes:
    from squishy.generators.lz77_synth import synthesize, _sample_log_uniform
    import random

    # Patch the window into a local synthesize call
    rng = random.Random(seed)
    from squishy.generators.calibrated import tilted_pmf
    lit_pmf = tilted_pmf(lit_H)
    lit_alphabet = list(range(256))

    p_start = M / (mean_L - M * (mean_L - 1)) if M > 0 else 0.0
    log_q = math.log(1.0 - p_start) if 0 < p_start < 1 else -1e300

    buf = bytearray(size)
    is_copy = bytearray(size)
    pos = 0

    def emit_lit() -> None:
        nonlocal pos
        b = rng.choices(lit_alphabet, weights=lit_pmf)[0]
        buf[pos] = b
        pos += 1

    while pos < size:
        if pos == 0 or p_start <= 0:
            emit_lit()
            continue
        u = rng.random()
        if u <= 0.0:
            u = 1e-300
        wait = int(math.log(u) / log_q)
        for _ in range(min(wait, size - pos)):
            emit_lit()
        if pos >= size:
            break

        D = _sample_log_uniform(rng, 1, min(pos, window))
        L = max(1, int(rng.expovariate(1.0 / mean_L)) + 1)
        run_len = min(L, size - pos)
        src = pos - D

        if any(is_copy[src + i] for i in range(run_len)):
            emit_lit()
            continue

        for i in range(run_len):
            buf[pos + i] = buf[src + i]
            is_copy[pos + i] = 1
        pos += run_len

    return bytes(buf)


def gen_periodic(size: int, period: int, profile: str,
                 variant: str, seed: int) -> bytes:
    if variant == "structured":
        return generate_structured(size, period, profile, seed)
    else:
        return generate_shuffled(size, period, profile, seed)


# ── Measurement ───────────────────────────────────────────────────────────────

@dataclass
class FileMeasurement:
    H: float
    S: float
    H_bin: int
    S_bin: int
    H_label: str
    S_label: str


def _measure(path: Path) -> FileMeasurement:
    data = path.read_bytes()
    H = byte_entropy(data)
    s_result = measure_s(path)
    return FileMeasurement(
        H=H, S=s_result.S,
        H_bin=h_bin(H), S_bin=s_bin(s_result.S),
        H_label=h_label(H), S_label=s_label(s_result.S),
    )


# ── Calibration sweep ─────────────────────────────────────────────────────────

@dataclass
class CalibrationPoint:
    generator: str
    params: dict
    H: float
    S: float
    H_label: str
    S_label: str


def calibration_sweep(out_dir: Path) -> list[CalibrationPoint]:
    """Run generators over the full parameter grid at 4 MB, measure (H, S).

    Files are written to out_dir/calibration/ and retained so the sweep can
    be resumed. Results are returned as CalibrationPoint objects.
    """
    from squishy.core.fs import write_bytes_atomic

    cal_dir = out_dir / "calibration"
    cal_dir.mkdir(parents=True, exist_ok=True)

    results: list[CalibrationPoint] = []
    size = CALIBRATION_SIZE

    print("Calibration sweep (4 MB files)…")

    # markov
    for k in MARKOV_K_VALUES:
        for tau in MARKOV_TAU_VALUES:
            tag = f"markov-k{k}-tau{tau:.3f}"
            path = cal_dir / f"{tag}.bin"
            if not path.exists():
                seed = _make_seed(f"cal:{tag}")
                data = gen_markov(size, k, tau, seed)
                write_bytes_atomic(path, data)
            m = _measure(path)
            results.append(CalibrationPoint(
                generator="markov",
                params={"k": k, "tau": tau},
                H=m.H, S=m.S, H_label=m.H_label, S_label=m.S_label,
            ))
            print(f"  {tag}: {m.H_label}/{m.S_label} (H={m.H:.3f} S={m.S:.3f})")

    # lz77-synth
    for M in LZ77_M_VALUES:
        for mean_L in LZ77_L_VALUES:
            for window in LZ77_W_VALUES:
                for lit_H in LZ77_H_VALUES:
                    tag = f"lz77-M{M:.2f}-L{mean_L}-W{window}-H{lit_H:.1f}"
                    path = cal_dir / f"{tag}.bin"
                    if not path.exists():
                        seed = _make_seed(f"cal:{tag}")
                        data = gen_lz77(size, M, mean_L, window, lit_H, seed)
                        write_bytes_atomic(path, data)
                    m = _measure(path)
                    results.append(CalibrationPoint(
                        generator="lz77",
                        params={"M": M, "mean_L": mean_L, "window": window, "lit_H": lit_H},
                        H=m.H, S=m.S, H_label=m.H_label, S_label=m.S_label,
                    ))
                    print(f"  {tag}: {m.H_label}/{m.S_label} (H={m.H:.3f} S={m.S:.3f})")

    # periodic (structured and shuffled)
    for period in PERIODIC_P_VALUES:
        for profile in PERIODIC_PROFILES:
            for variant in ["structured", "shuffled"]:
                tag = f"periodic-P{period}-{profile}-{variant}"
                path = cal_dir / f"{tag}.bin"
                if not path.exists():
                    seed = _make_seed(f"cal:{tag}")
                    data = gen_periodic(size, period, profile, variant, seed)
                    write_bytes_atomic(path, data)
                m = _measure(path)
                results.append(CalibrationPoint(
                    generator="periodic",
                    params={"period": period, "profile": profile, "variant": variant},
                    H=m.H, S=m.S, H_label=m.H_label, S_label=m.S_label,
                ))
                print(f"  {tag}: {m.H_label}/{m.S_label} (H={m.H:.3f} S={m.S:.3f})")

    return results


def print_calibration_map(results: list[CalibrationPoint]) -> None:
    """Print the calibration results as an H×S coverage map."""
    from collections import defaultdict
    cell_counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in results:
        cell_counts[(r.H_label, r.S_label)] += 1

    print("\n── Calibration coverage map (reachable cells) ────────────────────────────")
    print(f"{'':6s}", end="")
    for sl in S_LABELS:
        print(f"  {sl:4s}", end="")
    print()
    for hl in H_LABELS:
        print(f"  {hl:4s}", end="")
        for sl in S_LABELS:
            count = cell_counts.get((hl, sl), 0)
            print(f"  {count:4d}", end="")
        print()
    print()

    reachable = [(hl, sl) for (hl, sl), c in cell_counts.items() if c > 0]
    print(f"  Reachable cells: {len(reachable)}")
