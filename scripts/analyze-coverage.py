#!/usr/bin/env python3
"""Analyze cell coverage of the v4 H×M×L corpus grid.

Reads measurement CSVs (from measure-corpus.py) and reports:
  - Coverage map across the H×M×L grid
  - Best-fit file per populated cell
  - Empty cells (need synthetic files)

Usage:
    uv run scripts/analyze-coverage.py
    uv run scripts/analyze-coverage.py --csvs build/bench/corpus-measurements.csv \
        build/bench/candidates-measurements.csv
    uv run scripts/analyze-coverage.py --min-size-mb 4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from squishy.corpus.grid import H_LABELS, M_LABELS, L_LABELS
from squishy.corpus.coverage import (
    load_rows, cells_by_file, best_file_for_cell,
    coverage_map_lines, empty_cells,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csvs", nargs="+",
                        default=["build/bench/corpus-measurements.csv",
                                 "build/bench/candidates-measurements.csv"],
                        help="Measurement CSVs to combine")
    parser.add_argument("--min-size-mb", type=float, default=4.0,
                        help="Minimum file size in MB (default: 4)")
    args = parser.parse_args()

    min_size = int(args.min_size_mb * 1e6)
    csv_paths = [ROOT / p for p in args.csvs]
    rows = load_rows(csv_paths, min_size_bytes=min_size)
    print(f"\nLoaded {len(rows)} files ≥ {args.min_size_mb:.0f} MB\n")

    populated = cells_by_file(rows)
    print(f"Populated cells: {len(populated)} / ~{len(H_LABELS) * len(M_LABELS) * len(L_LABELS)} "
          f"theoretical (~35-45 realistic)\n")

    # Coverage map
    print("── Coverage map (H rows × M cols, L collapsed as symbols) ──────────────────────")
    for line in coverage_map_lines(populated):
        print(line)

    # Best representative per cell
    print("\n── Best representative per populated cell ────────────────────────────────────────")
    print(f"  {'Cell':42s}  {'File':40s}  H     M      L_med  MB     L_ci")
    print("  " + "-" * 110)
    for cell in sorted(populated):
        hi, mi, li = cell
        best = best_file_for_cell(populated[cell])
        lci = best["_L_ci_rel"]
        flag = "⚠ " if lci is not None and lci >= 0.15 else "  "
        label = f"{H_LABELS[hi]}/{M_LABELS[mi]}/{L_LABELS[li]}"
        mb = best["_size"] / 1e6
        lci_str = f"{lci:.3f}" if lci is not None else "--   "
        print(f"  {flag}{label:40s}  {best['_label']:40s}  "
              f"{best['_H']:4.2f}  {best['_M']:5.3f}  "
              f"{str(best['_L'] or '--'):5s}  {mb:5.1f}  {lci_str}")

    # Empty cells
    empties = empty_cells(populated)
    print(f"\n── Empty cells (need synthetic or additional downloads) ─────────────────────────")
    print(f"  {len(empties)} empty cells:")
    for hi, mi, li in empties:
        print(f"    {H_LABELS[hi]}/{M_LABELS[mi]}/{L_LABELS[li]}")

    # Summary
    total_gb = sum(r["_size"] for r in rows) / 1e9
    print(f"\n── Summary ──────────────────────────────────────────────────────────────────────")
    print(f"  Files ≥ {args.min_size_mb:.0f} MB:  {len(rows)}")
    print(f"  Populated cells:    {len(populated)}")
    print(f"  Empty cells:        {len(empties)}")
    print(f"  Total data:         {total_gb:.2f} GB")

    warn = [r for r in rows if (r["_L_ci_rel"] or 0) >= 0.15]
    if warn:
        print(f"\n  Files with unreliable L_median (L_ci_rel ≥ 0.15):")
        for r in sorted(warn, key=lambda x: -(x["_L_ci_rel"] or 0)):
            print(f"    {r['_label']:50s}  {r['_L_ci_rel']:.3f}")


if __name__ == "__main__":
    main()
