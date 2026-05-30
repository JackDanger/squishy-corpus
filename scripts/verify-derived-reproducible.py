#!/usr/bin/env python3
"""Freeze-blocker check: do the DERIVED corpus files rebuild byte-for-byte from their
pinned recipes? Re-derives the constructed artifacts and compares sha256 to the
recorded values. If any differs, the corpus is not freezable (a future party can't
reproduce the cited bytes). Reproduces the cheap-to-rebuild derived files:
  • clang multi-version archive (4 release .tar.xz → decompress → concat)
  • BTS all-string parquet, one month (pyarrow writer determinism, given pinned version)

  uv run --with pyarrow python scripts/verify-derived-reproducible.py
"""
from __future__ import annotations
import hashlib, importlib.util, json, lzma, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "squishy/1.0"}


def main() -> int:
    scale = json.loads((REPO / "build/meta/scale-properties.json").read_text())["files"]
    sums = {}
    for line in (REPO / "build/meta/CHECKSUMS.sha256").read_text().splitlines():
        p = line.split()
        if len(p) == 2:
            sums[p[1]] = p[0]
    fails = []

    # 1. clang archive
    want = scale["clang-releases-16-17-18-19.tar"]["sha256"]
    h = hashlib.sha256()
    for v in ("16.0.0", "17.0.1", "18.1.8", "19.1.0"):
        u = f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{v}/clang-{v}.src.tar.xz"
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=300) as r:
            src = lzma.LZMAFile(r)
            for c in iter(lambda: src.read(1 << 22), b""):
                h.update(c)
    got = h.hexdigest()
    ok = got == want
    print(f"  clang-archive   {'✓ reproduces' if ok else '✗ DIFFERS'}  {got[:12]} vs {want[:12]}")
    if not ok:
        fails.append("clang-archive")

    # 2. BTS parquet, core month (pyarrow writer)
    s = importlib.util.spec_from_file_location("bts", REPO / "scripts" / "scale-acquire-bts-parquet.py")
    bts = importlib.util.module_from_spec(s); s.loader.exec_module(bts)
    tmp = REPO / "build" / "raw" / "corpus" / "_repro.parquet"
    bts.build(tmp, bts.parse_months("2024-1"))
    got = hashlib.sha256(tmp.read_bytes()).hexdigest(); tmp.unlink()
    want = sums.get("corpus/data.parquet")
    ok = got == want
    print(f"  bts-parquet     {'✓ reproduces' if ok else '✗ DIFFERS'}  {got[:12]} vs {want[:12]}")
    if not ok:
        fails.append("bts-parquet")

    if fails:
        print(f"  FAIL: not byte-reproducible: {fails}")
        return 1
    print("  PASS: derived files reproduce byte-identical from their recipes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
