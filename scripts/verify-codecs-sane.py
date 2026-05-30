#!/usr/bin/env python3
"""Certify each panel codec binary is lossless ON THIS HOST — cheaply. Lossless
codecs are lossless by construction, so the real question is only "is this installed
binary/arch sane?", which one small round-trip per codec answers. (Re-proving
losslessness on 12 GB × N codecs is theatre; the reference codec gets one full-edition
round-trip in squishy-calculate --verify, this covers the rest.)

  uv run python scripts/verify-codecs-sane.py
"""
from __future__ import annotations
import importlib.util, os, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAIRS = [("gzip -9 -c", "gzip -dc"), ("bzip2 -9 -c", "bzip2 -dc"),
         ("zstd -19 -c", "zstd -dc"), ("zstd --ultra -22 -c", "zstd -dc"),
         ("xz -9 -c", "xz -dc"), ("brotli -q 11 -c", "brotli -dc")]


def main() -> int:
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    # a small but non-trivial real sample: the dickens core file (prose, ~12 MB)
    sample = sq.raw_path("corpus", "dickens")
    if not sample.exists():
        print("  (no local sample; skipping codec sanity)"); return 0
    fails = []
    for comp, dec in PAIRS:
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "c"); o = os.path.join(d, "o")
            with open(sample, "rb") as fi, open(c, "wb") as fo:
                subprocess.run(comp, shell=True, stdin=fi, stdout=fo, stderr=subprocess.DEVNULL)
            with open(c, "rb") as fi, open(o, "wb") as fo:
                subprocess.run(dec, shell=True, stdin=fi, stdout=fo, stderr=subprocess.DEVNULL)
            ok = Path(o).read_bytes() == sample.read_bytes()
        prov = sq.tool_provenance(comp)
        print(f"  {comp:20} {'✓ lossless' if ok else '✗ LOSSY'}  [{prov['version'][:30]}, {prov['arch']}]")
        if not ok:
            fails.append(comp)
    if fails:
        print(f"  FAIL: not lossless on this host: {fails}"); return 1
    print("  PASS: all panel codecs round-trip losslessly on this host")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
