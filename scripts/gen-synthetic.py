#!/usr/bin/env python3
"""Generate v4 synthetic corpus files.

Phase 1: calibration sweep — run all generator parameters at 4 MB, measure
(H, S), build empirical coverage map. Results written to
build/raw/synthetic/calibration/.

Phase 2 (not yet implemented): target-cell generation — for each (H_bin, S_bin)
cell covered by calibration, generate files at 4 MB, 64 MB, 1 GB with
parameter values that land in the target cell.

Usage:
    uv run scripts/gen-synthetic.py --calibrate-only
    uv run scripts/gen-synthetic.py --calibrate-only --out build/raw/synthetic
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from squishy.generators.v4_driver import calibration_sweep, print_calibration_map


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="build/raw/synthetic",
                        help="Output directory for synthetic corpus files")
    parser.add_argument("--calibrate-only", action="store_true",
                        help="Only run the calibration sweep, don't generate corpus files")
    args = parser.parse_args()

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    results = calibration_sweep(out_dir)
    print_calibration_map(results)

    cal_path = out_dir / "calibration-results.json"
    cal_path.write_text(json.dumps(
        [{"filename": r.filename, "generator": r.generator, "params": r.params,
          "H": round(r.H, 4), "S": round(r.S, 4),
          "H_label": r.H_label, "S_label": r.S_label}
         for r in results],
        indent=2
    ))
    print(f"Wrote {len(results)} calibration results → {cal_path}")

    if args.calibrate_only:
        return

    print("\nTarget-cell generation not yet implemented (Phase 2 in progress).")


if __name__ == "__main__":
    main()
