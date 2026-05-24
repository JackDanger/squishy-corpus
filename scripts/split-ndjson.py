#!/usr/bin/env python3
"""Split an NDJSON file into per-line files for zstd dictionary training.
Usage: split-ndjson.py <input.ndjson> <out-dir> <max-files>
"""
from __future__ import annotations
import sys
from pathlib import Path

def main(inp: str, outdir: str, max_n: int) -> None:
    p = Path(inp); out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    with p.open("rb") as f:
        for i, line in enumerate(f):
            if i >= max_n: break
            (out / f"{i:06d}.json").write_bytes(line)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]))
