#!/usr/bin/env python3
"""Generate build/bundle/index.html from manifest.csv.

Edit the HTML copy in scripts/bundle-index.html.
This script only handles live substitutions (file counts, τ values).

Usage:
    uv run scripts/build-bundle-html.py --bundle build/bundle
    uv run scripts/build-bundle-html.py  # uses default build/bundle
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).parent.parent
TEMPLATE_PATH = Path(__file__).parent / "bundle-index.html"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", default=str(ROOT / "build" / "bundle"),
                    help="Bundle directory containing manifest.csv (default: build/bundle)")
    ap.add_argument("--tau-zstd-bzip2", default="0.939",
                    help="Validated Kendall-τ zstd vs bzip2 (default: 0.939)")
    ap.add_argument("--tau-zstd-zpaq", default="see note",
                    help="Kendall-τ zstd vs zpaq (context-mixing out-of-family)")
    ap.add_argument("--stability", default="40/40",
                    help="Per-cell winner stability e.g. 40/40")
    ap.add_argument("--unclamped-cells", default="33",
                    help="Number of unclamped cells used for headline τ")
    args = ap.parse_args()

    bundle_dir = Path(args.bundle)
    manifest_path = bundle_dir / "manifest.csv"
    if not manifest_path.exists():
        print(f"ERROR: manifest.csv not found at {manifest_path}", file=sys.stderr)
        sys.exit(1)

    if not TEMPLATE_PATH.exists():
        print(f"ERROR: template not found at {TEMPLATE_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path, newline="") as f:
        rows = list(csv.DictReader(f))

    calibrated_count = sum(1 for r in rows if r.get("source_type") == "calibrated")
    natural_count    = sum(1 for r in rows if r.get("source_type") == "natural")
    cell_count       = len({r["cell"] for r in rows})

    html = Template(TEMPLATE_PATH.read_text()).substitute(
        calibrated_count=calibrated_count,
        natural_count=natural_count,
        cell_count=cell_count,
        tau_zstd_bzip2=args.tau_zstd_bzip2,
        tau_zstd_zpaq=args.tau_zstd_zpaq,
        stability=args.stability,
        unclamped_cells=args.unclamped_cells,
    )

    out = bundle_dir / "index.html"
    out.write_text(html)
    print(f"Wrote {out} ({len(html):,} bytes, {calibrated_count} cal + {natural_count} nat, {cell_count} cells)")


if __name__ == "__main__":
    main()
