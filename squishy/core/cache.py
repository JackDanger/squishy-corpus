"""Content-addressed SQLite build cache.

Cache key = SHA-256( input_content_hash  ‖ tag  ‖ flags  ‖ binary  ‖ version  ‖ machine )

Two artifacts with identical inputs and tool versions get the same key, so the
cache is correct across machines and time regardless of filesystem timestamps.

Thread safety: each thread gets its own SQLite connection via threading.local().
The database is opened in WAL mode so readers never block writers.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

_tls = threading.local()

_CREATE = """
CREATE TABLE IF NOT EXISTS builds (
    key     TEXT PRIMARY KEY,
    status  TEXT NOT NULL,
    out     TEXT NOT NULL,
    sha256  TEXT NOT NULL,
    size    INTEGER NOT NULL
)
"""


def _conn(db_path: Path) -> sqlite3.Connection:
    """Return the thread-local SQLite connection, creating it if needed."""
    if not hasattr(_tls, "conns"):
        _tls.conns = {}
    key = str(db_path)
    if key not in _tls.conns:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(_CREATE)
        conn.commit()
        _tls.conns[key] = conn
    return _tls.conns[key]


def make_key(
    inp_hash: str,
    tag: str,
    cmd_flags: str,
    binary: str,
    binary_version: str,
    machine: str,
) -> str:
    """Deterministic cache key from all inputs that affect the output bytes."""
    raw = "\0".join([inp_hash, tag, cmd_flags, binary, binary_version, machine])
    return hashlib.sha256(raw.encode()).hexdigest()


def lookup(db_path: Path, key: str) -> str | None:
    """Return cached status ('ok') or None if not found."""
    row = _conn(db_path).execute(
        "SELECT status FROM builds WHERE key=?", (key,)
    ).fetchone()
    return row[0] if row else None


def record(
    db_path: Path,
    key: str,
    status: str,
    out_path: str,
    sha256: str,
    size: int,
) -> None:
    conn = _conn(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO builds VALUES (?,?,?,?,?)",
        (key, status, out_path, sha256, size),
    )
    conn.commit()


def load_all(db_path: Path) -> dict[str, str]:
    """Return {key: status} for all cached entries."""
    if not db_path.exists():
        return {}
    return {
        row[0]: row[1]
        for row in _conn(db_path).execute("SELECT key, status FROM builds")
    }


def wipe(db_path: Path) -> None:
    """Delete all cached entries (used by --clean)."""
    if db_path.exists():
        _conn(db_path).execute("DELETE FROM builds")
        _conn(db_path).commit()
