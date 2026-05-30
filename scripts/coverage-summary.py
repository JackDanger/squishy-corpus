#!/usr/bin/env python3
"""Coverage check: does the corpus span the intrinsic byte-property volume?

Loads every file's measured properties (core file-properties.json + large
scale-properties.json), then reports the span of each axis (entropy, coverage,
match_distance, size) and the full point cloud, so a human can see at a glance
that the set is sparse-but-representative rather than bunched in one corner.

  uv run python scripts/coverage-summary.py
"""
from __future__ import annotations
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load() -> list[dict]:
    pts = []
    core = json.loads((REPO / "build/meta/file-properties.json").read_text())["files"]
    for name, m in core.items():
        pts.append({"name": name, "tier": "core", **m})
    sp = REPO / "build/meta/scale-properties.json"
    if sp.exists():
        for name, m in json.loads(sp.read_text())["files"].items():
            pts.append({"name": name, "tier": "scale", **m})
    return pts


def main() -> int:
    pts = load()
    print(f"{len(pts)} files ({sum(p['tier']=='core' for p in pts)} core + "
          f"{sum(p['tier']=='scale' for p in pts)} scale)\n")
    axes = [("entropy", "bits/byte", 1e0, ".2f"),
            ("coverage", "repeat-fraction", 1e0, ".3f"),
            ("match_distance", "median back-distance (B)", 1e0, ",.0f"),
            ("match_distance_p90", "p90 back-distance (B)", 1e0, ",.0f"),
            ("size", "bytes", 1e0, ",.0f")]
    print("AXIS SPANS (min → max):")
    for key, unit, _, fmt in axes:
        vals = [(p[key], p["name"]) for p in pts if key in p]
        lo = min(vals); hi = max(vals)
        print(f"  {key:20} {format(lo[0], fmt):>16} ({lo[1][:24]:24})  →  "
              f"{format(hi[0], fmt):>16} ({hi[1][:24]})   [{unit}]")

    print("\nPOINT CLOUD (sorted by entropy):")
    print(f"  {'file':34} {'tier':5} {'MB':>8} {'H':>5} {'cover':>6} {'md(med)':>12} {'md(p90)':>14}")
    for p in sorted(pts, key=lambda x: x["entropy"]):
        print(f"  {p['name'][:34]:34} {p['tier']:5} {p['size']/1e6:8.1f} "
              f"{p['entropy']:5.2f} {p['coverage']:6.3f} {p['match_distance']:>12,} {p['match_distance_p90']:>14,}")

    # crude bucket occupancy across the 3 shape axes (not size) — just to eyeball gaps
    def bucket(p):
        H = "Hlo" if p["entropy"] < 5 else ("Hmid" if p["entropy"] < 6.5 else "Hhi")
        C = "Clo" if p["coverage"] < 0.15 else ("Cmid" if p["coverage"] < 0.45 else "Chi")
        D = "Dloc" if p["match_distance_p90"] < 2_000_000 else "Dlong"
        return f"{H}/{C}/{D}"
    from collections import Counter
    occ = Counter(bucket(p) for p in pts)
    print("\nSHAPE-CELL OCCUPANCY (entropy × coverage × p90-distance; eyeball for empties/crowding):")
    for cell, n in sorted(occ.items()):
        print(f"  {cell:18} {'█'*n} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
