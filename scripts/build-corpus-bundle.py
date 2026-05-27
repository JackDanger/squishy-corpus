#!/usr/bin/env python3
"""Build the squishy corpus v4 bundle.

Creates build/bundle/ with:
  natural/          ← one symlink per natural file (one per populated natural cell)
  calibrated/       ← symlinks for all 3 seed replicates per calibrated cell
  manifest.csv      ← complete file inventory with cell coordinates + SHA-256
  ground-truth.json ← calibrated file construction metadata
  score.py          ← standalone scoring script (no squishy imports required)

manifest.csv columns:
  bundle, cell, source_type, replicate, filename, size_bytes, sha256,
  H_measured, M_greedy_norm, L_p90, H_target, M_target, R_ref, reference_bytes

Usage:
    uv run scripts/build-corpus-bundle.py
    uv run scripts/build-corpus-bundle.py --out build/bundle --dry-run
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


CURATED_DIR = ROOT / "build" / "raw" / "curated"
CALIBRATED_DIR = ROOT / "build" / "raw" / "calibrated"
MEASUREMENT_CSVS = [
    ROOT / "build" / "bench" / "corpus-measurements.csv",
    ROOT / "build" / "bench" / "candidates-measurements.csv",
    ROOT / "build" / "bench" / "calibrated-measurements.csv",
    ROOT / "build" / "bench" / "natural-measurements.csv",
]
SCORE_PY_SRC = ROOT / "scripts" / "score.py"

MANIFEST_FIELDS = [
    "bundle", "cell", "source_type", "replicate",
    "filename", "size_bytes", "sha256",
    "H_measured", "M_greedy_norm", "L_p90",
    "M_norm_reliable", "R_ref_clamped",
    "H_target", "M_target", "R_ref", "reference_bytes",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_measurements() -> dict[str, dict]:
    """Load measurement CSVs → filename → row dict."""
    lookup: dict[str, dict] = {}
    for csv_path in MEASUREMENT_CSVS:
        if not csv_path.exists():
            continue
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                lookup[row["filename"]] = row
    return lookup


def _load_ground_truth() -> dict[str, dict]:
    """Load ground-truth.json from calibrated dir → filename → entry."""
    gt_path = CALIBRATED_DIR / "ground-truth.json"
    if not gt_path.exists():
        return {}
    entries = json.loads(gt_path.read_text())
    return {e["filename"]: e for e in entries}


def _cell_from_link_name(name: str) -> str:
    """Extract cell label from curated symlink name.

    Symlink name format: H_LABEL__M_LABEL__L_LABEL__corpus__filename
    Returns: "H_LABEL/M_LABEL/L_LABEL"
    """
    parts = name.split("__")
    if len(parts) >= 3:
        h = parts[0].replace("lt", "<").replace("p", "+", 1) if "lt" in parts[0] else parts[0]
        m = parts[1].replace("lt", "<").replace("p", "+", 1) if "lt" in parts[1] else parts[1]
        l = parts[2]
        # Reconstruct the canonical label format
        h = h.replace("Mlt", "M<")  # shouldn't appear here but guard
        m = m.replace("Mlt", "M<").replace("M0p", "M0.").replace("p", "+")
        # The parts already use the correct label strings (with hyphens etc.)
        return f"{parts[0]}/{parts[1]}/{parts[2]}"
    return name


def _sibling_replicates(src_path: Path) -> list[Path]:
    """Given a calibrated file like …-s0.bin, return [s0, s1, s2] paths that exist."""
    name = src_path.name
    stem = name
    ext = ""
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        ext = "." + ext
    # Find the seed suffix: …-s0
    m = re.match(r"^(.*-s)(\d+)$", stem)
    if not m:
        return [src_path]
    base, _ = m.group(1), m.group(2)
    siblings = []
    for i in range(3):
        p = src_path.parent / f"{base}{i}{ext}"
        if p.exists():
            siblings.append(p)
    return siblings or [src_path]


def _replicate_id(filename: str) -> str:
    """Extract replicate id 's0'/'s1'/'s2' from filename, or '' if none."""
    m = re.search(r"-(s\d+)\.", filename)
    return m.group(1) if m else ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", default="build/bundle",
                        help="Output bundle directory (default: build/bundle)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be created without writing")
    args = parser.parse_args()

    out_dir = ROOT / args.out
    nat_dir = out_dir / "natural"
    cal_dir = out_dir / "calibrated"

    if not CURATED_DIR.exists():
        print(f"ERROR: curated directory not found: {CURATED_DIR}", file=sys.stderr)
        sys.exit(1)

    measurements = _load_measurements()
    ground_truth = _load_ground_truth()

    if not args.dry_run:
        nat_dir.mkdir(parents=True, exist_ok=True)
        cal_dir.mkdir(parents=True, exist_ok=True)
        # Remove stale entries
        for d in [nat_dir, cal_dir]:
            for p in d.iterdir():
                if p.is_symlink() or p.is_file():
                    p.unlink()

    manifest_rows: list[dict] = []
    gt_bundle: list[dict] = []

    curated_links = sorted(CURATED_DIR.iterdir())
    print(f"Processing {len(curated_links)} curated cells …")

    for link in curated_links:
        if not link.is_symlink():
            continue
        target = link.resolve()
        is_calibrated = (target.parent.name == "calibrated")
        source_type = "calibrated" if is_calibrated else "natural"

        cell = _cell_from_link_name(link.name)

        if is_calibrated:
            siblings = _sibling_replicates(target)
        else:
            siblings = [target]

        for sibling in siblings:
            fname = sibling.name
            row_m = measurements.get(fname, {})
            gt_entry = ground_truth.get(fname, {}) if is_calibrated else {}

            size = sibling.stat().st_size
            bundle_label = "calibrated" if is_calibrated else "natural"
            replicate = _replicate_id(fname) if is_calibrated else ""

            if args.dry_run:
                sha = "(skipped)"
            else:
                sha = sha256_file(sibling)

            manifest_row: dict = {
                "bundle":         bundle_label,
                "cell":           cell,
                "source_type":    source_type,
                "replicate":      replicate,
                "filename":       fname,
                "size_bytes":     size,
                "sha256":         sha,
                "H_measured":     row_m.get("H_marginal", ""),
                "M_greedy_norm":  row_m.get("M_greedy_norm", ""),
                "L_p90":          row_m.get("L_p90", ""),
                "M_norm_reliable": row_m.get("M_norm_reliable", ""),
                "R_ref_clamped":  gt_entry.get("R_ref_clamped", row_m.get("R_ref_clamped", "")),
                "H_target":       gt_entry.get("H_marginal", ""),
                "M_target":       gt_entry.get("M_fraction", ""),
                "R_ref":          gt_entry.get("R_ref", ""),
                "reference_bytes": gt_entry.get("reference_bytes", ""),
            }
            manifest_rows.append(manifest_row)

            if is_calibrated and fname in ground_truth:
                gt_bundle.append(ground_truth[fname])

            dest_dir = cal_dir if is_calibrated else nat_dir
            dest = dest_dir / fname
            action = "DRYRUN" if args.dry_run else "link  "
            rep_str = f" [{replicate}]" if replicate else ""
            print(f"  {action} {bundle_label}/{fname}{rep_str}")

            if not args.dry_run:
                if not dest.exists():
                    try:
                        os.link(sibling, dest)  # hardlink; avoids symlink-resolution issues on S3 sync
                    except OSError:
                        import shutil
                        shutil.copy2(sibling, dest)

    if not args.dry_run:
        # manifest.csv
        manifest_path = out_dir / "manifest.csv"
        with open(manifest_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(manifest_rows)
        print(f"\nWrote {manifest_path} ({len(manifest_rows)} rows)")

        # ground-truth.json (calibrated files only)
        gt_seen = set()
        gt_deduped = []
        for entry in gt_bundle:
            if entry["filename"] not in gt_seen:
                gt_seen.add(entry["filename"])
                gt_deduped.append(entry)
        gt_path = out_dir / "ground-truth.json"
        gt_path.write_text(json.dumps(gt_deduped, indent=2))
        print(f"Wrote {gt_path} ({len(gt_deduped)} entries)")

        # score.py
        if SCORE_PY_SRC.exists():
            shutil.copy2(SCORE_PY_SRC, out_dir / "score.py")
            print(f"Copied score.py → {out_dir / 'score.py'}")
        else:
            print(f"WARN: score.py not found at {SCORE_PY_SRC}", file=sys.stderr)

    natural_count = sum(1 for r in manifest_rows if r["source_type"] == "natural")
    cal_count = sum(1 for r in manifest_rows if r["source_type"] == "calibrated")
    cells = len({r["cell"] for r in manifest_rows})
    print(f"\nBundle summary:")
    print(f"  {natural_count} natural files (1 per natural cell)")
    print(f"  {cal_count} calibrated files ({cal_count // 3 if cal_count >= 3 else cal_count} cells × 3 replicates)")
    print(f"  {cells} unique cells total")
    if not args.dry_run:
        print(f"  Output: {out_dir}")


if __name__ == "__main__":
    main()
