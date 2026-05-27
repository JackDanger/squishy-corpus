#!/usr/bin/env python3
"""Benchmark harness: generate calibrated corpus files and run codec sweep.

Generates the H×M factorial calibrated grid at 256K and 4M sizes (skips 64M
for speed), then runs each file through a suite of codecs and records
compressed size, wall time, and rate_ratio = compressed / reference_bytes.

NOTE: R_ref (and therefore reference_bytes) is the reference-coder rate under
the construction parse — NOT the Shannon entropy rate. Rate_ratio < 1.0 is
possible for strong coders that discover the LZ structure independently.

Output: build/bench/calibrated-bench.csv  (one row per file × codec)
        build/bench/calibrated-ci.csv     (one row per H×M×codec, mean ± 95% CI)

Usage:
    python scripts/run-bench.py                      # 256K + 4M, 3 seeds
    python scripts/run-bench.py --sizes 256K         # 256K only (fast)
    python scripts/run-bench.py --seeds 10           # more replicates for tighter CI
    python scripts/run-bench.py --no-generate        # skip file generation
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from squishy.core.fs import write_bytes_atomic
from squishy.generators.calibrated import (
    H_VALUES,
    L_VALUES,
    L_SWEEP_M,
    M_VALUES,
    generate_file,
    ground_truth_record,
)

# ── Codec definitions ──────────────────────────────────────────────────────────
# Each entry: (name, [cmd, ...]) where {INPUT} is replaced with the file path.
# All codecs write compressed output to stdout.

CODECS: list[tuple[str, list[str]]] = [
    ("zstd-3",   ["zstd", "-3",  "-T1", "-c", "-q", "--no-progress", "{INPUT}"]),
    ("zstd-19",  ["zstd", "-19", "-T1", "-c", "-q", "--no-progress", "{INPUT}"]),
    ("gzip-9",   ["gzip", "-9", "-n", "-k", "-c", "{INPUT}"]),
    ("bzip2-9",  ["bzip2", "-9", "-k", "-c", "{INPUT}"]),
    ("xz-6",     ["xz", "-6", "-T1", "-k", "-c", "{INPUT}"]),
    ("brotli-6", ["brotli", "-6", "-k", "-c", "{INPUT}"]),
    ("lz4-1",    ["lz4", "-1", "-k", "-c", "-q", "{INPUT}"]),
    ("lz4-9",    ["lz4", "-9", "-k", "-c", "-q", "{INPUT}"]),
]

ZPAQ_AVAILABLE = bool(subprocess.run(["which", "zpaq"], capture_output=True).returncode == 0)

ALL_SIZES: dict[str, int] = {
    "256K":  262144,
    "4M":   4194304,
    "64M": 67108864,
}


def compress(inp: Path, cmd_template: list[str]) -> tuple[int, float, bytes]:
    """Run codec, return (compressed_bytes, wall_time_s, data). Returns (-1, -1, b'') on error."""
    cmd = [c if c != "{INPUT}" else str(inp) for c in cmd_template]
    t0 = time.monotonic()
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        elapsed = time.monotonic() - t0
        if result.returncode != 0:
            print(f"  WARN: {cmd[0]} exited {result.returncode} on {inp.name}",
                  file=sys.stderr)
            return -1, elapsed, b""
        return len(result.stdout), elapsed, result.stdout
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        print(f"  WARN: {cmd[0]} timed out on {inp.name}", file=sys.stderr)
        return -1, elapsed, b""


_DECOMPRESS_CMD: dict[str, list[str]] = {
    "zstd-3":   ["zstd", "-d", "-c", "-q", "--no-progress"],
    "zstd-19":  ["zstd", "-d", "-c", "-q", "--no-progress"],
    "gzip-9":   ["gzip", "-d", "-c"],
    "bzip2-9":  ["bzip2", "-d", "-c"],
    "xz-6":     ["xz", "-d", "-c"],
    "brotli-6": ["brotli", "-d", "-c"],
    "lz4-1":    ["lz4", "-d", "-c", "-q"],
    "lz4-9":    ["lz4", "-d", "-c", "-q"],
}


def decompress_time(compressed_data: bytes, codec_name: str) -> float:
    """Decompress from stdin and return wall time in seconds. -1 on error."""
    cmd = _DECOMPRESS_CMD.get(codec_name)
    if cmd is None:
        return -1.0
    t0 = time.monotonic()
    try:
        result = subprocess.run(cmd, input=compressed_data, capture_output=True,
                                timeout=300)
        elapsed = time.monotonic() - t0
        if result.returncode != 0:
            return -1.0
        return elapsed
    except (subprocess.TimeoutExpired, Exception):
        return -1.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="256K,4M",
                        help="Comma-separated size tiers to bench (default: 256K,4M)")
    parser.add_argument("--seeds", type=int, default=3, metavar="N",
                        help="Number of random seeds to generate (s0..sN-1, default 3, max 10)")
    parser.add_argument("--no-generate", action="store_true",
                        help="Skip file generation; use existing files only")
    parser.add_argument("--out", default="build/bench/calibrated-bench.csv",
                        help="Output CSV path")
    parser.add_argument("--raw-dir", default="build/raw/calibrated",
                        help="Directory for generated raw files")
    args = parser.parse_args()

    if not (1 <= args.seeds <= 10):
        print(f"ERROR: --seeds must be between 1 and 10 (got {args.seeds})", file=sys.stderr)
        sys.exit(1)
    replicates = [f"s{i}" for i in range(args.seeds)]

    sizes_to_bench = []
    for s in args.sizes.split(","):
        s = s.strip()
        if s not in ALL_SIZES:
            print(f"Unknown size: {s}. Valid: {list(ALL_SIZES)}", file=sys.stderr)
            sys.exit(1)
        sizes_to_bench.append((s, ALL_SIZES[s]))

    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    ci_path = out_path.parent / out_path.name.replace("-bench.csv", "-ci.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Generate files ─────────────────────────────────────────────────────────

    if not args.no_generate:
        print(f"Generating calibrated files in {raw_dir} …")
        total_gen = 0
        for size_label, size in sizes_to_bench:
            # zeros baseline
            zeros_path = raw_dir / f"{size_label}-zeros-s0.bin"
            if not zeros_path.exists():
                write_bytes_atomic(zeros_path, b"\x00" * size)
                print(f"  {zeros_path.name}")
                total_gen += 1

            for H in H_VALUES:
                for M in M_VALUES:
                    for rep in replicates:
                        H_str = f"H{H:.1f}".replace(".", "p")
                        M_str = f"M{M:.2f}".replace(".", "p")
                        fname = f"{size_label}-{H_str}-{M_str}-{rep}.bin"
                        path = raw_dir / fname
                        if not path.exists():
                            data = generate_file(size, H, M, rep)
                            write_bytes_atomic(path, data)
                            total_gen += 1
                            if total_gen % 20 == 0:
                                print(f"  … {total_gen} files generated")
        print(f"  Generation complete: {total_gen} new files")

        # Write ground-truth sidecar
        gt_records = []
        for size_label, size in sizes_to_bench:
            gt_records.append({
                "filename": f"{size_label}-zeros-s0.bin", "size_bytes": size,
                "H_marginal": 0.0, "R_ref": 0.0, "M_fraction": 0.0,
                "reference_bytes": 0, "generator": "zeros",
            })
            for H in H_VALUES:
                for M in M_VALUES:
                    for rep in replicates:
                        H_str = f"H{H:.1f}".replace(".", "p")
                        M_str = f"M{M:.2f}".replace(".", "p")
                        fname = f"{size_label}-{H_str}-{M_str}-{rep}.bin"
                        rec = ground_truth_record(size_label, size, H, M, rep, fname)
                        gt_records.append(rec)
        write_bytes_atomic(
            raw_dir / "ground-truth.json",
            json.dumps(gt_records, indent=2).encode()
        )

    # ── Load ground truth ──────────────────────────────────────────────────────

    gt_path = raw_dir / "ground-truth.json"
    if not gt_path.exists():
        print(f"ERROR: {gt_path} not found. Run without --no-generate first.",
              file=sys.stderr)
        sys.exit(1)

    ground_truth: dict[str, dict] = {
        rec["filename"]: rec
        for rec in json.loads(gt_path.read_text())
    }

    # ── Benchmark loop ─────────────────────────────────────────────────────────

    rows: list[dict] = []
    files_to_bench = [
        p for size_label, _ in sizes_to_bench
        for p in sorted(raw_dir.glob(f"{size_label}-*.bin"))
        if p.name in ground_truth and ground_truth[p.name].get("R_ref", 0) > 0
    ]

    total_files = len(files_to_bench)
    total_runs = total_files * len(CODECS)
    print(f"\nBenchmarking {total_files} files × {len(CODECS)} codecs = {total_runs} runs …")
    print(f"Output → {out_path}\n")

    below_one: list[tuple[str, str, float]] = []  # (filename, codec, rate_ratio)

    for i, inp_path in enumerate(files_to_bench):
        gt = ground_truth[inp_path.name]
        reference_bytes = gt["reference_bytes"]
        R_ref = gt["R_ref"]
        H_m = gt["H_marginal"]
        M_f = gt.get("M_fraction", 0.0)

        size_bytes = gt["size_bytes"]
        for codec_name, cmd_template in CODECS:
            compressed_bytes, wall_time, compressed_data = compress(
                inp_path, cmd_template
            )
            if compressed_bytes < 0:
                rate_ratio = None
                bpb = None
                decomp_s = None
            else:
                rate_ratio = (compressed_bytes / reference_bytes
                              if reference_bytes > 0 else None)
                bpb = compressed_bytes * 8.0 / size_bytes if size_bytes > 0 else None
                decomp_s = decompress_time(compressed_data, codec_name)
                if decomp_s < 0:
                    decomp_s = None

            row = {
                "filename":           inp_path.name,
                "size_bytes":         size_bytes,
                "H_marginal":         H_m,
                "M_fraction":         M_f,
                "R_ref":              R_ref,
                "reference_bytes":    reference_bytes,
                "codec":              codec_name,
                "compressed_bytes":   compressed_bytes,
                "bpb":                round(bpb, 4) if bpb is not None else "",
                "rate_ratio":         round(rate_ratio, 5) if rate_ratio is not None else "",
                "compress_time_s":    round(wall_time, 3),
                "decompress_time_s":  round(decomp_s, 3) if decomp_s is not None else "",
            }
            rows.append(row)

            if rate_ratio is not None and rate_ratio < 1.0:
                below_one.append((inp_path.name, codec_name, rate_ratio, bpb))
                flag = " *** BELOW R_ref ***"
            else:
                flag = ""

            if (i * len(CODECS)) % 100 == 0:
                ratio_str = f"{rate_ratio:.4f}" if rate_ratio is not None else "ERR"
                bpb_str = f"{bpb:.3f}" if bpb is not None else "ERR"
                print(f"  [{i+1}/{total_files}] {inp_path.name:40s} {codec_name:10s} "
                      f"bpb={bpb_str} ratio={ratio_str}{flag}")

    # ── Write CSV ──────────────────────────────────────────────────────────────

    fieldnames = ["filename", "size_bytes", "H_marginal", "M_fraction", "R_ref",
                  "reference_bytes", "codec", "compressed_bytes", "bpb",
                  "rate_ratio", "compress_time_s", "decompress_time_s"]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {out_path}")

    # ── CI aggregation (per size×H×M×codec, across seeds) ─────────────────────

    # t-critical for 95% two-sided CI; index = df = n-1
    _T95 = [0.0, 12.706, 4.303, 3.182, 2.776, 2.571,
            2.447, 2.365, 2.306, 2.262, 2.228]  # df 0..10

    ci_groups: dict[tuple, list] = {}
    for row in rows:
        if row["rate_ratio"] == "" or row["bpb"] == "":
            continue
        key = (row["size_bytes"], row["H_marginal"], row["M_fraction"], row["codec"])
        ci_groups.setdefault(key, []).append(
            (float(row["rate_ratio"]), float(row["bpb"]))
        )

    def _ci(vals: list[float]) -> tuple[float, float, float]:
        n = len(vals)
        mean = sum(vals) / n
        if n > 1:
            var = sum((v - mean) ** 2 for v in vals) / (n - 1)
            se = math.sqrt(var / n)
            t = _T95[min(n - 1, 10)]
            return mean, mean - t * se, mean + t * se
        return mean, mean, mean

    ci_rows: list[dict] = []
    for (sz, H, M, codec), pairs in sorted(ci_groups.items()):
        ratios, bpbs = zip(*pairs)
        n = len(ratios)
        r_mean, r_lo, r_hi = _ci(list(ratios))
        b_mean, b_lo, b_hi = _ci(list(bpbs))
        ci_rows.append({
            "size_bytes": sz, "H_marginal": H, "M_fraction": M, "codec": codec,
            "n_seeds": n,
            "mean_bpb": round(b_mean, 4), "bpb_ci95_lo": round(b_lo, 4),
            "bpb_ci95_hi": round(b_hi, 4),
            "mean_rate_ratio": round(r_mean, 5), "ci95_lo": round(r_lo, 5),
            "ci95_hi": round(r_hi, 5),
        })

    ci_fieldnames = ["size_bytes", "H_marginal", "M_fraction", "codec",
                     "n_seeds", "mean_bpb", "bpb_ci95_lo", "bpb_ci95_hi",
                     "mean_rate_ratio", "ci95_lo", "ci95_hi"]
    with open(ci_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ci_fieldnames)
        writer.writeheader()
        writer.writerows(ci_rows)
    print(f"Wrote {len(ci_rows)} CI rows to {ci_path}")

    # ── Summary ────────────────────────────────────────────────────────────────

    print("\n── Per-codec median rate_ratio ──────────────────────────────────────────")
    by_codec: dict[str, list[float]] = {}
    for row in rows:
        if row["rate_ratio"] != "":
            by_codec.setdefault(row["codec"], []).append(float(row["rate_ratio"]))

    for codec, ratios in sorted(by_codec.items()):
        ratios.sort()
        median = ratios[len(ratios) // 2]
        minimum = min(ratios)
        maximum = max(ratios)
        print(f"  {codec:12s}  median={median:.4f}  min={minimum:.4f}  max={maximum:.4f}")

    if below_one:
        print(f"\n*** {len(below_one)} rate_ratio < 1.0 cases (codec beat R_ref) ***")
        for fname, codec, rr, bp in sorted(below_one, key=lambda x: x[2]):
            gt = ground_truth[fname]
            print(f"  {fname:45s} {codec:10s} bpb={bp:.3f} ratio={rr:.4f} "
                  f"H={gt['H_marginal']:.1f} M={gt.get('M_fraction',0):.2f}")
    else:
        print("\nNo rate_ratio < 1.0 cases (all codecs stayed above R_ref).")

    print("\nDone.")


if __name__ == "__main__":
    main()
