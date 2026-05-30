"""Measure R×D×M coordinates for every file in a directory.

Usage:
    uv run scripts/measure-rdm.py [--input DIR] [--output FILE]

Output is a CSV with columns: file, R, D, M, r_bin, d_bin, m_bin, cell
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from squishy.corpus.parse import measure
from squishy.corpus.axes_rdm import cell_label, cell_tuple, r_label, d_label, m_label


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",  default="build/raw/silesia", metavar="DIR")
    parser.add_argument("--output", default=None, metavar="FILE",
                        help="CSV output path (default: stdout)")
    args = parser.parse_args()

    in_dir = args.input
    paths = sorted(
        os.path.join(in_dir, f)
        for f in os.listdir(in_dir)
        if os.path.isfile(os.path.join(in_dir, f))
    )

    fieldnames = ["file", "R", "D", "M", "r_bin", "d_bin", "m_bin", "cell"]
    out = open(args.output, "w", newline="") if args.output else sys.stdout
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()

    for path in paths:
        t0 = time.time()
        data = open(path, "rb").read()
        r, d, m = measure(data)
        rb, db, mb = cell_tuple(r, d, m)
        row = {
            "file":  os.path.basename(path),
            "R":     f"{r:.4f}",
            "D":     f"{d:.4f}",
            "M":     f"{m:.4f}",
            "r_bin": r_label(r),
            "d_bin": d_label(d),
            "m_bin": m_label(m),
            "cell":  cell_label(r, d, m),
        }
        writer.writerow(row)
        elapsed = time.time() - t0
        size_kb = os.path.getsize(path) // 1024
        print(
            f"  {os.path.basename(path):20s} "
            f"R={r:.3f} D={d:.3f} M={m:.3f}  "
            f"{cell_label(r, d, m):18s}  "
            f"{size_kb:6d}KB  ({elapsed:.1f}s)",
            file=sys.stderr,
        )

    if args.output:
        out.close()
        print(f"Wrote {len(paths)} rows → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
