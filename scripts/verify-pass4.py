#!/usr/bin/env python3
"""Verification pass-4: independent cross-implementation re-score.

The reference board is produced by the pinned CLI codec builds in tools.lock.
This recomputes the per-file ratios for the codecs that have a *different*,
independent implementation in the Python standard library — gzip→zlib,
bzip2→bz2, xz→lzma — and confirms they reproduce the published board within
tolerance. If an independent implementation agrees, the published number is a
property of the data + algorithm, not an artifact of one CLI build.

  uv run python scripts/verify-pass4.py     # -> build/meta/verification-pass4.json
"""
from __future__ import annotations
import bz2, gzip, importlib.util, json, lzma
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOL = 0.02  # 2% per-file ratio tolerance (CLI vs stdlib differ in headers/edge tuning)

# published board codec → stdlib compressor at the matching level
STDLIB = {
    "gzip -9":  lambda d: gzip.compress(d, 9, mtime=0),
    "bzip2 -9": lambda d: bz2.compress(d, 9),
    "xz -9":    lambda d: lzma.compress(d, preset=9),
}


def load_sq():
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def main() -> int:
    sq = load_sq()
    board = json.loads((REPO / "build/meta/squishy-scores.json").read_text())["panel"]
    report, worst_overall, all_ok = {}, 0.0, True
    for codec, compress in STDLIB.items():
        if codec not in board:
            continue
        want = board[codec]["per_file"]
        per, worst = {}, 0.0
        ratios = []
        for files in sq.CORE.values():
            for display, s, name in files:
                p = sq.raw_path(s, name)
                if not p.exists():
                    continue
                data = p.read_bytes()
                r = len(data) / len(compress(data))
                ratios.append(r)
                d = abs(r - want[display]) / want[display]
                worst = max(worst, d)
                per[display] = {"stdlib_ratio": round(r, 4), "cli_ratio": want[display],
                                "delta_pct": round(d * 100, 2)}
        score = sq.geomean(ratios)
        ok = worst <= TOL
        all_ok &= ok
        worst_overall = max(worst_overall, worst)
        report[codec] = {"stdlib_squishy": round(score, 3), "cli_squishy": board[codec]["squishy_score"],
                         "worst_file_delta_pct": round(worst * 100, 2), "agrees": ok, "per_file": per}
        print(f"  {codec:<10} stdlib={score:.3f}x  cli={board[codec]['squishy_score']:.3f}x  "
              f"worst Δ={worst*100:.2f}%  {'✓' if ok else '⚠'}")
    out = REPO / "build" / "meta" / "verification-pass4.json"
    out.write_text(json.dumps({
        "method": "independent stdlib re-score (zlib/bz2/lzma) vs the pinned-CLI board",
        "tolerance_pct": TOL * 100, "worst_delta_pct": round(worst_overall * 100, 2),
        "all_agree": all_ok, "codecs": report}, indent=2) + "\n")
    print(f"\nworst Δ across all: {worst_overall*100:.2f}% (tol {TOL*100:.0f}%) → "
          f"{'PASS — independent impl agrees' if all_ok else 'REVIEW'}")
    print(f"wrote {out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
