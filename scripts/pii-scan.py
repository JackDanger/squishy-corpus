#!/usr/bin/env python3
"""PII / secret scanner for corpus core files.

A 20-year public corpus must not leak credentials or personal data. This scans
given files (or the core raw files present locally) for credential patterns and
PII signals. The `log` core file (real web-server traffic) is the highest-risk
and MUST be clean before freeze. Findings are reported; exit non-zero on any
credential hit.

Usage:
  uv run python scripts/pii-scan.py [FILE ...]     # default: core files on disk
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# High-confidence credential patterns → any hit FAILS the scan.
CREDENTIAL = {
    "aws-access-key": rb"AKIA[0-9A-Z]{16}",
    "private-key-block": rb"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "github-pat": rb"ghp_[0-9A-Za-z]{36}",
    "slack-token": rb"xox[baprs]-[0-9A-Za-z-]{10,}",
    "google-api-key": rb"AIza[0-9A-Za-z_\-]{35}",
    "jwt": rb"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    "aws-secret-hint": rb"aws_secret_access_key",
}
# Lower-confidence PII signals → reported as WARN (review), not auto-fail
# (email/IP are legitimately present in some real data, e.g. the access log).
PII = {
    "email": rb"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
    "ipv4": rb"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "ssn-like": rb"\b\d{3}-\d{2}-\d{4}\b",
    "credit-card-like": rb"\b(?:\d[ -]*?){13,16}\b",
}


def _scan_bytes(data: bytes) -> tuple[dict, dict]:
    creds = {k: len(re.findall(p, data)) for k, p in CREDENTIAL.items()}
    pii = {k: len(re.findall(p, data)) for k, p in PII.items()}
    return ({k: v for k, v in creds.items() if v},
            {k: v for k, v in pii.items() if v})


def scan(path: Path) -> tuple[dict, dict]:
    return _scan_bytes(path.read_bytes())


def scan_structured(path: Path, cap: int = 64 << 20) -> tuple[dict, dict] | None:
    """Decode the *string cells* of a SQLite DB or Parquet file and scan those.
    Catches PII stored non-contiguously (which raw-byte regex misses) and skips
    numeric columns (which produce credit-card-like false positives). Returns
    None if the format isn't structured or its reader is unavailable."""
    head = path.read_bytes()[:16]
    buf = bytearray()

    def add(v):
        if isinstance(v, str) and not v.isdigit():
            buf.extend(v.encode("utf-8", "replace") + b"\n")

    if head.startswith(b"SQLite format 3"):
        import sqlite3
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            for row in con.execute(f'SELECT * FROM "{t}"'):
                for cell in row:
                    add(cell)
                if len(buf) >= cap:
                    break
        con.close()
    elif head[:4] == b"PAR1":
        try:
            import pyarrow.parquet as pq
        except Exception:
            return None  # reader unavailable → caller notes parquet structured scan skipped
        t = pq.read_table(path)
        import pyarrow as pa
        for name in t.column_names:
            col = t.column(name)
            if pa.types.is_string(col.type) or pa.types.is_large_string(col.type):
                for v in col.to_pylist():
                    add(v)
                    if len(buf) >= cap:
                        break
    else:
        return None
    return _scan_bytes(bytes(buf))


def core_files_on_disk() -> list[Path]:
    sys.path.insert(0, str(REPO / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(spec); spec.loader.exec_module(sq)
    out = []
    for files in sq.CORE.values():
        for _d, s, name in files:
            p = REPO / "build" / "raw" / s / name
            if p.is_file():
                out.append(p)
    return out


def main() -> int:
    targets = [Path(a) for a in sys.argv[1:]] or core_files_on_disk()
    if not targets:
        print("no files to scan (core not yet acquired locally)")
        return 0
    failed = False
    for p in targets:
        creds, pii = scan(p)
        status = "FAIL" if creds else ("warn" if pii else "ok")
        print(f"[{status}] {p.name}: creds={creds or '-'} pii={pii or '-'}")
        if creds:
            failed = True
        # structured pass for DB/columnar files (decoded string cells only)
        st = scan_structured(p)
        if st is None and p.read_bytes()[:4] == b"PAR1":
            print(f"       └ structured: parquet reader unavailable — re-run with "
                  f"`uv run --with pyarrow python scripts/pii-scan.py {p}`")
        elif st is not None:
            screds, spii = st
            sstatus = "FAIL" if screds else ("warn" if spii else "ok")
            print(f"       └ structured[{sstatus}] {p.name}: creds={screds or '-'} pii={spii or '-'} "
                  f"(string cells only)")
            if screds:
                failed = True
    print(f"\nscanned {len(targets)} file(s); {'CREDENTIAL FINDINGS — must fix' if failed else 'no credential findings'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
