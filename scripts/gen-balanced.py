#!/usr/bin/env python3
"""Generate a balanced H×S corpus: 5 files per reachable (H_bin, S_bin) cell.

Reads calibration-results.json to find which generator parameters land in each
cell, then assigns 5 (config, seed) pairs per cell:
  - If ≥5 calibration configs exist: take the first 5, each with seed=0.
  - If <5 configs exist: round-robin through them, incrementing the seed when
    all configs have been used.
  - New cells (H0/S4, H5/S1, H5/S2) use new lz77 parameters verified
    post-generation via measurement.

Output:
  build/raw/synthetic/balanced/          -- flat .bin file tree
  build/raw/synthetic/balanced-results.json  -- H/S/codec measurements

Usage:
    uv run scripts/gen-balanced.py
    uv run scripts/gen-balanced.py --cal build/raw/synthetic/calibration-results.json
    uv run scripts/gen-balanced.py --workers 6
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from squishy.generators.v4_driver import gen_lz77, gen_markov, gen_periodic, _measure
from squishy.core.fs import write_bytes_atomic

TARGET_N = 5
FILE_SIZE = 4 * 1024 * 1024  # 4 MB


# ── New configs for cells absent from the calibration sweep ──────────────────

NEW_CELL_CONFIGS: dict[str, list[dict]] = {
    "H0/S4": [
        {"generator": "lz77", "params": {"M": 0.0, "mean_L": 8, "window": 32768, "lit_H": 0.5}},
    ],
    "H5/S1": [
        {"generator": "lz77", "params": {"M": 0.0, "mean_L": 8, "window": 32768, "lit_H": 7.0}},
    ],
    "H5/S2": [
        # Speculative: copies add LZ structure atop 7-bpb literals → S > 0.125.
        # Actual cell placement verified by measurement.
        {"generator": "lz77", "params": {"M": 0.3, "mean_L": 64, "window": 32768, "lit_H": 7.0}},
    ],
}


# ── Job dataclass ─────────────────────────────────────────────────────────────

@dataclass
class BalancedJob:
    cell: str          # "H3/S2"
    config_key: str    # unique key for the generator config
    generator: str
    params: dict
    seed_idx: int      # 0-4
    filename: str      # output filename


def _make_seed(tag: str) -> int:
    return int.from_bytes(hashlib.sha256(tag.encode()).digest()[:8], "big")


def _config_key(gen: str, params: dict) -> str:
    """Stable string key for a (generator, params) pair."""
    if gen == "lz77":
        return f"lz77-M{params['M']:.2f}-L{params['mean_L']}-W{params['window']}-H{params['lit_H']:.1f}"
    elif gen == "markov":
        return f"markov-k{params['k']}-tau{params['tau']:.3f}"
    elif gen == "periodic":
        return f"periodic-P{params['period']}-{params['profile']}-{params['variant']}"
    return str(params)


def build_jobs(cal_records: list[dict]) -> list[BalancedJob]:
    """Build the 5-per-cell job list from calibration records + new configs."""

    # Group unique (generator, params) configs by cell, preserving encounter order.
    cell_configs: dict[str, list[dict]] = defaultdict(list)
    seen_keys: dict[str, set] = defaultdict(set)
    for rec in cal_records:
        cell = f"{rec['H_label']}/{rec['S_label']}"
        key = _config_key(rec["generator"], rec["params"])
        if key not in seen_keys[cell]:
            cell_configs[cell].append({"generator": rec["generator"], "params": rec["params"]})
            seen_keys[cell].add(key)

    # Inject new cell configs for cells absent from the calibration sweep.
    for cell, cfgs in NEW_CELL_CONFIGS.items():
        for cfg in cfgs:
            key = _config_key(cfg["generator"], cfg["params"])
            if key not in seen_keys[cell]:
                cell_configs[cell].append({"generator": cfg["generator"], "params": cfg["params"]})
                seen_keys[cell].add(key)

    jobs: list[BalancedJob] = []
    for cell in sorted(cell_configs):
        all_cfgs = cell_configs[cell]
        # Use first TARGET_N distinct configs (if available), else repeat with extra seeds.
        take = all_cfgs[:TARGET_N]

        for i in range(TARGET_N):
            cfg = take[i % len(take)]
            seed_idx = i // len(take)
            gen = cfg["generator"]
            params = cfg["params"]
            ckey = _config_key(gen, params)
            cell_fs = cell.replace("/", "_")
            jobs.append(BalancedJob(
                cell=cell,
                config_key=ckey,
                generator=gen,
                params=params,
                seed_idx=seed_idx,
                filename=f"{cell_fs}_{i:03d}.bin",
            ))

    return jobs


# ── Worker ────────────────────────────────────────────────────────────────────

def _gen_job(args: tuple) -> str:
    """ProcessPoolExecutor worker: generate one file, write to disk."""
    seed_tag, path_str, generator, params = args
    path = Path(path_str)
    if path.exists():
        return path_str
    seed = _make_seed(seed_tag)
    size = FILE_SIZE
    if generator == "lz77":
        data = gen_lz77(size, params["M"], params["mean_L"], params["window"],
                        params["lit_H"], seed)
    elif generator == "markov":
        data = gen_markov(size, params["k"], params["tau"], seed)
    elif generator == "periodic":
        data = gen_periodic(size, params["period"], params["profile"],
                            params["variant"], seed)
    else:
        raise ValueError(f"Unknown generator: {generator}")
    write_bytes_atomic(path, data)
    return path_str


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cal",
                        default="build/raw/synthetic/calibration-results.json",
                        help="Path to calibration-results.json")
    parser.add_argument("--out", default="build/raw/synthetic",
                        help="Root output directory (balanced/ subdir created here)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel generation workers")
    args = parser.parse_args()

    cal_path = ROOT / args.cal
    if not cal_path.exists():
        print(f"ERROR: calibration JSON not found: {cal_path}\n"
              f"Run: uv run scripts/gen-synthetic.py --calibrate-only", file=sys.stderr)
        sys.exit(1)

    cal_records = json.loads(cal_path.read_text())
    jobs = build_jobs(cal_records)

    bal_dir = ROOT / args.out / "balanced"
    bal_dir.mkdir(parents=True, exist_ok=True)

    print(f"Balanced corpus: {len(jobs)} files across "
          f"{len(set(j.cell for j in jobs))} cells ({TARGET_N} per cell)")

    # Generate in parallel
    missing = [(j, bal_dir / j.filename) for j in jobs
               if not (bal_dir / j.filename).exists()]
    if missing:
        print(f"Generating {len(missing)} new files ({args.workers} workers)…")
        worker_args = [
            (f"bal:{j.cell}:{j.config_key}:{j.seed_idx}",
             str(bal_dir / j.filename),
             j.generator, j.params)
            for j, _ in missing
        ]
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
            for _ in pool.map(_gen_job, worker_args):
                pass
    else:
        print("All files already generated.")

    # Measure serially (codec subprocesses contend with each other)
    print(f"Measuring {len(jobs)} files (H, S, per-codec bpb)…")
    results = []
    for i, job in enumerate(jobs):
        path = bal_dir / job.filename
        m = _measure(path)
        results.append({
            "filename": job.filename,
            "generator": job.generator,
            "params": job.params,
            "target_cell": job.cell,
            "actual_cell": f"{m.H_label}/{m.S_label}",
            "H": round(m.H, 4),
            "S": round(m.S, 4),
            "H_label": m.H_label,
            "S_label": m.S_label,
            "bpb_zstd_long": round(m.bpb_zstd_long, 4),
            "bpb_bzip2_9": round(m.bpb_bzip2_9, 4),
            "bpb_zpaq_m5": round(m.bpb_zpaq_m5, 4),
        })
        hit = "✓" if m.H_label + "/" + m.S_label == job.cell else "✗ MISS"
        print(f"  [{i+1}/{len(jobs)}] {job.filename} "
              f"→ {m.H_label}/{m.S_label} (H={m.H:.3f} S={m.S:.3f}) {hit}")

    out_json = ROOT / args.out / "balanced-results.json"
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {len(results)} records → {out_json}")

    # Coverage summary
    from collections import Counter
    actual_cells = Counter(r["actual_cell"] for r in results)
    misses = [r for r in results if r["actual_cell"] != r["target_cell"]]
    print(f"\nCoverage: {len(actual_cells)} distinct cells populated")
    if misses:
        print(f"MISSES ({len(misses)} files landed outside target cell):")
        for r in misses:
            print(f"  {r['filename']}: target={r['target_cell']} actual={r['actual_cell']}")
    else:
        print("All files landed in their target cell.")


if __name__ == "__main__":
    main()
