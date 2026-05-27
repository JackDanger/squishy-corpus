#!/usr/bin/env python3
"""
score.py — Squishy corpus v4 reference scoring script.

ONBOARDING QUICK START
======================
This script is a standalone scoring tool. Copy it anywhere; it has no
dependencies outside the Python standard library.

Step 1 — List the files you need to compress:
    python score.py list --manifest manifest.csv --bundle calibrated
    python score.py list --manifest manifest.csv --bundle natural
    python score.py list --manifest manifest.csv --bundle all

Step 2 — Compress each file. Put the compressed output in a results directory
    where each file is named identically to the original (any extension appended
    is stripped by this script). Example with zstd:

        mkdir -p results/zstd-3
        while read fname; do
            zstd -3 calibrated/"$fname" -c -q > results/zstd-3/"$fname"
        done < <(python score.py list --manifest manifest.csv --bundle calibrated)

Step 3 — Score:
    python score.py score --manifest manifest.csv \\
        --results results/zstd-3 --codec zstd-3

ABOUT rate_ratio
================
rate_ratio = compressed_bytes / reference_bytes

*** rate_ratio < 1.0 IS EXPECTED AND NORMAL ***

R_ref is the cost of the *construction parse* — the LZ parse that was used to
build the calibrated synthetic file. R_ref is NOT a Shannon entropy lower bound
and NOT an information-theoretic limit. Real codecs routinely achieve
rate_ratio < 1.0 by finding better parses than the one used during construction.
A sub-unit ratio means "your codec found more redundancy than the generator
used to create the file." It is a feature, not a bug.

RANK STABILITY (Kendall-τ)
==========================
The `compare` command reports two stability metrics:

1. Per-cell winner stability: for cells with 3 replicates (s0, s1, s2), does
   the same codec win on every replicate? Stable = yes; unstable = winner
   flips across replicates (suggests the cell is too small or noisy).

2. Corpus-level Kendall-τ (headline metric): rank all calibrated cells by
   codec A's mean bpb, rank them by codec B's mean bpb, compute τ between
   the two rankings.  τ ≈ 1.0 means both codecs agree on which cells are
   harder — the corpus reliably ranks cell difficulty regardless of codec.
   A well-designed corpus should achieve τ ≥ 0.9.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path


def _read_manifest(manifest_path: Path) -> list[dict]:
    with open(manifest_path, newline="") as f:
        return list(csv.DictReader(f))


def _float(v) -> float | None:
    try:
        return float(v) if v not in ("", None) else None
    except (TypeError, ValueError):
        return None


def cmd_list(args) -> None:
    """Print filenames for the requested bundle (one per line)."""
    rows = _read_manifest(Path(args.manifest))
    bundle = args.bundle.lower()
    for row in rows:
        if bundle == "all" or row["bundle"] == bundle:
            print(row["filename"])


def kendall_tau(a: list[float], b: list[float]) -> float:
    """Kendall-τ rank correlation. Returns NaN if fewer than 2 items."""
    n = len(a)
    if n < 2:
        return float("nan")
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            da = (a[i] > a[j]) - (a[i] < a[j])
            db = (b[i] > b[j]) - (b[i] < b[j])
            if da != 0 and db != 0:
                if da == db:
                    concordant += 1
                else:
                    discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total > 0 else float("nan")


def _find_compressed(results_dir: Path, filename: str) -> int | None:
    """Look for a compressed version of `filename` in results_dir.

    Accepts: exact match, or {filename}.{any_ext}, or {filename} with the
    last extension stripped. Returns compressed size in bytes, or None.
    """
    # Exact match
    p = results_dir / filename
    if p.exists():
        return p.stat().st_size
    # With any single extra extension appended
    stem = Path(filename).stem
    for candidate in results_dir.iterdir():
        if candidate.is_file() and (candidate.name == filename or
                                    candidate.stem == filename or
                                    candidate.name.startswith(filename + ".")):
            return candidate.stat().st_size
    return None


def cmd_score(args) -> None:
    """Score a results directory against the manifest."""
    manifest_path = Path(args.manifest)
    results_dir = Path(args.results)
    codec_name = args.codec

    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)
    if not results_dir.exists():
        print(f"ERROR: results directory not found: {results_dir}", file=sys.stderr)
        sys.exit(1)

    rows = _read_manifest(manifest_path)
    bundle = args.bundle.lower() if args.bundle else "all"

    # Collect per-cell data: cell → {replicate: compressed_bytes}
    # and cell metadata
    cell_meta: dict[str, dict] = {}       # cell → row metadata (first seen)
    cell_replicates: dict[str, dict] = {} # cell → {replicate: compressed_bytes}
    natural_rows: list[dict] = []
    missing = 0

    for row in rows:
        if bundle != "all" and row["bundle"] != bundle:
            continue
        fname = row["filename"]
        compressed = _find_compressed(results_dir, fname)
        if compressed is None:
            missing += 1
            continue

        cell = row["cell"]
        rep = row.get("replicate") or "–"
        size = int(row["size_bytes"])
        ref_bytes = _float(row.get("reference_bytes"))
        r_ref = _float(row.get("R_ref"))
        h_measured = _float(row.get("H_measured"))
        m_norm = _float(row.get("M_greedy_norm"))
        l_p90 = _float(row.get("L_p90"))
        source = row["source_type"]
        r_ref_clamped = row.get("R_ref_clamped", "").lower() in ("true", "1")
        m_norm_reliable = row.get("M_norm_reliable", "True").lower() not in ("false", "0")

        bpb = compressed * 8.0 / size if size > 0 else None
        rate_ratio = compressed / ref_bytes if ref_bytes and ref_bytes > 0 else None

        if cell not in cell_meta:
            cell_meta[cell] = {
                "cell": cell, "source_type": source,
                "H_measured": h_measured, "M_greedy_norm": m_norm,
                "L_p90": l_p90, "R_ref": r_ref,
                "R_ref_clamped": r_ref_clamped,
                "M_norm_reliable": m_norm_reliable,
            }
            cell_replicates[cell] = {}

        cell_replicates[cell][rep] = {
            "compressed": compressed, "size": size,
            "bpb": bpb, "rate_ratio": rate_ratio,
        }

        if source == "natural":
            natural_rows.append({
                "cell": cell, "filename": fname,
                "bpb": bpb, "rate_ratio": rate_ratio,
            })

    if missing:
        print(f"WARNING: {missing} manifest files not found in {results_dir}",
              file=sys.stderr)

    # ── Per-cell summary ────────────────────────────────────────────────────────

    below_r_ref_count = 0
    total_r_ref_count = 0

    print(f"\n── {codec_name} — per-cell results ({'calibrated' if bundle == 'calibrated' else bundle}) ──")
    print(f"  {'Cell':45s}  {'bpb':6s}  {'rate_ratio':10s}  {'n':2s}  note")
    print("  " + "-" * 80)

    tau_values: list[float] = []

    for cell in sorted(cell_meta.keys()):
        meta = cell_meta[cell]
        reps = cell_replicates[cell]
        if not reps:
            continue

        bpbs = [r["bpb"] for r in reps.values() if r["bpb"] is not None]
        ratios = [r["rate_ratio"] for r in reps.values() if r["rate_ratio"] is not None]
        n = len(bpbs)

        mean_bpb = sum(bpbs) / n if bpbs else None
        mean_ratio = sum(ratios) / n if ratios else None

        notes = []

        if meta.get("R_ref_clamped"):
            notes.append("R_ref=H_marginal (copying not profitable at H<1.86)")

        if not meta.get("M_norm_reliable", True):
            notes.append("M-axis uses M_target (H<4, low M_greedy_norm range)")

        if mean_ratio is not None:
            total_r_ref_count += 1
            if mean_ratio < 1.0:
                below_r_ref_count += 1
                notes.append("< R_ref (expected)")

        if meta["source_type"] == "calibrated" and len(reps) == 1:
            notes.append("only s0")

        note_str = "; ".join(notes)
        bpb_str = f"{mean_bpb:.4f}" if mean_bpb is not None else "  N/A"
        ratio_str = f"{mean_ratio:.5f}" if mean_ratio is not None else "       N/A"
        print(f"  {cell:45s}  {bpb_str:6s}  {ratio_str:10s}  {n:2d}  {note_str}")

    # ── Kendall-τ rank stability (multi-codec comparison needed) ────────────────

    print(f"\n── R_ref summary ──")
    if total_r_ref_count > 0:
        pct = 100 * below_r_ref_count / total_r_ref_count
        print(f"  {below_r_ref_count} / {total_r_ref_count} calibrated cells have "
              f"mean rate_ratio < 1.0 ({pct:.0f}%)")
        print(f"  rate_ratio < 1.0 means {codec_name} found a better parse than R_ref.")
        print(f"  R_ref is the construction-parse cost, NOT a lower bound.")
    else:
        print("  No calibrated cells scored (check --bundle flag).")

    print(f"\n── Rank stability note ──")
    print(f"  Kendall-τ rank stability requires results from ≥2 codecs.")
    print(f"  Run: python score.py compare --manifest manifest.csv \\")
    print(f"       --results-a results/codec-a --results-b results/codec-b")


def cmd_compare(args) -> None:
    """Compare two codecs and report Kendall-τ rank stability across replicates."""
    manifest_path = Path(args.manifest)
    dir_a = Path(args.results_a)
    dir_b = Path(args.results_b)
    bundle = args.bundle.lower() if args.bundle else "calibrated"

    rows = _read_manifest(manifest_path)

    # For each calibrated cell: collect bpb for each replicate for each codec
    # cell → replicate → {a: bpb, b: bpb}
    cell_reps: dict[str, dict[str, dict[str, float | None]]] = {}
    cell_source: dict[str, str] = {}

    cell_meta_cmp: dict[str, dict] = {}  # cell → flags from manifest

    for row in rows:
        if row["source_type"] != "calibrated":
            continue
        if bundle != "all" and row["bundle"] != bundle:
            continue
        cell = row["cell"]
        rep = row.get("replicate") or "s0"
        fname = row["filename"]
        size = int(row["size_bytes"])

        ca = _find_compressed(dir_a, fname)
        cb = _find_compressed(dir_b, fname)
        bpb_a = ca * 8.0 / size if ca and size > 0 else None
        bpb_b = cb * 8.0 / size if cb and size > 0 else None

        if cell not in cell_reps:
            cell_reps[cell] = {}
            cell_source[cell] = row["source_type"]
            cell_meta_cmp[cell] = {
                "R_ref_clamped": row.get("R_ref_clamped", "").lower() in ("true", "1"),
                "M_norm_reliable": row.get("M_norm_reliable", "True").lower() not in ("false", "0"),
            }
        cell_reps[cell][rep] = {"a": bpb_a, "b": bpb_b}

    if not cell_reps:
        print("No calibrated cells found. Use --bundle calibrated or --bundle all.",
              file=sys.stderr)
        sys.exit(1)

    codec_a = args.codec_a or dir_a.name
    codec_b = args.codec_b or dir_b.name

    print(f"\n── Rank stability: {codec_a} vs {codec_b} ──")
    print(f"  {'Cell':45s}  {'A bpb':6s}  {'B bpb':6s}  {'winner':8s}  {'τ note'}")
    print("  " + "-" * 80)

    wins_a = wins_b = ties = 0
    # For corpus-level τ: only unclamped cells (R_ref_clamped=False) are included.
    # Clamped cells (H<1.86) have R_ref=H_marginal regardless of M, so all M-axis
    # variation collapses; mixing them into τ measures something different.
    cell_order: list[tuple[str, float, float]] = []  # (cell, mean_a, mean_b)
    cell_order_clamped: list[tuple[str, float, float]] = []  # clamped cells, separate
    # For per-cell winner stability: does the same codec win on every replicate?
    stable_cells = unstable_cells = 0

    for cell in sorted(cell_reps.keys()):
        reps = cell_reps[cell]
        rep_keys = sorted(reps.keys())
        flags = cell_meta_cmp.get(cell, {})
        is_clamped = flags.get("R_ref_clamped", False)
        m_reliable = flags.get("M_norm_reliable", True)

        bpbs_a = [reps[k]["a"] for k in rep_keys if reps[k]["a"] is not None]
        bpbs_b = [reps[k]["b"] for k in rep_keys if reps[k]["b"] is not None]

        if not bpbs_a or not bpbs_b:
            continue

        mean_a = sum(bpbs_a) / len(bpbs_a)
        mean_b = sum(bpbs_b) / len(bpbs_b)
        if is_clamped:
            cell_order_clamped.append((cell, mean_a, mean_b))
        else:
            cell_order.append((cell, mean_a, mean_b))

        winner = codec_a if mean_a < mean_b else (codec_b if mean_b < mean_a else "tie")
        if winner == codec_a:
            wins_a += 1
        elif winner == codec_b:
            wins_b += 1
        else:
            ties += 1

        # Per-cell winner stability: does the same codec win on every replicate?
        stability_note = ""
        if len(bpbs_a) >= 2 and len(bpbs_b) >= 2:
            n_reps = min(len(bpbs_a), len(bpbs_b))
            winner_per_rep = [
                "A" if bpbs_a[i] < bpbs_b[i] else ("B" if bpbs_b[i] < bpbs_a[i] else "tie")
                for i in range(n_reps)
            ]
            distinct = set(winner_per_rep) - {"tie"}
            if len(distinct) <= 1:
                stable_cells += 1
                stability_note = "stable"
            else:
                unstable_cells += 1
                stability_note = "unstable"

        clamped_note = " [R_ref clamped]" if is_clamped else ""
        m_note = " [M=target]" if not m_reliable else ""
        print(f"  {cell:45s}  {mean_a:.4f}  {mean_b:.4f}  {winner:8s}  "
              f"{stability_note}{clamped_note}{m_note}")

    total = wins_a + wins_b + ties
    print(f"\n  {codec_a} wins: {wins_a}/{total}   "
          f"{codec_b} wins: {wins_b}/{total}   ties: {ties}/{total}")

    if stable_cells + unstable_cells > 0:
        pct = 100 * stable_cells / (stable_cells + unstable_cells)
        print(f"\n── Per-cell winner stability ──")
        print(f"  {stable_cells}/{stable_cells + unstable_cells} cells ({pct:.0f}%) "
              f"have a consistent winner across all {min(3, max(len(cell_reps[c]) for c in cell_reps))} replicates")
        print(f"  Unstable cells have different winners for different replicates — "
              f"consider larger file sizes.")

    # Corpus-level Kendall-τ: do both codecs agree on cell difficulty ordering?
    # Computed separately for unclamped and clamped cells.
    if len(cell_order) >= 2:
        mean_as = [t[1] for t in cell_order]
        mean_bs = [t[2] for t in cell_order]
        corpus_tau = kendall_tau(mean_as, mean_bs)
        print(f"\n── Corpus-level Kendall-τ (headline metric) ──")
        print(f"  τ = {corpus_tau:.3f}  (across {len(cell_order)} unclamped calibrated cells)")
        if cell_order_clamped:
            mean_as_c = [t[1] for t in cell_order_clamped]
            mean_bs_c = [t[2] for t in cell_order_clamped]
            tau_c = kendall_tau(mean_as_c, mean_bs_c)
            tau_c_str = f"{tau_c:.3f}" if tau_c == tau_c else "N/A"
            print(f"  τ = {tau_c_str}  (across {len(cell_order_clamped)} R_ref-clamped cells, H<1.86)")
            print(f"  Note: clamped cells excluded from headline τ (R_ref=H_marginal "
                  f"there; M-axis variation collapses).")
        print(f"  Interpretation: do {codec_a} and {codec_b} agree on which cells")
        print(f"    are harder to compress?")
        print(f"  τ ≥ 0.9 = strong agreement — corpus reliably ranks cell difficulty")
        print(f"  τ < 0.7 = weak agreement — cells may be too small or too few codecs")
    else:
        print(f"\n  Note: corpus-level τ requires ≥2 cells.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # list
    p_list = sub.add_parser("list", help="Print filenames for a bundle")
    p_list.add_argument("--manifest", required=True, help="Path to manifest.csv")
    p_list.add_argument("--bundle", default="all",
                        choices=["all", "natural", "calibrated"],
                        help="Which bundle to list (default: all)")

    # score
    p_score = sub.add_parser("score", help="Score a single codec's compressed output")
    p_score.add_argument("--manifest", required=True, help="Path to manifest.csv")
    p_score.add_argument("--results", required=True,
                         help="Directory containing compressed files")
    p_score.add_argument("--codec", required=True, help="Codec name (for display)")
    p_score.add_argument("--bundle", default="all",
                         choices=["all", "natural", "calibrated"],
                         help="Subset of manifest to score (default: all)")

    # compare
    p_cmp = sub.add_parser("compare", help="Compare two codecs with Kendall-τ stability")
    p_cmp.add_argument("--manifest", required=True, help="Path to manifest.csv")
    p_cmp.add_argument("--results-a", required=True,
                       help="Results directory for codec A")
    p_cmp.add_argument("--results-b", required=True,
                       help="Results directory for codec B")
    p_cmp.add_argument("--codec-a", default=None, help="Name for codec A (display)")
    p_cmp.add_argument("--codec-b", default=None, help="Name for codec B (display)")
    p_cmp.add_argument("--bundle", default="calibrated",
                       choices=["all", "natural", "calibrated"])

    args = parser.parse_args()

    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "score":
        cmd_score(args)
    elif args.cmd == "compare":
        cmd_compare(args)


if __name__ == "__main__":
    main()
