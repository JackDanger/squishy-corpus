#!/usr/bin/env python3
"""Verification pass-4: independent cross-implementation re-score.

The published board (build/meta/squishy-board-complete.json) is produced by the
pinned CLI codec builds in tools.lock. This recomputes the per-file ratios for the
codecs that have a *different*, independent implementation in the Python standard
library — gzip→zlib, bzip2→bz2, xz→lzma — and confirms they reproduce the published
per-file numbers within tolerance. If an independent implementation agrees, the
published number is a property of the data + algorithm, not an artifact of one CLI
build.

Coverage is deliberately bounded: a file is independently re-scored only when its
bytes are present locally AND it is small enough to recompress in memory (multi-GB
scale rungs would need gigabytes of RAM and minutes per codec). Every file that is
NOT re-scored is recorded explicitly with a reason — coverage is reported, never
silently truncated.

  uv run python scripts/verify-pass4.py     # -> build/meta/verification-pass4.json
"""
from __future__ import annotations
import bz2, gzip, importlib.util, json, lzma
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOL = 0.02                       # 2% per-file ratio tolerance (CLI vs stdlib headers/edge tuning)
SIZE_CAP = 64 * 1024 * 1024      # only recompress files up to 64 MiB in memory

# published board codec label → stdlib compressor at the matching level
STDLIB = {
    "gzip -9":  lambda d: gzip.compress(d, 9, mtime=0),
    "bzip2 -9": lambda d: bz2.compress(d, 9),
    "xz -9":    lambda d: lzma.compress(d, preset=9),
}


def main() -> int:
    board = json.loads((REPO / "build/meta/squishy-board-complete.json").read_text())["codecs"]
    ed = json.loads((REPO / "build/meta/edition.json").read_text())
    by_name = {f["name"]: f for f in ed["files"]}   # per_file keys are edition names
    raw = REPO / "build" / "raw"

    report, worst_overall, all_ok = {}, 0.0, True
    for codec, compress in STDLIB.items():
        if codec not in board:
            continue
        want = board[codec]["per_file"]
        per, skipped, worst = {}, {}, 0.0
        for name, cli_ratio in want.items():
            f = by_name.get(name)
            if not f:
                skipped[name] = "not in edition manifest"
                continue
            p = raw / f["key"]
            if not p.exists():
                skipped[name] = "bytes absent locally"
                continue
            if (f.get("size_bytes") or p.stat().st_size) > SIZE_CAP:
                skipped[name] = f"larger than {SIZE_CAP // (1024*1024)} MiB cap"
                continue
            data = p.read_bytes()
            r = len(data) / len(compress(data))
            d = abs(r - cli_ratio) / cli_ratio
            worst = max(worst, d)
            per[name] = {"stdlib_ratio": round(r, 4), "cli_ratio": cli_ratio,
                         "delta_pct": round(d * 100, 2)}
        ok = (worst <= TOL) and bool(per)   # must have verified at least one file to "agree"
        all_ok &= ok
        worst_overall = max(worst_overall, worst)
        report[codec] = {
            "cli_squishy": board[codec]["squishy_score"],
            "verified_files": len(per), "skipped_files": len(skipped),
            "worst_file_delta_pct": round(worst * 100, 2),
            "agrees": ok, "per_file": per, "skipped": skipped,
        }
        print(f"  {codec:<10} verified {len(per):>2}/{len(want)}  "
              f"worst Δ={worst*100:.2f}%  ({len(skipped)} skipped)  {'✓' if ok else '⚠'}")

    out = REPO / "build" / "meta" / "verification-pass4.json"
    out.write_text(json.dumps({
        "method": ("independent stdlib re-score (zlib/bz2/lzma) of the locally-present, "
                   "≤64 MiB scored cells vs the published pinned-CLI board "
                   "(squishy-board-complete.json); larger/absent files are listed under "
                   "each codec's 'skipped' with a reason."),
        "board": "squishy-board-complete.json",
        "tolerance_pct": TOL * 100, "size_cap_bytes": SIZE_CAP,
        "worst_delta_pct": round(worst_overall * 100, 2),
        "all_agree": all_ok, "codecs": report}, indent=2) + "\n")
    print(f"\nworst Δ across all verified: {worst_overall*100:.2f}% (tol {TOL*100:.0f}%) → "
          f"{'PASS — independent impl agrees' if all_ok else 'REVIEW'}")
    print(f"wrote {out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
