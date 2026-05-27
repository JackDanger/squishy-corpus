"""Generate the locally-built portion of the modern file set.

We avoid shipping raspbian/vmlinux/blender (license risk). Instead we produce
synthetic-but-representative modern formats: JSON, NDJSON, SQLite, Parquet
(optional, requires pyarrow), protobuf wire format, CSV, Arrow IPC (optional),
WebAssembly binary, MessagePack (optional), UTF-8 multibyte text, and a
deterministic 1 MiB pseudo-random blob.

All output is deterministic from a fixed seed so repeated runs are idempotent.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import struct
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import write_bytes_atomic

SEED = b"jackdanger-corpus-modern-v1"


def _prng(seed: bytes, n: int) -> bytes:
    """SHA-256 counter-mode PRNG — deterministic, stdlib-only."""
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(seed + struct.pack(">Q", i)).digest()
        i += 1
    return bytes(out[:n])


def _write(path: Path, data: bytes) -> None:
    print(f"  {path.name} ({len(data):,} bytes)")
    write_bytes_atomic(path, data)


def _write_parquet(out: Path, seed: bytes) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        print("  SKIP sample.parquet — install pyarrow: pip install pyarrow")
        return

    import numpy as np
    rng = np.random.default_rng(
        seed=int.from_bytes(hashlib.sha256(seed).digest()[:8], "little")
    )

    n = 100_000
    categories = ["alpha", "beta", "gamma", "delta", "epsilon"]
    table = pa.table({
        "id":       pa.array(range(n), type=pa.int64()),
        "category": pa.array(rng.choice(categories, n).tolist()),
        "name":     pa.array([f"user-{i:06d}" for i in range(n)]),
        "score":    pa.array(rng.random(n).tolist(), type=pa.float64()),
        "active":   pa.array(rng.choice([True, False], n).tolist()),
        "ts_ms":    pa.array(
            rng.integers(1_600_000_000_000, 1_700_000_000_000, n).tolist(),
            type=pa.int64(),
        ),
    })
    pq.write_table(
        table, str(out),
        compression="snappy",
        use_dictionary=True,
        write_statistics=True,
        data_page_size=64 * 1024,
        row_group_size=10_000,
        version="2.6",
    )
    print(f"  sample.parquet ({out.stat().st_size:,} bytes)")


def _write_protobuf(out: Path, seed: bytes) -> None:
    """Hand-encode protobuf wire format (no protoc dependency).

    Schema (conceptual):
      message EventBatch {
        repeated Event events = 1;
      }
      message Event {
        int64 timestamp = 1;
        string user_id = 2;
        int32 kind = 3;
        repeated int32 tags = 4 [packed=true];
        fixed64 session_id = 5;
        double score = 6;
      }
    """
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
        return varint((fn << 3) | 1) + struct.pack("<Q", v)

    def field_double(fn: int, v: float) -> bytes:
        return varint((fn << 3) | 1) + struct.pack("<d", v)

    rng_bytes = _prng(seed + b":proto", 64 * 1024)
    events = bytearray()
    for i in range(10_000):
        ts = 1_600_000_000 + i * 60
        user_id = (
            f"user-{(rng_bytes[i % len(rng_bytes)] * 256 + rng_bytes[(i + 1) % len(rng_bytes)]) % 10000:04d}"
        )
        kind = rng_bytes[(i + 2) % len(rng_bytes)] % 4
        tags = [rng_bytes[(i + 3 + k) % len(rng_bytes)] % 64 for k in range(3)]
        session_id = int.from_bytes(
            rng_bytes[i * 4 % (len(rng_bytes) - 8): i * 4 % (len(rng_bytes) - 8) + 8],
            "little",
        )
        score = (
            rng_bytes[(i * 4) % len(rng_bytes)] * 256
            + rng_bytes[(i * 4 + 1) % len(rng_bytes)]
        ) / 65536.0

        msg = bytearray()
        msg += field_varint(1, ts)
        msg += field_len(2, user_id.encode())
        msg += field_varint(3, kind)
        packed_tags = b"".join(varint(t) for t in tags)
        msg += field_len(4, packed_tags)
        msg += field_fixed64(5, session_id)
        msg += field_double(6, score)

        events += field_len(1, bytes(msg))

    _write(out, bytes(events))


def _write_csv(out: Path, seed: bytes) -> None:
    rng_data = _prng(seed + b":csv", 4 * 1024 * 1024)

    statuses = ["active", "inactive", "pending", "suspended", "archived"]
    regions = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1", "ap-northeast-1"]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "user_id", "email", "status", "region", "score", "created_at", "tags"])

    for i in range(50_000):
        b = rng_data[i * 8 % (len(rng_data) - 8): i * 8 % (len(rng_data) - 8) + 8]
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

    _write(out, buf.getvalue().encode())


def _write_arrow(out: Path, seed: bytes) -> None:
    try:
        import pyarrow as pa
        import pyarrow.ipc as ipc
    except ImportError:
        print("  SKIP sample.arrow — install pyarrow: pip install pyarrow")
        return

    import numpy as np
    rng = np.random.default_rng(
        seed=int.from_bytes(hashlib.sha256(seed + b":arrow").digest()[:8], "little")
    )

    n = 50_000
    table = pa.table({
        "id":    pa.array(range(n), type=pa.int64()),
        "x":     pa.array(rng.random(n).tolist(), type=pa.float32()),
        "y":     pa.array(rng.random(n).tolist(), type=pa.float32()),
        "label": pa.array(rng.choice(["A", "B", "C", "D"], n).tolist()),
        "value": pa.array(rng.integers(-1000, 1000, n).tolist(), type=pa.int32()),
    })
    sink = pa.BufferOutputStream()
    writer = ipc.new_file(sink, table.schema)
    writer.write_table(table, max_chunksize=10_000)
    writer.close()
    _write(out, sink.getvalue().to_pybytes())


def _write_wasm(out: Path, seed: bytes) -> None:
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

    magic = b"\x00asm"
    version = b"\x01\x00\x00\x00"

    n_funcs = 200
    type_entry = bytes([0x60, 0x02, 0x7F, 0x7F, 0x01, 0x7F])  # (i32,i32)->i32
    type_sec = section(0x01, leb128_u(1) + type_entry)
    func_sec = section(0x03, leb128_u(n_funcs) + b"\x00" * n_funcs)

    rng_data = _prng(seed + b":wasm", n_funcs * 4)
    bodies = []
    for i in range(n_funcs):
        op = rng_data[i % len(rng_data)] % 4
        if op == 0:
            body = bytes([0x20, 0x00, 0x20, 0x01, 0x6A])   # i32.add
        elif op == 1:
            body = bytes([0x20, 0x00, 0x20, 0x01, 0x6C])   # i32.mul
        elif op == 2:
            body = bytes([0x20, 0x00, 0x20, 0x01, 0x6B])   # i32.sub
        else:
            body = bytes([0x20, 0x00, 0x20, 0x01, 0x71])   # i32.and
        func_body = leb128_u(0) + body + b"\x0B"
        bodies.append(leb128_u(len(func_body)) + func_body)

    code_content = leb128_u(n_funcs) + b"".join(bodies)
    code_sec = section(0x0A, code_content)

    _write(out, magic + version + type_sec + func_sec + code_sec)


def _write_msgpack(out: Path, seed: bytes) -> None:
    try:
        import msgpack
    except ImportError:
        print("  SKIP sample.msgpack — install msgpack: pip install msgpack")
        return

    rng_data = _prng(seed + b":msgpack", 128 * 1024)
    records = []
    for i in range(10_000):
        b = rng_data[i * 8 % (len(rng_data) - 8): i * 8 % (len(rng_data) - 8) + 8]
        records.append({
            "id": i,
            "v": (b[0] * 256 + b[1]),
            "s": b[2] % 5,
            "f": (b[3] * 256 + b[4]) / 65536.0,
            "t": [b[5] % 16, b[6] % 16, b[7] % 16],
        })
    _write(out, msgpack.packb(records, use_bin_type=True))


def _write_utf8_samples(out_dir: Path, seed: bytes) -> None:
    # Chinese: CJK Unified Ideographs U+4E00-U+9FFF
    cjk = [chr(c) for c in range(0x4E00, 0x9FFF)]
    rng_data = _prng(seed + b":zh", 2 * 1024 * 1024)
    chars = [cjk[b % len(cjk)] for b in rng_data[:100_000]]
    text = ""
    for i, ch in enumerate(chars):
        text += ch
        if i % 20 == 19:
            text += "。\n"
        elif i % 5 == 4:
            text += "，"
    write_bytes_atomic(out_dir / "sample.utf8-zh.txt", text.encode("utf-8"))
    print(f"  sample.utf8-zh.txt ({len(text.encode('utf-8')):,} bytes)")

    # Japanese: Hiragana + Katakana
    hira = [chr(c) for c in range(0x3041, 0x3097)]
    kata = [chr(c) for c in range(0x30A1, 0x30F7)]
    ja_chars = hira + kata
    rng_data = _prng(seed + b":ja", 2 * 1024 * 1024)
    chars = [ja_chars[b % len(ja_chars)] for b in rng_data[:100_000]]
    text = ""
    for i, ch in enumerate(chars):
        text += ch
        if i % 30 == 29:
            text += "。\n"
        elif i % 8 == 7:
            text += "、"
    write_bytes_atomic(out_dir / "sample.utf8-ja.txt", text.encode("utf-8"))
    print(f"  sample.utf8-ja.txt ({len(text.encode('utf-8')):,} bytes)")

    # Arabic: U+0621-U+064F
    arabic = [chr(c) for c in range(0x0621, 0x0650)]
    rng_data = _prng(seed + b":ar", 2 * 1024 * 1024)
    chars = [arabic[b % len(arabic)] for b in rng_data[:100_000]]
    text = ""
    for i, ch in enumerate(chars):
        text += ch
        if i % 25 == 24:
            text += ".\n"
        elif i % 6 == 5:
            text += " "
    write_bytes_atomic(out_dir / "sample.utf8-ar.txt", text.encode("utf-8"))
    print(f"  sample.utf8-ar.txt ({len(text.encode('utf-8')):,} bytes)")


def run(cfg: BuildConfig) -> int:
    """Generate all modern synthetic files. Returns 0 on success, 1 on failure."""
    try:
        out = cfg.raw_dir / "modern"
        out.mkdir(parents=True, exist_ok=True)

        # sample.json — pretty-printed nested record collection (~2 MiB)
        records = []
        rng_data = _prng(SEED + b":json", 2 * 1024 * 1024)
        for i in range(5000):
            records.append({
                "id": i,
                "name": f"record-{i:05d}",
                "tags": [f"tag-{(rng_data[i * 4 + k] % 32)}" for k in range(3)],
                "score": (rng_data[i * 4] * 256 + rng_data[i * 4 + 1]) / 65536.0,
                "active": bool(rng_data[i * 4 + 2] & 1),
                "metadata": {
                    "created_at": "2026-01-01T00:00:00Z",
                    "owner": f"user-{(rng_data[i * 4 + 3] % 100):03d}",
                },
            })
        json_text = json.dumps(records, indent=2, sort_keys=True)
        _write(out / "sample.json", json_text.encode())

        # sample.ndjson — one JSON object per line
        ndjson_lines = "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n"
        _write(out / "sample.ndjson", ndjson_lines.encode())

        # sample.log — synthetic syslog-like lines
        levels = ["INFO", "WARN", "ERROR", "DEBUG"]
        services = ["api", "worker", "scheduler", "auth", "db"]
        log_lines = []
        rng_data = _prng(SEED + b":log", 1024 * 1024)
        for i in range(20000):
            ts = f"2026-01-01T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}Z"
            lvl = levels[rng_data[i % len(rng_data)] % len(levels)]
            svc = services[rng_data[(i + 1) % len(rng_data)] % len(services)]
            log_lines.append(
                f"{ts} {lvl:5s} {svc:9s} request_id=req-{i:08d}"
                f" latency_ms={rng_data[(i + 2) % len(rng_data)]} status=200\n"
            )
        _write(out / "sample.log", "".join(log_lines).encode())

        # sample.sqlite — small SQLite db with reproducible content
        db_path = out / "sample.sqlite"
        if db_path.exists():
            db_path.unlink()
        con = sqlite3.connect(str(db_path))
        con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, value REAL)")
        rng_data = _prng(SEED + b":sqlite", 64 * 1024)
        for i in range(2000):
            con.execute(
                "INSERT INTO items (id, name, value) VALUES (?, ?, ?)",
                (i, f"item-{i:05d}", (rng_data[i * 4] * 256 + rng_data[i * 4 + 1]) / 65536.0),
            )
        con.commit()
        con.close()
        print(f"  sample.sqlite ({db_path.stat().st_size:,} bytes)")

        # sample.parquet — real parquet via pyarrow (skipped if not installed)
        _write_parquet(out / "sample.parquet", SEED + b":parquet")

        # sample.protobuf — protobuf wire format
        print(f"  writing sample.protobuf...", flush=True)
        _write_protobuf(out / "sample.protobuf", SEED)

        # sample.csv — realistic CSV
        print(f"  writing sample.csv...", flush=True)
        _write_csv(out / "sample.csv", SEED)

        # sample.arrow — Arrow IPC (skipped if pyarrow not installed)
        _write_arrow(out / "sample.arrow", SEED)

        # sample.wasm — valid WebAssembly binary
        print(f"  writing sample.wasm...", flush=True)
        _write_wasm(out / "sample.wasm", SEED)

        # sample.msgpack — MessagePack (skipped if msgpack not installed)
        _write_msgpack(out / "sample.msgpack", SEED)

        # UTF-8 multibyte text samples
        print(f"  writing UTF-8 multibyte samples...", flush=True)
        _write_utf8_samples(out, SEED)

        # random-1M — deterministic incompressible bytes
        _write(out / "random-1M", _prng(SEED + b":random", 1024 * 1024))

        print(f"  modern: all files written to {out}")
        return 0

    except Exception as exc:
        print(f"  ERROR in modern: {exc}")
        import traceback; traceback.print_exc()
        return 1
