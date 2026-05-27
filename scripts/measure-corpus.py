#!/usr/bin/env python3
"""Measure full (H, M, L, σ_H, NCD) coordinates for corpus files.

For metric definitions see squishy/corpus/metrics.py and plans/corpus-v4.md.

Usage:
    uv run scripts/measure-corpus.py
    uv run scripts/measure-corpus.py --dirs build/raw/silesia build/raw/candidates
    uv run scripts/measure-corpus.py --no-bootstrap   # fast: skip CIs
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from squishy.corpus.measure import measure_file, FIELDNAMES


def _load_ground_truth(dirs: list[Path]) -> dict[str, dict]:
    """Load ground-truth.json from any scanned directory. Returns filename → entry."""
    lookup: dict[str, dict] = {}
    for d in dirs:
        gt_path = d / "ground-truth.json"
        if gt_path.exists():
            entries = json.loads(gt_path.read_text())
            for entry in entries:
                lookup[entry["filename"]] = entry
    return lookup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dirs", nargs="+", default=["build/raw/silesia"],
                        help="Directories to scan for corpus files")
    parser.add_argument("--out", default="build/bench/corpus-measurements.csv",
                        help="Output CSV path")
    parser.add_argument("--extensions", default="",
                        help="Comma-separated extensions to include (default: all)")
    parser.add_argument("--no-bootstrap", action="store_true",
                        help="Skip bootstrap CIs, sigma_H, and NCD (fast mode)")
    args = parser.parse_args()

    exts = set(args.extensions.split(",")) - {""} if args.extensions else None
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scan_dirs: list[Path] = []
    files: list[Path] = []
    for d in args.dirs:
        p = ROOT / d if not Path(d).is_absolute() else Path(d)
        if not p.exists():
            print(f"  WARN: {p} does not exist, skipping", file=sys.stderr)
            continue
        scan_dirs.append(p)
        for f in sorted(p.iterdir()):
            if not f.is_file():
                continue
            if f.suffix in {".json", ".md"}:
                continue  # metadata files, not corpus data
            if exts and f.suffix not in exts:
                continue
            files.append(f)

    ground_truth = _load_ground_truth(scan_dirs)
    if ground_truth:
        print(f"Loaded ground-truth.json: {len(ground_truth)} entries")

    if not files:
        print("No files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Measuring {len(files)} files …\n")
    rows: list[dict] = []
    for f in files:
        size_mb = f.stat().st_size / 1e6
        print(f"  {f.parent.name}/{f.name}  ({size_mb:.1f} MB)", flush=True)
        gt_entry = ground_truth.get(f.name)
        row = measure_file(f, bootstrap=not args.no_bootstrap, ground_truth=gt_entry)
        rows.append(row)

        ncd_s = f"{row['ncd_halves']:.4f}" if row["ncd_halves"] is not None else "--"
        lci = row.get("L_ci_rel")
        if lci is not None:
            ci_s = f"  L_ci_rel={lci:.3f}{'  ✓' if lci < 0.15 else '  WARN: > 0.15'}"
        else:
            ci_s = ""
        print(f"    H={row['H_marginal']:.3f}  M={row['M_greedy']:.3f}"
              f"  L_med={row['L_median']}  ncd={ncd_s}{ci_s}")

    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows → {out_path}")

    print("\n── Corpus coordinates ──────────────────────────────────────────────────────────")
    print(f"  {'file':30s}  H(bpb)  M_greedy  L_med  L_p90  ncd_h  L_ci_rel")
    for row in sorted(rows, key=lambda r: (r["H_marginal"] or 0)):
        ncd_s = f"{row['ncd_halves']:.3f}" if row["ncd_halves"] is not None else "  -- "
        lci_s = f"{row['L_ci_rel']:.3f}" if row["L_ci_rel"] is not None else "  -- "
        print(f"  {row['filename']:30s}  {row['H_marginal']:6.3f}  "
              f"{row['M_greedy']:8.3f}  {str(row['L_median']):5s}  "
              f"{str(row['L_p90']):5s}  {ncd_s:6s}  {lci_s}")


if __name__ == "__main__":
    main()
