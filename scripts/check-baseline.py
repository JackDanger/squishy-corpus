#!/usr/bin/env python3
"""Diff the current state against build/meta/baseline.json — the equality check that
turns "end-to-end" into real verification. Confirms:
  • the scored-set fingerprint (names/shas/kinds/categories/scored) is unchanged,
  • every locally-present corpus file still hashes to its pinned sha256,
  • the reference codec's complete-edition Squishy Score matches, and that its run
    round-trip-verified every file (lossless).
Exit non-zero on any mismatch.

  uv run python scripts/check-baseline.py [--reference build/meta/squishy-score-complete.json]
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", type=Path, default=REPO / "build/meta/squishy-score-complete.json")
    a = ap.parse_args()
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    base = json.loads((REPO / "build/meta/baseline.json").read_text())
    fails: list[str] = []

    # 1. scored-set fingerprint unchanged
    ed = json.loads((REPO / "build/meta/edition.json").read_text())
    fp = [(f["name"], f["sha256"], f["kind"], f["category"], f.get("scored"))
          for f in sorted(ed["files"], key=lambda x: x["name"])]
    cur = hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()
    if cur != base["scored_set_fingerprint"]:
        fails.append(f"scored-set fingerprint changed\n  baseline {base['scored_set_fingerprint']}\n  current  {cur}")
    else:
        print("✓ scored-set fingerprint matches baseline")

    # 2. local core files still hash to their pinned sha
    checked = 0
    for cat, members in sq.CORE.items():
        for display, st, name in members:
            p = sq.raw_path(st, name)
            want = base["files_sha256"].get(name)
            if p.exists() and want:
                got = sha256(p)
                if got != want:
                    fails.append(f"{name}: sha {got[:12]} != baseline {want[:12]}")
                else:
                    checked += 1
    print(f"✓ {checked} local core files hash to their pinned sha256")

    # 3. reference complete-edition score matches + was round-trip verified
    if a.reference.exists():
        d = json.loads(a.reference.read_text())
        rb = base["reference_score"]
        if d.get("complete") is not True:
            fails.append("reference run is not complete")
        if d.get("squishy_score") != rb.get("squishy_score"):
            fails.append(f"reference score {d.get('squishy_score')} != baseline {rb.get('squishy_score')}")
        else:
            print(f"✓ reference {rb.get('codec')} = {rb.get('squishy_score')}× matches baseline")
        if not d.get("round_trip_verified"):
            fails.append("reference run did NOT round-trip-verify every file (re-run with --verify)")
        else:
            print("✓ reference run round-trip-verified every file (lossless)")
    else:
        fails.append(f"no reference result at {a.reference}")

    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("\nPASS — current state matches the golden baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
