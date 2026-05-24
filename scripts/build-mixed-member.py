#!/usr/bin/env python3
"""Build a deliberately mixed-member stream that a universal decompressor
might mis-handle: gzip member + zstd skippable frame + gzip member.

Strictly speaking only zstd defines a "skippable frame" semantics. Tools that
auto-detect by magic and then loop until EOF are tested here for whether
they break, leak, or recover gracefully.
"""
from __future__ import annotations
import gzip, io, json, struct, sys
from pathlib import Path

def gz(payload: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as g:
        g.write(payload)
    return buf.getvalue()

def zstd_skippable(meta: dict) -> bytes:
    payload = json.dumps(meta, sort_keys=True).encode()
    pad = (-len(payload)) & 3
    payload += b"\x00" * pad
    return struct.pack("<II", 0x184D2A50, len(payload)) + payload

def main(src: str, out: str) -> None:
    src_p = Path(src); out_p = Path(out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src_p.iterdir() if p.is_file())[:3]
    with out_p.with_suffix(out_p.suffix + ".tmp").open("wb") as f:
        f.write(gz(files[0].read_bytes()))
        f.write(zstd_skippable({"note": "mixed-member-stream",
                                "between": [files[0].name, files[1].name]}))
        f.write(gz(files[1].read_bytes()))
        f.write(zstd_skippable({"note": "end-marker"}))
        f.write(gz(files[2].read_bytes()))
    out_p.with_suffix(out_p.suffix + ".tmp").rename(out_p)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
