#!/usr/bin/env python3
"""Generate the locally-built portion of the modern set.

We don't ship raspbian/vmlinux/blender (license risk per advisor). Instead:
synthetic-but-representative modern files (JSON, NDJSON, SQLite, parquet,
protobuf-wire, log lines, random). Deterministic from a fixed seed.
"""
from __future__ import annotations
import csv, hashlib, io, json, os, sqlite3, struct, sys
from pathlib import Path

SEED = b"jackdanger-corpus-modern-v1"

def prng(seed: bytes, n: int) -> bytes:
    out = bytearray(); i = 0
    while len(out) < n:
        out += hashlib.sha256(seed + struct.pack(">Q", i)).digest()
        i += 1
    return bytes(out[:n])

def write_parquet(out: Path, seed: bytes) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        print("  SKIP sample.parquet — install pyarrow: pip install pyarrow", file=sys.stderr)
        return

    import numpy as np
    rng = np.random.default_rng(seed=int.from_bytes(hashlib.sha256(seed).digest()[:8], 'little'))

    n = 100_000
    categories = ["alpha", "beta", "gamma", "delta", "epsilon"]
    table = pa.table({
        "id":       pa.array(range(n), type=pa.int64()),
        "category": pa.array(rng.choice(categories, n).tolist()),  # low cardinality → RLE
        "name":     pa.array([f"user-{i:06d}" for i in range(n)]),  # high cardinality
        "score":    pa.array(rng.random(n).tolist(), type=pa.float64()),  # float column
        "active":   pa.array(rng.choice([True, False], n).tolist()),
        "ts_ms":    pa.array(rng.integers(1_600_000_000_000, 1_700_000_000_000, n).tolist(),
                             type=pa.int64()),
    })
    pq.write_table(table, str(out),
        compression="snappy",
        use_dictionary=True,
        write_statistics=True,
        data_page_size=64 * 1024,
        row_group_size=10_000,
        version="2.6",
    )
    print(f"  wrote {out} ({out.stat().st_size:,} bytes)", file=sys.stderr)

def write_protobuf(out: Path, seed: bytes) -> None:
    # Encode a realistic schema by hand using protobuf wire format.
    # Schema (conceptual):
    #   message Event {
    #     int64 timestamp = 1;           # varint
    #     string user_id = 2;            # length-delimited
    #     int32 kind = 3;                # varint (enum)
    #     repeated int32 tags = 4 [packed=true];  # length-delim + packed varints
    #     fixed64 session_id = 5;        # 8 bytes little-endian
    #     double score = 6;              # 8 bytes IEEE 754
    #   }

    def varint(n: int) -> bytes:
        result = []
        n = n & 0xFFFFFFFFFFFFFFFF
        while n > 0x7F:
            result.append((n & 0x7F) | 0x80)
            n >>= 7
        result.append(n)
        return bytes(result)

    def field_varint(fn: int, v: int) -> bytes:
        return varint((fn << 3) | 0) + varint(v)

    def field_len(fn: int, data: bytes) -> bytes:
        return varint((fn << 3) | 2) + varint(len(data)) + data

    def field_fixed64(fn: int, v: int) -> bytes:
        return varint((fn << 3) | 1) + struct.pack('<Q', v)

    def field_double(fn: int, v: float) -> bytes:
        return varint((fn << 3) | 1) + struct.pack('<d', v)

    rng_bytes = prng(seed + b":proto", 64 * 1024)
    events = bytearray()
    for i in range(10_000):
        ts = 1_600_000_000 + i * 60
        user_id = f"user-{(rng_bytes[i % len(rng_bytes)] * 256 + rng_bytes[(i+1) % len(rng_bytes)]) % 10000:04d}"
        kind = rng_bytes[(i+2) % len(rng_bytes)] % 4
        tags = [rng_bytes[(i+3+k) % len(rng_bytes)] % 64 for k in range(3)]
        session_id = int.from_bytes(rng_bytes[i*4 % (len(rng_bytes)-8): i*4 % (len(rng_bytes)-8)+8], 'little')
        score = (rng_bytes[(i*4) % len(rng_bytes)] * 256 + rng_bytes[(i*4+1) % len(rng_bytes)]) / 65536.0

        msg = bytearray()
        msg += field_varint(1, ts)
        msg += field_len(2, user_id.encode())
        msg += field_varint(3, kind)
        # packed repeated: length-delimited, contains varints concatenated
        packed_tags = b"".join(varint(t) for t in tags)
        msg += field_len(4, packed_tags)
        msg += field_fixed64(5, session_id)
        msg += field_double(6, score)

        # Outer message wraps as repeated field 1
        events += field_len(1, bytes(msg))

    out.write_bytes(bytes(events))

def write_csv(out: Path, seed: bytes) -> None:
    rng = prng(seed + b":csv", 4 * 1024 * 1024)

    statuses = ["active", "inactive", "pending", "suspended", "archived"]
    regions = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1", "ap-northeast-1"]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "user_id", "email", "status", "region", "score", "created_at", "tags"])

    for i in range(50_000):
        b = rng[i * 8 % (len(rng) - 8): i * 8 % (len(rng) - 8) + 8]
        user_n = (b[0] * 256 + b[1]) % 5000
        status = statuses[b[2] % len(statuses)]
        region = regions[b[3] % len(regions)]
        score = (b[4] * 256 + b[5]) / 655.36
        day = (b[6] % 365) + 1
        tags = f"tag-{b[7] % 10},tag-{b[6] % 20}"
        writer.writerow([
            i,
            f"user-{user_n:04d}",
            f"user{user_n:04d}@example.com",
            status,
            region,
            f"{score:.2f}",
            f"2024-{(day // 31) + 1:02d}-{(day % 28) + 1:02d}",
            tags,
        ])

    out.write_text(buf.getvalue())

def write_arrow(out: Path, seed: bytes) -> None:
    try:
        import pyarrow as pa
    except ImportError:
        print("  SKIP sample.arrow — install pyarrow: pip install pyarrow", file=sys.stderr)
        return

    import pyarrow.ipc as ipc
    import numpy as np
    rng = np.random.default_rng(seed=int.from_bytes(hashlib.sha256(seed + b":arrow").digest()[:8], 'little'))

    n = 50_000
    table = pa.table({
        "id":    pa.array(range(n), type=pa.int64()),
        "x":     pa.array(rng.random(n).tolist(), type=pa.float32()),
        "y":     pa.array(rng.random(n).tolist(), type=pa.float32()),
        "label": pa.array(rng.choice(["A","B","C","D"], n).tolist()),
        "value": pa.array(rng.integers(-1000, 1000, n).tolist(), type=pa.int32()),
    })
    sink = pa.BufferOutputStream()
    writer = ipc.new_file(sink, table.schema)
    writer.write_table(table, max_chunksize=10_000)
    writer.close()
    out.write_bytes(sink.getvalue().to_pybytes())
    print(f"  wrote {out} ({out.stat().st_size:,} bytes)", file=sys.stderr)

def write_wasm(out: Path, seed: bytes) -> None:
    def leb128_u(n: int) -> bytes:
        result = []
        while True:
            byte = n & 0x7F
            n >>= 7
            if n != 0:
                byte |= 0x80
            result.append(byte)
            if n == 0:
                break
        return bytes(result)

    def section(sid: int, data: bytes) -> bytes:
        return bytes([sid]) + leb128_u(len(data)) + data

    magic = b'\x00asm'
    version = b'\x01\x00\x00\x00'

    n_funcs = 200

    # Type section: one type (i32, i32) -> i32
    type_entry = bytes([0x60, 0x02, 0x7f, 0x7f, 0x01, 0x7f])  # (i32,i32)->i32
    type_sec = section(0x01, leb128_u(1) + type_entry)

    # Function section: n_funcs functions all of type 0
    func_sec = section(0x03, leb128_u(n_funcs) + b'\x00' * n_funcs)

    # Code section: n_funcs functions with varying bodies
    rng = prng(seed + b":wasm", n_funcs * 4)
    bodies = []
    for i in range(n_funcs):
        op = rng[i % len(rng)] % 4
        if op == 0:   # local.get 0 + local.get 1 + i32.add
            body = bytes([0x20, 0x00, 0x20, 0x01, 0x6a])
        elif op == 1: # local.get 0 + local.get 1 + i32.mul
            body = bytes([0x20, 0x00, 0x20, 0x01, 0x6c])
        elif op == 2: # local.get 0 + local.get 1 + i32.sub
            body = bytes([0x20, 0x00, 0x20, 0x01, 0x6b])
        else:         # local.get 0 + local.get 1 + i32.and
            body = bytes([0x20, 0x00, 0x20, 0x01, 0x71])
        # locals count (0) + body + end
        func_body = leb128_u(0) + body + b'\x0b'
        bodies.append(leb128_u(len(func_body)) + func_body)
    code_content = leb128_u(n_funcs) + b''.join(bodies)
    code_sec = section(0x0a, code_content)

    out.write_bytes(magic + version + type_sec + func_sec + code_sec)

def write_msgpack(out: Path, seed: bytes) -> None:
    try:
        import msgpack
    except ImportError:
        print("  SKIP sample.msgpack — install msgpack: pip install msgpack", file=sys.stderr)
        return

    rng = prng(seed + b":msgpack", 128 * 1024)
    records = []
    for i in range(10_000):
        b = rng[i * 8 % (len(rng) - 8): i * 8 % (len(rng) - 8) + 8]
        records.append({
            "id": i,
            "v": (b[0] * 256 + b[1]),
            "s": b[2] % 5,
            "f": (b[3] * 256 + b[4]) / 65536.0,
            "t": [b[5] % 16, b[6] % 16, b[7] % 16],
        })
    out.write_bytes(msgpack.packb(records, use_bin_type=True))
    print(f"  wrote {out} ({out.stat().st_size:,} bytes)", file=sys.stderr)

def write_utf8_samples(out_dir: Path, seed: bytes) -> None:
    # Chinese: CJK Unified Ideographs U+4E00-U+9FFF (common Chinese)
    cjk = [chr(c) for c in range(0x4E00, 0x9FFF)]
    rng = prng(seed + b":zh", 2 * 1024 * 1024)
    chars = [cjk[b % len(cjk)] for b in rng[:100_000]]
    # Add spaces and punctuation for realistic text structure
    text = ""
    for i, ch in enumerate(chars):
        text += ch
        if i % 20 == 19: text += "。\n"  # Chinese period + newline
        elif i % 5 == 4: text += "，"     # Chinese comma
    (out_dir / "sample.utf8-zh.txt").write_text(text, encoding="utf-8")

    # Japanese: Hiragana (U+3040-U+309F) + Katakana (U+30A0-U+30FF) + common Kanji
    hira = [chr(c) for c in range(0x3041, 0x3097)]
    kata = [chr(c) for c in range(0x30A1, 0x30F7)]
    ja_chars = hira + kata
    rng = prng(seed + b":ja", 2 * 1024 * 1024)
    chars = [ja_chars[b % len(ja_chars)] for b in rng[:100_000]]
    text = ""
    for i, ch in enumerate(chars):
        text += ch
        if i % 30 == 29: text += "。\n"
        elif i % 8 == 7: text += "、"
    (out_dir / "sample.utf8-ja.txt").write_text(text, encoding="utf-8")

    # Arabic: Arabic script U+0600-U+06FF
    arabic = [chr(c) for c in range(0x0621, 0x0650)]  # Arabic letters
    rng = prng(seed + b":ar", 2 * 1024 * 1024)
    chars = [arabic[b % len(arabic)] for b in rng[:100_000]]
    text = ""
    for i, ch in enumerate(chars):
        text += ch
        if i % 25 == 24: text += ".\n"
        elif i % 6 == 5: text += " "
    (out_dir / "sample.utf8-ar.txt").write_text(text, encoding="utf-8")

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

    # sample.parquet — real parquet via pyarrow (skipped if not installed)
    write_parquet(out / "sample.parquet", SEED + b":parquet")

    # sample.protobuf — real protobuf wire format with multiple field types
    print("  writing sample.protobuf...", file=sys.stderr)
    write_protobuf(out / "sample.protobuf", SEED)

    # sample.csv — realistic CSV with mixed column types
    print("  writing sample.csv...", file=sys.stderr)
    write_csv(out / "sample.csv", SEED)

    # sample.arrow — Arrow IPC format (skipped if pyarrow not installed)
    write_arrow(out / "sample.arrow", SEED)

    # sample.wasm — valid WebAssembly binary with 200 functions
    print("  writing sample.wasm...", file=sys.stderr)
    write_wasm(out / "sample.wasm", SEED)

    # sample.msgpack — MessagePack binary encoding (skipped if msgpack not installed)
    write_msgpack(out / "sample.msgpack", SEED)

    # UTF-8 multibyte text samples
    print("  writing UTF-8 multibyte samples...", file=sys.stderr)
    write_utf8_samples(out, SEED)

    # random-1M — deterministic incompressible bytes
    (out / "random-1M").write_bytes(prng(SEED + b":random", 1024 * 1024))

    print(f"wrote modern synthetic inputs to {out}", file=sys.stderr)

if __name__ == "__main__":
    main(sys.argv[1])
