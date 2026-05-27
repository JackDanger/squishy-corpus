"""Fetch and extract third-party source files used by the corpus.

Sources fetched:
  - Silesia corpus (tar) — classic LZ compression benchmark
  - jQuery minified JS — representative minified JavaScript
  - Bootstrap minified CSS — representative minified CSS
  - EFF homepage HTML — representative modern HTML

All downloads are idempotent: files already on disk are skipped.
SHA-256 of each fetched file is printed for audit purposes.
"""
from __future__ import annotations

import tarfile
import urllib.request
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import sha256_file, write_bytes_atomic


_SOURCES: list[tuple[str, str]] = [
    (
        "https://wanos.co/assets/silesia.tar",
        "silesia.tar",
    ),
    (
        "https://code.jquery.com/jquery-2.1.4.min.js",
        "modern/jquery-2.1.4.min.js",
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/3.3.6/css/bootstrap.min.css",
        "modern/bootstrap-3.3.6.min.css",
    ),
    (
        "https://www.eff.org/",
        "modern/eff.html",
    ),
]


def _fetch(url: str, dest: Path) -> None:
    """Download *url* to *dest* atomically, printing progress."""
    if dest.exists():
        print(f"  skip {dest.name} (already exists)")
        return

    print(f"  fetch {url} -> {dest.name} ...", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
        tmp.write_bytes(data)
        tmp.rename(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    digest = sha256_file(dest)
    print(f"  sha256 {dest.name}: {digest}")


def _extract_silesia(tar_path: Path, raw_dir: Path) -> None:
    """Extract silesia.tar into raw_dir/silesia/ if not already done."""
    out_dir = raw_dir / "silesia"
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"  skip silesia extraction (already extracted)")
        return

    print(f"  extracting {tar_path.name} -> {out_dir} ...", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r") as tf:
        # tarfile.extractall with filter='data' (Python 3.12+) or manual loop
        for member in tf.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                continue  # skip unsafe paths
            tf.extract(member, path=out_dir)
    print(f"  extracted silesia ({sum(1 for _ in out_dir.rglob('*'))} items)")


def run(cfg: BuildConfig) -> int:
    """Fetch all third-party sources. Returns 0 on success, 1 on failure."""
    try:
        # Silesia goes to sources_dir (as a tar), then extracted to raw_dir
        silesia_tar = cfg.sources_dir / "silesia.tar"
        _fetch("https://wanos.co/assets/silesia.tar", silesia_tar)
        if silesia_tar.exists():
            _extract_silesia(silesia_tar, cfg.raw_dir)

        # Modern files go directly into raw_dir
        for url, rel in _SOURCES[1:]:  # skip silesia, handled above
            dest = cfg.raw_dir / rel
            _fetch(url, dest)

        return 0
    except Exception as exc:
        print(f"  ERROR in sources: {exc}")
        return 1
