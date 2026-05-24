#!/usr/bin/env python3
"""Build a zstd concatenated stream with skippable metadata frames interleaved
between data frames.

Layout:
  [skippable frame: JSON metadata for file 1][zstd frame: file 1 content]
  [skippable frame: JSON metadata for file 2][zstd frame: file 2 content]
  ...

A compliant zstd decoder yields the concatenated decompressed bytes of the
data frames, skipping past the metadata. A library that supports skippable
frames can expose them as out-of-band info.
"""
from __future__ import annotations
import json, os, struct, subprocess, sys
from pathlib import Path

SKIPPABLE_MAGIC_BASE = 0x184D2A50  # magics 0x184D2A50..5F all denote skippable

def main(src: str, out: str) -> None:
    src_p = Path(src); out_p = Path(out)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in src_p.rglob("*") if p.is_file())
    with out_p.with_suffix(out_p.suffix + ".tmp").open("wb") as f:
        for fp in files:
            meta = json.dumps({
                "filename": fp.relative_to(src_p.parent).as_posix(),
                "size":     fp.stat().st_size,
            }, sort_keys=True).encode()
            # pad to 4-byte boundary so total skippable frame length is even-aligned
            pad = (-len(meta)) & 3
            payload = meta + b"\x00" * pad
            f.write(struct.pack("<II", SKIPPABLE_MAGIC_BASE, len(payload)))
            f.write(payload)

            # compress file as its own zstd frame, append
            r = subprocess.run(
                ["zstd", "-T1", "-19", "-q", "-f", "--no-progress", "-c", str(fp)],
                check=True, stdout=subprocess.PIPE,
            )
            f.write(r.stdout)
    out_p.with_suffix(out_p.suffix + ".tmp").rename(out_p)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
