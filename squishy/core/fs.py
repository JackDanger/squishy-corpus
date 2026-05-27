"""Filesystem utilities: atomic writes, content hashing, directory helpers."""
from __future__ import annotations

import hashlib
from pathlib import Path


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically via a .tmp sibling, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.rename(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def write_text_atomic(path: Path, text: str, encoding: str = "utf-8") -> None:
    write_bytes_atomic(path, text.encode(encoding))


def sha256_file(path: Path) -> str:
    """Return hex SHA-256 of *path*, reading in 1 MiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_size(path: Path) -> int:
    return path.stat().st_size
