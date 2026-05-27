#!/usr/bin/env python3
"""Select one representative file per populated (H, M, L) cell and symlink it
into build/raw/curated/.

Selection criteria (from best_file_for_cell):
  - Prefer largest file with L_ci_rel < 0.15 (reliable L_median estimate)
  - Fall back to largest overall if none qualify

The curated directory is rebuilt from scratch each run (stale symlinks removed).

Usage:
    uv run scripts/select-curated.py
    uv run scripts/select-curated.py --csvs build/bench/corpus-measurements.csv \
        build/bench/candidates-measurements.csv build/bench/calibrated-measurements.csv
    uv run scripts/select-curated.py --min-size-mb 1
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from squishy.corpus.grid import H_LABELS, M_LABELS, L_LABELS
from squishy.corpus.coverage import load_rows, cells_by_file, best_file_for_cell


RAW_ROOT = ROOT / "build" / "raw"
CURATED_DIR = ROOT / "build" / "raw" / "curated"


def _resolve_source(label: str) -> Path | None:
    """Map 'corpus/filename' back to an absolute file path.

    Searches recursively under build/raw/ since some corpora are nested
    (e.g., build/raw/candidates/pizza-chili-extracted/).
    """
    corpus, filename = label.split("/", 1)
    for d in RAW_ROOT.rglob(corpus):
        if d.is_dir():
            p = d / filename
            if p.exists():
                return p
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csvs", nargs="+",
                        default=["build/bench/corpus-measurements.csv",
                                 "build/bench/candidates-measurements.csv",
                                 "build/bench/calibrated-measurements.csv"],
                        help="Measurement CSVs to combine")
    parser.add_argument("--min-size-mb", type=float, default=0.25,
                        help="Minimum file size in MB (default: 0.25)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without creating symlinks")
    args = parser.parse_args()

    min_size = int(args.min_size_mb * 1e6)
    csv_paths = [ROOT / p for p in args.csvs]
    rows = load_rows(csv_paths, min_size_bytes=min_size)
    print(f"Loaded {len(rows)} files ≥ {args.min_size_mb:.2f} MB")

    populated = cells_by_file(rows)
    print(f"Populated cells: {len(populated)}")

    CURATED_DIR.mkdir(parents=True, exist_ok=True)

    # Remove stale symlinks from previous runs
    removed = 0
    for p in CURATED_DIR.iterdir():
        if p.is_symlink():
            p.unlink()
            removed += 1
    if removed:
        print(f"Removed {removed} stale symlinks")

    created = 0
    skipped = 0
    for cell in sorted(populated):
        hi, mi, li = cell
        best = best_file_for_cell(populated[cell])
        src = _resolve_source(best["_label"])
        if src is None:
            print(f"  WARN: cannot resolve {best['_label']!r} — skipping")
            skipped += 1
            continue

        cell_name = f"{H_LABELS[hi]}__{M_LABELS[mi]}__{L_LABELS[li]}".replace(
            "/", "-").replace("+", "p").replace("<", "lt").replace(" ", "")
        # Include source corpus and original filename for traceability
        corpus_tag = best["_label"].replace("/", "__")
        link_name = f"{cell_name}__{corpus_tag}"
        dest = CURATED_DIR / link_name

        if not args.dry_run:
            os.symlink(src, dest)

        lci = best["_L_ci_rel"]
        lci_str = f"{lci:.3f}" if lci is not None else "--   "
        flag = "⚠ " if lci is not None and lci >= 0.15 else "  "
        label = f"{H_LABELS[hi]}/{M_LABELS[mi]}/{L_LABELS[li]}"
        mb = best["_size"] / 1e6
        action = "DRYRUN" if args.dry_run else "link  "
        print(f"  {flag}{action} {label:40s}  H={best['_H']:.2f}  M={best['_M']:.3f}  "
              f"L={str(best['_L'] or '--'):5s}  {mb:6.1f}MB  lci={lci_str}")
        created += 1

    print(f"\n{'Would create' if args.dry_run else 'Created'} {created} symlinks "
          f"in {CURATED_DIR}")
    if skipped:
        print(f"Skipped {skipped} files (source not found)")


if __name__ == "__main__":
    main()
