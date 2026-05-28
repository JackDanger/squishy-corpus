#!/usr/bin/env python3
"""Measure v4 corpus metrics (H, S, LZ diagnostics, NCD) for corpus files.

Produces a manifest.csv with columns defined in squishy/corpus/measure.py.
The S driver runs three reference codecs (zstd --long=27 -19, bzip2 -9,
zpaq -m5) per file; this is the slow path. Use --skip-s for a fast scan
that omits S and per-codec rates.

Usage:
    uv run scripts/measure-corpus.py
    uv run scripts/measure-corpus.py --dirs build/raw/natural build/raw/synthetic
    uv run scripts/measure-corpus.py --skip-s --skip-ncd   # fast: H and LZ only
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from squishy.corpus.measure import measure_file, FIELDNAMES


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dirs", nargs="+", default=["build/raw/natural"],
                        help="Directories to scan for corpus files")
    parser.add_argument("--out", default="build/bench/manifest.csv",
                        help="Output CSV path")
    parser.add_argument("--extensions", default="",
                        help="Comma-separated extensions to include (default: all)")
    parser.add_argument("--skip-s", action="store_true",
                        help="Skip S driver (fast mode; S columns are empty)")
    parser.add_argument("--skip-ncd", action="store_true",
                        help="Skip NCD halves (fast mode)")
    parser.add_argument("--domain", default="",
                        help="Domain label to assign to all files (e.g. text-english)")
    parser.add_argument("--corpus", default="natural",
                        help="Corpus type: 'natural' or 'synthetic'")
    args = parser.parse_args()

    exts = set(args.extensions.split(",")) - {""} if args.extensions else None
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for d in args.dirs:
        p = ROOT / d if not Path(d).is_absolute() else Path(d)
        if not p.exists():
            print(f"  WARN: {p} does not exist, skipping", file=sys.stderr)
            continue
        for f in sorted(p.rglob("*")):
            if not f.is_file():
                continue
            if f.suffix in {".json", ".md", ".csv", ".txt"}:
                continue
            if exts and f.suffix not in exts:
                continue
            files.append(f)

    if not files:
        print("No files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Measuring {len(files)} files …\n")
    rows: list[dict] = []
    for f in files:
        size_mb = f.stat().st_size / 1e6
        print(f"  {f.parent.name}/{f.name}  ({size_mb:.1f} MB) …", end="", flush=True)
        row = measure_file(
            f,
            domain=args.domain,
            corpus=args.corpus,
            skip_s=args.skip_s,
            skip_ncd=args.skip_ncd,
        )
        rows.append(row)

        h_s = f"H={row['H']:.3f}" if row["H"] is not None else "H=?"
        s_s = f"S={row['S']:.3f}" if row["S"] is not None else "S=--"
        lp_s = f"Lp90={row['Lp90_lz77_32k']}" if row["Lp90_lz77_32k"] is not None else ""
        print(f"  {h_s}  {s_s}  {row['H_bin']}/{row['S_bin'] or '--'}  {lp_s}")

    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows → {out_path}")

    print("\n── Corpus coordinates ──────────────────────────────────────────────────")
    print(f"  {'file':35s}  H(bpb)  S      H_bin  S_bin")
    for row in sorted(rows, key=lambda r: (r["H"] or 0)):
        fname = f"{Path(str(row['path'])).parent.name}/{Path(str(row['path'])).name}"
        h_s = f"{row['H']:.3f}" if row["H"] is not None else "  -  "
        s_s = f"{row['S']:.3f}" if row["S"] is not None else "  -  "
        print(f"  {fname:35s}  {h_s}  {s_s}  {row['H_bin']}  {row['S_bin'] or '--'}")


if __name__ == "__main__":
    main()
