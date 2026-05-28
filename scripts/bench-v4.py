#!/usr/bin/env python3
"""v4 benchmark harness: run codec suite over pilot synthetic corpus files.

Compresses each file with each codec; records bpb and wall time.
Computes pairwise Kendall-τ between codec orderings per H×S cell.

Output:
  build/bench/v4-bench.csv       (one row per file × codec)
  build/bench/v4-kendall-tau.csv (one row per H×S cell × codec pair)
  build/bench/v4-coverage.txt    (H×S coverage map)

Usage:
    uv run scripts/bench-v4.py --input build/raw/synthetic/calibration
    uv run scripts/bench-v4.py --input build/raw/synthetic/calibration --codecs zstd-1,zstd-19,bzip2-9,zpaq-m5
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from squishy.corpus.axes import h_label, s_label, H_LABELS, S_LABELS
from squishy.corpus.metrics import byte_entropy
from squishy.corpus.s_driver import measure_s

# ── Codec definitions ──────────────────────────────────────────────────────────

CODECS: dict[str, list[str]] = {
    "zstd-1":    ["zstd", "-1",  "-T1", "-c", "-q", "--no-progress", "{INPUT}"],
    "zstd-9":    ["zstd", "-9",  "-T1", "-c", "-q", "--no-progress", "{INPUT}"],
    "zstd-19":   ["zstd", "-19", "-T1", "-c", "-q", "--no-progress", "{INPUT}"],
    "zstd-long": ["zstd", "--long=27", "-19", "-T1", "-c", "-q", "--no-progress", "{INPUT}"],
    "bzip2-9":   ["bzip2", "-9", "-k", "-c", "{INPUT}"],
    "zpaq-m5":   None,   # handled specially (writes archive, not stdout)
    "xz-6":      ["xz", "-6", "-T1", "-k", "-c", "{INPUT}"],
    "brotli-6":  ["brotli", "-6", "-k", "-c", "{INPUT}"],
}

DEFAULT_CODECS = ["zstd-1", "zstd-19", "bzip2-9", "zpaq-m5"]


def _codec_available(name: str) -> bool:
    cmd = "brotli" if name.startswith("brotli") else name.split("-")[0]
    return subprocess.run(["which", cmd], capture_output=True).returncode == 0


def compress_file(path: Path, codec: str) -> tuple[int, float]:
    """Return (compressed_bytes, wall_seconds). Returns (-1, elapsed) on error."""
    import tempfile, os

    if codec == "zpaq-m5":
        with tempfile.NamedTemporaryFile(suffix=".zpaq", delete=False) as tf:
            arc = tf.name
        try:
            t0 = time.monotonic()
            r = subprocess.run(
                ["zpaq", "a", arc, str(path), "-m5"],
                capture_output=True, timeout=300,
            )
            elapsed = time.monotonic() - t0
            if r.returncode != 0:
                return -1, elapsed
            return os.path.getsize(arc), elapsed
        except Exception:
            return -1, time.monotonic() - t0
        finally:
            try:
                os.unlink(arc)
            except OSError:
                pass

    cmd_tmpl = CODECS[codec]
    cmd = [c if c != "{INPUT}" else str(path) for c in cmd_tmpl]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        elapsed = time.monotonic() - t0
        if r.returncode != 0:
            print(f"  WARN: {codec} failed on {path.name} (rc={r.returncode})",
                  file=sys.stderr)
            return -1, elapsed
        return len(r.stdout), elapsed
    except subprocess.TimeoutExpired:
        return -1, time.monotonic() - t0


def kendall_tau(rank1: list[int], rank2: list[int]) -> float:
    """Kendall-τ between two orderings (higher = more concordant)."""
    n = len(rank1)
    if n < 2:
        return float("nan")
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            s1 = rank1[i] - rank1[j]
            s2 = rank2[i] - rank2[j]
            if s1 * s2 > 0:
                concordant += 1
            elif s1 * s2 < 0:
                discordant += 1
    denom = n * (n - 1) // 2
    return (concordant - discordant) / denom if denom > 0 else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default="build/raw/synthetic/calibration",
                        help="Directory of .bin files to benchmark")
    parser.add_argument("--codecs", default=",".join(DEFAULT_CODECS),
                        help=f"Comma-separated codec names (default: {','.join(DEFAULT_CODECS)})")
    parser.add_argument("--out-dir", default="build/bench",
                        help="Output directory for CSV results")
    args = parser.parse_args()

    input_dir = ROOT / args.input
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    codec_names = [c.strip() for c in args.codecs.split(",")]
    for c in codec_names:
        if c not in CODECS:
            print(f"ERROR: unknown codec '{c}'. Valid: {list(CODECS)}", file=sys.stderr)
            sys.exit(1)
        if not _codec_available(c):
            print(f"ERROR: codec '{c}' not found in PATH", file=sys.stderr)
            sys.exit(1)

    bin_files = sorted(input_dir.glob("*.bin"))
    if not bin_files:
        print(f"ERROR: no .bin files found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    # Load cached measurements if available (avoids re-running bzip2/zpaq, ~30 min).
    # Prefer <dir-name>-results.json (produced by gen-balanced.py or gen-synthetic.py)
    # then fall back to calibration-results.json for backwards compatibility.
    _candidates = [
        input_dir.parent / f"{input_dir.name}-results.json",
        input_dir.parent / "calibration-results.json",
    ]
    _cal_json = next((p for p in _candidates if p.exists()), _candidates[-1])
    _hs_cache: dict[str, dict] = {}
    if _cal_json.exists():
        import json as _json
        for rec in _json.loads(_cal_json.read_text()):
            if rec.get("filename"):
                _hs_cache[rec["filename"]] = {
                    "H": rec["H"], "S": rec["S"],
                    "H_bin": rec["H_label"], "S_bin": rec["S_label"],
                    "bpb_zstd_long": rec.get("bpb_zstd_long"),
                    "bpb_bzip2_9": rec.get("bpb_bzip2_9"),
                    "bpb_zpaq_m5": rec.get("bpb_zpaq_m5"),
                }
        if _hs_cache:
            print(f"Loaded cached H/S+codec bpb for {len(_hs_cache)} files from {_cal_json.name}")

    print(f"Benchmarking {len(bin_files)} files × {len(codec_names)} codecs…")

    bench_path = out_dir / "v4-bench.csv"
    bench_fields = ["filename", "size_bytes", "H", "H_bin", "S", "S_bin",
                    "codec", "compressed_bytes", "bpb", "compress_time_s"]

    rows: list[dict] = []
    file_meta: dict[str, dict] = {}  # filename → {H, S, H_bin, S_bin, size_bytes}

    for i, path in enumerate(bin_files):
        data = path.read_bytes()
        size_bytes = len(data)
        if path.name in _hs_cache:
            cached = _hs_cache[path.name]
            H, S, hl, sl = cached["H"], cached["S"], cached["H_bin"], cached["S_bin"]
        else:
            H = byte_entropy(data)
            s_result = measure_s(path)
            S = s_result.S
            hl = h_label(H)
            sl = s_label(S)
        file_meta[path.name] = {
            "H": H, "S": S, "H_bin": hl, "S_bin": sl,
            "size_bytes": size_bytes,
        }
        print(f"  [{i+1}/{len(bin_files)}] {path.name} → {hl}/{sl} (H={H:.3f} S={S:.3f})")

        cached = _hs_cache.get(path.name, {})
        _CACHED_CODEC = {
            "zstd-long": cached.get("bpb_zstd_long"),
            "bzip2-9":   cached.get("bpb_bzip2_9"),
            "zpaq-m5":   cached.get("bpb_zpaq_m5"),
        }
        for codec in codec_names:
            cached_bpb = _CACHED_CODEC.get(codec)
            if cached_bpb is not None:
                bpb, nbytes, elapsed = cached_bpb, round(cached_bpb * size_bytes / 8), 0.0
            else:
                nbytes, elapsed = compress_file(path, codec)
                bpb = nbytes * 8.0 / size_bytes if nbytes > 0 else None
            rows.append({
                "filename":        path.name,
                "size_bytes":      size_bytes,
                "H":               round(H, 4),
                "H_bin":           hl,
                "S":               round(S, 4),
                "S_bin":           sl,
                "codec":           codec,
                "compressed_bytes": nbytes,
                "bpb":             round(bpb, 4) if bpb else "",
                "compress_time_s": round(elapsed, 3),
            })

    with open(bench_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=bench_fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows → {bench_path}")

    # ── Kendall-τ per H×S cell ─────────────────────────────────────────────────

    from collections import defaultdict
    cells: dict[tuple[str, str], list[str]] = defaultdict(list)
    for fname, meta in file_meta.items():
        cells[(meta["H_bin"], meta["S_bin"])].append(fname)

    tau_rows: list[dict] = []
    print("\n── Kendall-τ between codec orderings ───────────────────────────────────────")
    for (hb, sb), fnames in sorted(cells.items()):
        if len(fnames) < 3:
            continue
        # Build bpb rankings per codec for files in this cell
        codec_bpb: dict[str, list[float]] = {c: [] for c in codec_names}
        cell_files: list[str] = []
        for fname in fnames:
            bpbs = {}
            valid = True
            for codec in codec_names:
                match = [r for r in rows if r["filename"] == fname and r["codec"] == codec]
                if not match or match[0]["bpb"] == "":
                    valid = False
                    break
                bpbs[codec] = float(match[0]["bpb"])
            if not valid:
                continue
            cell_files.append(fname)
            for c, bpb in bpbs.items():
                codec_bpb[c].append(bpb)

        if len(cell_files) < 3:
            continue

        # Ranks (ascending bpb = better compression = lower rank index)
        def _ranks(vals: list[float]) -> list[int]:
            indexed = sorted(range(len(vals)), key=lambda i: vals[i])
            ranks = [0] * len(vals)
            for rank, idx in enumerate(indexed):
                ranks[idx] = rank
            return ranks

        codec_ranks = {c: _ranks(codec_bpb[c]) for c in codec_names}

        for i, c1 in enumerate(codec_names):
            for c2 in codec_names[i+1:]:
                tau = kendall_tau(codec_ranks[c1], codec_ranks[c2])
                tau_rows.append({
                    "H_bin": hb, "S_bin": sb, "n_files": len(cell_files),
                    "codec_a": c1, "codec_b": c2,
                    "kendall_tau": round(tau, 4),
                })
                flag = " *** DISAGREE" if tau < 0.8 else ""
                print(f"  {hb}/{sb}  {c1} vs {c2}: τ={tau:.3f}{flag}")

    tau_path = out_dir / "v4-kendall-tau.csv"
    tau_fields = ["H_bin", "S_bin", "n_files", "codec_a", "codec_b", "kendall_tau"]
    with open(tau_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=tau_fields)
        writer.writeheader()
        writer.writerows(tau_rows)
    print(f"\nWrote {len(tau_rows)} τ rows → {tau_path}")

    # ── Coverage map ───────────────────────────────────────────────────────────

    coverage_path = out_dir / "v4-coverage.txt"
    with open(coverage_path, "w") as f:
        f.write("H×S coverage map (file count per cell)\n\n")
        f.write(f"{'':6s}")
        for sl in S_LABELS:
            f.write(f"  {sl:4s}")
        f.write("\n")
        for hl in H_LABELS:
            f.write(f"  {hl:4s}")
            for sl in S_LABELS:
                count = len(cells.get((hl, sl), []))
                f.write(f"  {count:4d}")
            f.write("\n")
    print(f"Wrote coverage map → {coverage_path}")

    # Print disagreement summary
    disagreements = [(r["H_bin"], r["S_bin"], r["codec_a"], r["codec_b"], r["kendall_tau"])
                     for r in tau_rows if float(r["kendall_tau"]) < 0.8]
    if disagreements:
        print(f"\n{len(disagreements)} cell×pair(s) with τ < 0.8 (codec families disagree):")
        for hb, sb, ca, cb, tau in sorted(disagreements, key=lambda x: x[4]):
            print(f"  {hb}/{sb}: {ca} vs {cb} τ={tau:.3f}")
    else:
        print("\nNo codec disagreements found (τ ≥ 0.8 for all pairs).")


if __name__ == "__main__":
    main()
