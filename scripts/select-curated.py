#!/usr/bin/env python3
"""Populate build/raw/curated/ with symlinks for the calibrated corpus bundle.

Policy:
  - Calibrated files: one symlink per cell (best replicate; siblings s0/s1/s2
    are discovered later by build-corpus-bundle.py).
  - Natural files: ALL qualifying files per cell. Multiple natural datasets
    in the same cell are welcome — they validate the synthetic generator and
    expose blind spots.

"Qualifying" natural file: L_ci_rel < 0.15 (reliable L estimate). Falls back
to all natural files if none qualify.

The curated directory is rebuilt from scratch each run (stale symlinks removed).

Usage:
    uv run scripts/select-curated.py
    uv run scripts/select-curated.py --csvs build/bench/corpus-measurements.csv \
        build/bench/candidates-measurements.csv build/bench/calibrated-measurements.csv \
        build/bench/natural-measurements.csv
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


def _print_link(f: dict, hi: int, mi: int, li: int, dry_run: bool) -> None:
    lci = f["_L_ci_rel"]
    lci_str = f"{lci:.3f}" if lci is not None else "--   "
    flag = "⚠ " if lci is not None and lci >= 0.15 else "  "
    label = f"{H_LABELS[hi]}/{M_LABELS[mi]}/{L_LABELS[li]}"
    mb = f["_size"] / 1e6
    action = "DRYRUN" if dry_run else "link  "
    src_tag = "cal" if f.get("generator") else "nat"
    print(f"  {flag}{action} [{src_tag}] {label:40s}  H={f['_H']:.2f}  M={f['_M']:.3f}  "
          f"L={str(f['_L'] or '--'):5s}  {mb:6.1f}MB  lci={lci_str}")


def _qualifying_natural(files: list[dict]) -> list[dict]:
    """All natural (non-calibrated) files meeting the L_ci_rel quality bar."""
    natural = [f for f in files if not f.get("generator")]
    qualified = [f for f in natural if f["_L_ci_rel"] is not None and f["_L_ci_rel"] < 0.15]
    return qualified if qualified else natural


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
                                 "build/bench/calibrated-measurements.csv",
                                 "build/bench/natural-measurements.csv"],
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
        all_files = populated[cell]
        cell_name = f"{H_LABELS[hi]}__{M_LABELS[mi]}__{L_LABELS[li]}".replace(
            "/", "-").replace("+", "p").replace("<", "lt").replace(" ", "")

        # Calibrated: one representative (siblings discovered downstream)
        calibrated = [f for f in all_files if f.get("generator")]
        if calibrated:
            best_cal = best_file_for_cell(calibrated)
            src = _resolve_source(best_cal["_label"])
            if src is None:
                print(f"  WARN: cannot resolve {best_cal['_label']!r} — skipping")
                skipped += 1
            else:
                corpus_tag = best_cal["_label"].replace("/", "__")
                dest = CURATED_DIR / f"{cell_name}__{corpus_tag}"
                if not args.dry_run:
                    os.symlink(src, dest)
                _print_link(best_cal, hi, mi, li, args.dry_run)
                created += 1

        # Natural: ALL qualifying files per cell
        for f in _qualifying_natural(all_files):
            src = _resolve_source(f["_label"])
            if src is None:
                print(f"  WARN: cannot resolve {f['_label']!r} — skipping")
                skipped += 1
                continue
            corpus_tag = f["_label"].replace("/", "__")
            dest = CURATED_DIR / f"{cell_name}__{corpus_tag}"
            if not args.dry_run:
                os.symlink(src, dest)
            _print_link(f, hi, mi, li, args.dry_run)
            created += 1

    print(f"\n{'Would create' if args.dry_run else 'Created'} {created} symlinks "
          f"in {CURATED_DIR}")
    if skipped:
        print(f"Skipped {skipped} files (source not found)")


if __name__ == "__main__":
    main()
