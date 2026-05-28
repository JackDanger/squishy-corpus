"""v4 synthetic corpus driver: calibration sweep → cell-targeted generation.

Workflow:
  1. Calibration sweep (4 MB files only): run each generator over a curated
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

import concurrent.futures
import hashlib
import json
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
# tau=0.35 fills the H4 gap that tau=0.5→1.0 jumps over
MARKOV_TAU_VALUES = [0.05, 0.1, 0.2, 0.35, 0.5, 1.0, 2.0, 4.0]

# lz77-synth: curated 40-config grid covering all physics-reachable H×S cells.
# Organized by what each parameter actually controls:
#   lit_H → marginal H (modulated by M at high match fractions)
#   M + window → S (match fraction + scope of reuse)
#   mean_L → secondary S lever (longer copies compress better)
def _lz77_configs() -> list[dict]:
    configs: list[dict] = []

    # M=0.0: no copies; window/mean_L irrelevant. Pure lit_H sweep.
    for lit_H in [2.0, 4.0, 6.0, 8.0]:
        configs.append({"M": 0.0, "mean_L": 8, "window": 32768, "lit_H": lit_H})

    # M=0.5, M=0.8: full mean_L × window × lit_H endpoint cross.
    for M in [0.5, 0.8]:
        for mean_L in [8, 64, 512]:
            for window in [32768, 4194304]:
                for lit_H in [2.0, 8.0]:
                    configs.append({"M": M, "mean_L": mean_L,
                                    "window": window, "lit_H": lit_H})
        # Mid lit_H at the default window (interpolates interior H cells)
        for lit_H in [4.0, 6.0]:
            configs.append({"M": M, "mean_L": 64, "window": 32768, "lit_H": lit_H})

    # M=0.95: targeting S4 cells. Only large window + long mean_L can push there.
    # Physics forbids S4 for lit_H→H5 or H6, but still generate to confirm empirically.
    for mean_L in [64, 512]:
        for lit_H in [2.0, 4.0, 6.0, 8.0]:
            configs.append({"M": 0.95, "mean_L": mean_L,
                            "window": 4194304, "lit_H": lit_H})

    return configs


LZ77_CONFIGS: list[dict] = _lz77_configs()

# periodic: period, profile
PERIODIC_P_VALUES = [4, 8, 16, 32, 256]
PERIODIC_PROFILES = ["gradient", "block"]


# ── Per-generator file generation ─────────────────────────────────────────────

def gen_markov(size: int, k: int, tau: float, seed: int) -> bytes:
    gen = MarkovGenerator(k, tau, seed)
    return gen.generate(size)


def gen_lz77(size: int, M: float, mean_L: int, window: int,
             lit_H: float, seed: int) -> bytes:
    data, _parse, _rejected = lz77_synthesize(
        size, M, "log_uniform", mean_L, lit_H, seed, window=window,
    )
    return data


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


def _gen_and_measure(args: tuple) -> CalibrationPoint | None:
    """Worker: generate a calibration file if missing, then measure it."""
    from squishy.core.fs import write_bytes_atomic

    generator, tag, path, size, gen_fn, gen_args, params = args
    if not path.exists():
        seed = _make_seed(f"cal:{tag}")
        data = gen_fn(*gen_args, seed)
        write_bytes_atomic(path, data)
    m = _measure(path)
    return CalibrationPoint(
        generator=generator,
        params=params,
        H=m.H, S=m.S, H_label=m.H_label, S_label=m.S_label,
    )


def _build_work_items(cal_dir: Path, size: int) -> list[tuple]:
    """Build the list of (generator, tag, path, size, gen_fn, gen_args, params) tuples."""
    items: list[tuple] = []

    # markov
    for k in MARKOV_K_VALUES:
        for tau in MARKOV_TAU_VALUES:
            tag = f"markov-k{k}-tau{tau:.3f}"
            path = cal_dir / f"{tag}.bin"
            items.append((
                "markov", tag, path, size,
                gen_markov, (size, k, tau),
                {"k": k, "tau": tau},
            ))

    # lz77-synth (curated 40-config grid)
    for cfg in LZ77_CONFIGS:
        M, mean_L, window, lit_H = cfg["M"], cfg["mean_L"], cfg["window"], cfg["lit_H"]
        tag = f"lz77-M{M:.2f}-L{mean_L}-W{window}-H{lit_H:.1f}"
        path = cal_dir / f"{tag}.bin"
        items.append((
            "lz77", tag, path, size,
            gen_lz77, (size, M, mean_L, window, lit_H),
            {"M": M, "mean_L": mean_L, "window": window, "lit_H": lit_H},
        ))

    # periodic (structured and shuffled)
    for period in PERIODIC_P_VALUES:
        for profile in PERIODIC_PROFILES:
            for variant in ["structured", "shuffled"]:
                tag = f"periodic-P{period}-{profile}-{variant}"
                path = cal_dir / f"{tag}.bin"
                items.append((
                    "periodic", tag, path, size,
                    gen_periodic, (size, period, profile, variant),
                    {"period": period, "profile": profile, "variant": variant},
                ))

    return items


def calibration_sweep(out_dir: Path, workers: int = 4) -> list[CalibrationPoint]:
    """Run generators over the curated parameter grid at 4 MB, measure (H, S).

    Files are written to out_dir/calibration/ and retained so the sweep can
    be resumed. Parallelizes generation; measurement runs sequentially (codec
    processes are already CPU-bound and contend with each other).
    """
    cal_dir = out_dir / "calibration"
    cal_dir.mkdir(parents=True, exist_ok=True)

    size = CALIBRATION_SIZE
    items = _build_work_items(cal_dir, size)

    # Generate missing files in parallel; measure sequentially to avoid
    # codec subprocess contention (zstd/bzip2/zpaq are each multi-threaded)
    missing = [it for it in items if not it[2].exists()]
    if missing:
        print(f"Calibration sweep: generating {len(missing)} new files"
              f" ({workers} workers)…")
        from squishy.core.fs import write_bytes_atomic

        def _gen_only(args: tuple) -> tuple[Path, bytes]:
            generator, tag, path, size_, gen_fn, gen_args, params = args
            seed = _make_seed(f"cal:{tag}")
            data = gen_fn(*gen_args, seed)
            return path, data

        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            for path, data in pool.map(_gen_only, missing):
                if not path.exists():
                    write_bytes_atomic(path, data)

    print(f"Calibration sweep: measuring {len(items)} files…")
    results: list[CalibrationPoint] = []
    for it in items:
        generator, tag, path, *rest = it
        params = it[6]
        m = _measure(path)
        cp = CalibrationPoint(
            generator=generator,
            params=params,
            H=m.H, S=m.S, H_label=m.H_label, S_label=m.S_label,
        )
        results.append(cp)
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
