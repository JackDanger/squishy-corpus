#!/usr/bin/env python3
"""Representativeness + PII review sheet for the named core (#17).

Prints, for each core file: name, kind, license, source, size, and a short
content preview — so a human can eyeball "is this real data of its kind, and is
there anything that shouldn't be public?" in a few minutes.

  uv run python scripts/review-sheet.py
"""
from __future__ import annotations
import csv, importlib.util, subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load():
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    man = {r["core_slot"]: r for r in csv.DictReader((REPO / "build/meta/LICENSE-MANIFEST.csv").open())}
    return sq, man


def preview(p: Path) -> str:
    b = p.read_bytes()[:4096]
    ftype = subprocess.run(["file", "-b", str(p)], capture_output=True, text=True).stdout.strip()
    # text-ish → first 3 non-empty lines; binary → file(1) + tar listing
    printable = sum(32 <= c < 127 or c in (9, 10, 13) for c in b)
    if printable > len(b) * 0.85:
        lines = [l for l in b.decode("utf-8", "replace").splitlines() if l.strip()][:3]
        body = "\n      ".join(l[:100] for l in lines)
        return f"[{ftype}]\n      {body}"
    if p.suffix == ".tar" or b[:2] == b"\x1f\x8b" or p.name.endswith(".xml") and b[257:262] == b"ustar":
        names = subprocess.run(["tar", "-tf", str(p)], capture_output=True, text=True).stdout.split()[:4]
        return f"[{ftype}] members: {names} ..."
    return f"[{ftype}]"


def main() -> int:
    sq, man = load()
    print("SQUISHY-2026 CORE — representativeness + PII review sheet\n" + "=" * 64)
    n = 0
    for cat, files in sq.CORE.items():
        print(f"\n## {cat}")
        for display, st, name in files:
            n += 1
            p = REPO / "build" / "raw" / st / name
            m = man.get(display, {})
            sz = p.stat().st_size if p.exists() else 0
            print(f"\n[{n:>2}] {display}  ({sz/1e6:.1f} MB)  license={m.get('license','?')}")
            print(f"     source: {m.get('source_url','?')[:90]}")
            print(f"     {preview(p) if p.exists() else 'MISSING'}")
            print(f"     → representative of '{display}'? real data, nothing private?  [ ] OK")
    print(f"\n{'=' * 64}\n{n} files. Tick each box; flag anything that looks synthetic, "
          "wrong-kind, or contains data that shouldn't be public.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
