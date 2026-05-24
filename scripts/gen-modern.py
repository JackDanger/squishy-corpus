#!/usr/bin/env python3
"""Generate the locally-built portion of the modern set.

We don't ship raspbian/vmlinux/blender (license risk per advisor). Instead:
synthetic-but-representative modern files (JSON, NDJSON, SQLite, parquet,
protobuf-wire, log lines, random). Deterministic from a fixed seed.
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, struct, sys
from pathlib import Path

SEED = b"jackdanger-corpus-modern-v1"

def prng(seed: bytes, n: int) -> bytes:
    out = bytearray(); i = 0
    while len(out) < n:
        out += hashlib.sha256(seed + struct.pack(">Q", i)).digest()
        i += 1
    return bytes(out[:n])

def main(outdir: str) -> None:
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)

    # sample.json — pretty-printed nested record collection (~2 MiB)
    records = []
    rng = prng(SEED + b":json", 2 * 1024 * 1024)
    for i in range(5000):
        records.append({
            "id": i,
            "name": f"record-{i:05d}",
            "tags": [f"tag-{(rng[i*4 + k] % 32)}" for k in range(3)],
            "score": (rng[i*4] * 256 + rng[i*4 + 1]) / 65536.0,
            "active": bool(rng[i*4 + 2] & 1),
            "metadata": {
                "created_at": "2026-01-01T00:00:00Z",
                "owner": f"user-{(rng[i*4 + 3] % 100):03d}",
            },
        })
    (out / "sample.json").write_text(json.dumps(records, indent=2, sort_keys=True))

    # sample.ndjson — one JSON object per line (log-shaped)
    with (out / "sample.ndjson").open("w") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")

    # sample.log — synthetic syslog-like lines
    levels = ["INFO", "WARN", "ERROR", "DEBUG"]
    services = ["api", "worker", "scheduler", "auth", "db"]
    with (out / "sample.log").open("w") as f:
        rng = prng(SEED + b":log", 1024 * 1024)
        for i in range(20000):
            ts = f"2026-01-01T{(i//3600)%24:02d}:{(i//60)%60:02d}:{i%60:02d}Z"
            lvl = levels[rng[i % len(rng)] % len(levels)]
            svc = services[rng[(i+1) % len(rng)] % len(services)]
            f.write(f"{ts} {lvl:5s} {svc:9s} request_id=req-{i:08d} latency_ms={rng[(i+2)%len(rng)]} status=200\n")

    # sample.sqlite — small SQLite db with reproducible content
    db_path = out / "sample.sqlite"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, value REAL)")
    rng = prng(SEED + b":sqlite", 64 * 1024)
    for i in range(2000):
        con.execute(
            "INSERT INTO items (id, name, value) VALUES (?, ?, ?)",
            (i, f"item-{i:05d}", (rng[i*4] * 256 + rng[i*4+1]) / 65536.0),
        )
    con.commit(); con.close()

    # sample.parquet — written via the parquet crate is overkill; we instead
    # write a tiny synthetic file that LOOKS like parquet (PAR1 magic + footer).
    # Real parquet libs may not parse this; documented as "shape only" fixture.
    parquet = b"PAR1" + prng(SEED + b":parquet", 256 * 1024) + b"PAR1"
    (out / "sample.parquet").write_bytes(parquet)

    # sample.protobuf — protobuf-wire bytes by hand: a few varint+length-delim
    # fields. Decoder-shape-only fixture.
    pb = bytearray()
    for i in range(10000):
        # field 1, varint
        pb += b"\x08" + bytes([i & 0x7f])
        # field 2, length-delimited string
        s = f"name-{i:05d}".encode()
        pb += b"\x12" + bytes([len(s)]) + s
    (out / "sample.protobuf").write_bytes(bytes(pb))

    # random-1M — deterministic incompressible bytes
    (out / "random-1M").write_bytes(prng(SEED + b":random", 1024 * 1024))

    print(f"wrote modern synthetic inputs to {out}", file=sys.stderr)

if __name__ == "__main__":
    main(sys.argv[1])
