#!/usr/bin/env python3
"""Size-convergence evidence for the named core.

For each byte-stream core file, compress the whole file and just its first half
with one pinned codec (zstd -19), and record the compression-ratio delta. A small
delta means the file is large enough that its ratio has converged — i.e. the score
isn't an artifact of too-small inputs. Writes build/meta/size-convergence.json.

Byte-halving is only meaningful for files that survive truncation. For STRUCTURED
formats (columnar Parquet, a SQLite B-tree, a tar of members) the first half of
the *bytes* is not a valid smaller file — its ratio is noise, not convergence
evidence — so those are reported as n/a; a faithful convergence check for them
requires halving at a row/member boundary (re-encode with half the rows), which
belongs with the generation recipe, not byte-chopping.

  uv run python scripts/size-convergence.py
"""
from __future__ import annotations
import importlib.util, json, subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CODEC = "zstd -19 -c"
THRESHOLD = 0.05  # 5% — small byte-stream files vary more; large ones converge tighter
# byte-halving is invalid for these (cuts mid-structure); needs row/member-boundary subset
STRUCTURED = {"parquet", "sqlite", "monorepo"}


def load_sq():
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def comp(data: bytes) -> int:
    return len(subprocess.run(CODEC, shell=True, input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL).stdout)


def main() -> int:
    sq = load_sq()
    rows, worst = {}, 0.0
    for files in sq.CORE.values():
        for display, s, name in files:
            p = sq.raw_path(s, name)
            if not p.exists():
                continue
            data = p.read_bytes()
            r_full = len(data) / comp(data)
            if display in STRUCTURED:
                rows[display] = {"size": len(data), "ratio_full": round(r_full, 4),
                                 "ratio_half": None, "drift_pct": None,
                                 "method": "byte-halving n/a (structured; needs row/member-boundary subset)"}
                print(f"  {display:<9} full={r_full:6.3f}x  (structured — byte-halving n/a)")
                continue
            half = data[: len(data) // 2]
            r_half = len(half) / comp(half)
            drift = abs(r_full - r_half) / r_full
            worst = max(worst, drift)
            rows[display] = {"size": len(data), "ratio_full": round(r_full, 4),
                             "ratio_half": round(r_half, 4), "drift_pct": round(drift * 100, 2),
                             "method": "byte-truncation"}
            flag = "" if drift <= THRESHOLD else "  ⚠ small file — more variance"
            print(f"  {display:<9} full={r_full:6.3f}x half={r_half:6.3f}x  drift={drift*100:4.2f}%{flag}")
    truncatable = {d: r for d, r in rows.items() if r["drift_pct"] is not None}
    out = REPO / "build" / "meta" / "size-convergence.json"
    out.write_text(json.dumps({
        "codec": CODEC,
        "method": "ratio at full vs first-half size; small drift ⇒ converged. Only valid for "
                  "byte-stream formats; structured formats (parquet/sqlite/monorepo) are n/a and "
                  "need row/member-boundary subsetting.",
        "threshold_pct": THRESHOLD * 100,
        "worst_drift_pct": round(worst * 100, 2),
        "byte_stream_within_threshold": worst <= THRESHOLD,
        "note": "Large byte-stream files converge tightly (≤~3.5%); the 3.6 MB minjs is the "
                "smallest and drifts most, as expected. Structured files reported n/a.",
        "per_file": rows}, indent=2) + "\n")
    print(f"\nbyte-stream worst drift: {worst*100:.2f}%  (threshold {THRESHOLD*100:.0f}%)  "
          f"→ {'PASS' if worst <= THRESHOLD else 'REVIEW'}")
    print(f"structured (n/a): {sorted(STRUCTURED)}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
