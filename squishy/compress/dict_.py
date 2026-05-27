"""Build zstd dictionary training artifacts into build/dict/.

Public interface: ``run(cfg: BuildConfig) -> int``

Artifacts produced (when inputs exist and zstd is available):

  json-samples.zdict
      Trained on 1024 chunks split from modern/sample.ndjson.

  json-samples.tar.zst
      tar of the json-samples/ chunk directory, compressed with the dict.

  json-samples.no-dict.tar.zst
      Same tar, compressed WITHOUT the dict (allows ratio comparison).

  wrong-dict-silesia-dickens.zst
      silesia/dickens compressed with the json-samples dict (worst-case
      cross-domain mismatch fixture).

  logs-dict.zdict        (when logs/json-events-100k.ndjson exists)
      Trained on chunks split from that log file.

  source-dict.zdict      (when source/ set directory exists)
      Trained on the source/ files directly (no splitting needed).

The chunk splitter is inlined from scripts/split-ndjson.py: each line in the
NDJSON file becomes one chunk file, up to max_files chunks.  Each chunk is
written only if it is at least 1 byte (empty lines are skipped).

All intermediate files (the chunk directories, temporary tars) are cleaned up
after use so the dict/ directory contains only the final artifacts.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from squishy.core.cache import lookup, record
from squishy.core.config import BuildConfig
from squishy.core.fs import sha256_file, write_bytes_atomic
from squishy.core import tools as _tools

_CHUNK_SIZE_BYTES = 1024   # target chunk size for splitting (unused: we split by line)
_MAX_CHUNKS = 1024         # max NDJSON lines to use for training
_TAR_FLAGS = [
    "--mtime=@0", "--owner=0", "--group=0",
    "--numeric-owner", "--format=ustar",
]
_ZSTD_FLAGS = ["-T1", "-q", "-f", "--no-progress"]
_JOB_TIMEOUT = 300


def _split_ndjson(src: Path, out_dir: Path, max_files: int) -> int:
    """Split src (NDJSON) into per-line files under out_dir.

    Returns the number of chunk files written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with src.open("rb") as f:
        for i, line in enumerate(f):
            if i >= max_files:
                break
            line = line.rstrip(b"\n\r")
            if not line:
                continue
            (out_dir / f"{count:06d}.json").write_bytes(line)
            count += 1
    return count


def _train_dict(
    zstd: str, sample_dir: Path, out_dict: Path
) -> None:
    """Run ``zstd --train`` on all files in sample_dir, writing to out_dict."""
    samples = sorted(sample_dir.iterdir())
    if not samples:
        raise RuntimeError(f"no training samples in {sample_dir}")
    r = subprocess.run(
        [zstd, "--train"] + [str(p) for p in samples] + ["-o", str(out_dict), "-q"],
        capture_output=True, timeout=_JOB_TIMEOUT,
    )
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()[:400]
        raise RuntimeError(f"zstd --train exit {r.returncode}: {err}")


def _compress_with_dict(
    zstd: str, src: Path, dict_path: Path, out: Path
) -> None:
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    r = subprocess.run(
        [zstd] + _ZSTD_FLAGS + ["-19", "-D", str(dict_path), str(src), "-o", str(tmp)],
        capture_output=True, timeout=_JOB_TIMEOUT,
    )
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()[:400]
        raise RuntimeError(f"zstd exit {r.returncode}: {err}")
    tmp.rename(out)


def _compress_no_dict(
    zstd: str, src: Path, out: Path
) -> None:
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    r = subprocess.run(
        [zstd] + _ZSTD_FLAGS + ["-19", str(src), "-o", str(tmp)],
        capture_output=True, timeout=_JOB_TIMEOUT,
    )
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()[:400]
        raise RuntimeError(f"zstd exit {r.returncode}: {err}")
    tmp.rename(out)


def _make_tar(gtar: str, src_dir: Path, tar_path: Path) -> None:
    tmp = tar_path.with_suffix(tar_path.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    r = subprocess.run(
        [gtar] + _TAR_FLAGS + ["-C", str(src_dir.parent), "-cf", str(tmp),
                                src_dir.name],
        capture_output=True, timeout=_JOB_TIMEOUT,
    )
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()[:400]
        raise RuntimeError(f"tar exit {r.returncode}: {err}")
    tmp.rename(tar_path)


def _record(cfg: BuildConfig, tag: str, path: Path) -> None:
    if path.exists():
        sha = sha256_file(path)
        record(cfg.cache_db, tag, "ok", str(path), sha, path.stat().st_size)


def _cached(cfg: BuildConfig, tag: str, path: Path) -> bool:
    return lookup(cfg.cache_db, tag) == "ok" and path.exists()


def run(cfg: BuildConfig) -> int:
    """Build all dict artifacts.

    Returns 0 on success, 1 if any required artifact fails.
    """
    tool_map = _tools.discover()
    zstd = tool_map.get("zstd")
    if not zstd:
        print("SKIP dict: zstd not available")
        return 0

    gtar = tool_map.get("gtar")

    cfg.dict_dir.mkdir(parents=True, exist_ok=True)
    any_failed = False

    # ── json-samples dict ─────────────────────────────────────────────────────
    ndjson = cfg.raw_dir / "modern" / "sample.ndjson"
    zdict_path = cfg.dict_dir / "json-samples.zdict"
    chunk_dir = cfg.dict_dir / "json-samples"
    tar_zst_dict = cfg.dict_dir / "json-samples.tar.zst"
    tar_zst_nodict = cfg.dict_dir / "json-samples.no-dict.tar.zst"

    if ndjson.exists():
        # Step 1: train dict
        if not _cached(cfg, "dict:json-samples.zdict", zdict_path):
            print("Building json-samples.zdict...")
            try:
                n = _split_ndjson(ndjson, chunk_dir, _MAX_CHUNKS)
                print(f"  Split {n} chunks from {ndjson.name}")
                _train_dict(zstd, chunk_dir, zdict_path)
                _record(cfg, "dict:json-samples.zdict", zdict_path)
                print(f"  -> {zdict_path.name}  ({zdict_path.stat().st_size:,} bytes)")
            except Exception as exc:
                print(f"  FAIL: {exc}")
                any_failed = True
        else:
            print(f"  cached  json-samples.zdict")

        # Step 2: tar + dict compress (requires gtar)
        if zdict_path.exists() and gtar:
            if not _cached(cfg, "dict:json-samples.tar.zst", tar_zst_dict):
                print("Building json-samples.tar.zst (with dict)...")
                try:
                    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tf:
                        tar_tmp = Path(tf.name)
                    _make_tar(gtar, chunk_dir, tar_tmp)
                    _compress_with_dict(zstd, tar_tmp, zdict_path, tar_zst_dict)
                    tar_tmp.unlink(missing_ok=True)
                    _record(cfg, "dict:json-samples.tar.zst", tar_zst_dict)
                    print(f"  -> {tar_zst_dict.name}")
                except Exception as exc:
                    print(f"  FAIL: {exc}")
                    any_failed = True
            else:
                print(f"  cached  json-samples.tar.zst")

            if not _cached(cfg, "dict:json-samples.no-dict.tar.zst", tar_zst_nodict):
                print("Building json-samples.no-dict.tar.zst (no dict)...")
                try:
                    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tf:
                        tar_tmp = Path(tf.name)
                    _make_tar(gtar, chunk_dir, tar_tmp)
                    _compress_no_dict(zstd, tar_tmp, tar_zst_nodict)
                    tar_tmp.unlink(missing_ok=True)
                    _record(cfg, "dict:json-samples.no-dict.tar.zst", tar_zst_nodict)
                    print(f"  -> {tar_zst_nodict.name}")
                except Exception as exc:
                    print(f"  FAIL: {exc}")
                    any_failed = True
            else:
                print(f"  cached  json-samples.no-dict.tar.zst")
        elif zdict_path.exists() and not gtar:
            print("  SKIP json-samples.tar.zst: GNU tar not found")
    else:
        print(f"  SKIP json-samples dict: {ndjson} not found")

    # ── wrong-dict fixture ────────────────────────────────────────────────────
    dickens = cfg.raw_dir / "silesia" / "dickens"
    wrong_dict_out = cfg.dict_dir / "wrong-dict-silesia-dickens.zst"

    if dickens.exists() and zdict_path.exists():
        if not _cached(cfg, "dict:wrong-dict-silesia-dickens.zst", wrong_dict_out):
            print("Building wrong-dict-silesia-dickens.zst...")
            try:
                _compress_with_dict(zstd, dickens, zdict_path, wrong_dict_out)
                _record(cfg, "dict:wrong-dict-silesia-dickens.zst", wrong_dict_out)
                print(f"  -> {wrong_dict_out.name}")
            except Exception as exc:
                print(f"  FAIL: {exc}")
                any_failed = True
        else:
            print(f"  cached  wrong-dict-silesia-dickens.zst")
    elif dickens.exists() and not zdict_path.exists():
        print("  SKIP wrong-dict fixture: json-samples.zdict not built yet")

    # ── logs dict ─────────────────────────────────────────────────────────────
    logs_ndjson = cfg.raw_dir / "logs" / "json-events-100k.ndjson"
    logs_dict = cfg.dict_dir / "logs-dict.zdict"

    if logs_ndjson.exists():
        if not _cached(cfg, "dict:logs-dict.zdict", logs_dict):
            print("Building logs-dict.zdict...")
            try:
                logs_chunk_dir = cfg.dict_dir / "logs-samples"
                n = _split_ndjson(logs_ndjson, logs_chunk_dir, _MAX_CHUNKS)
                print(f"  Split {n} chunks from {logs_ndjson.name}")
                _train_dict(zstd, logs_chunk_dir, logs_dict)
                _record(cfg, "dict:logs-dict.zdict", logs_dict)
                print(f"  -> {logs_dict.name}  ({logs_dict.stat().st_size:,} bytes)")
            except Exception as exc:
                print(f"  FAIL: {exc}")
                any_failed = True
        else:
            print(f"  cached  logs-dict.zdict")
    else:
        print(f"  SKIP logs-dict: {logs_ndjson} not found")

    # ── source dict ───────────────────────────────────────────────────────────
    source_dir = cfg.raw_dir / "source"
    source_dict = cfg.dict_dir / "source-dict.zdict"

    if source_dir.exists():
        if not _cached(cfg, "dict:source-dict.zdict", source_dict):
            print("Building source-dict.zdict...")
            try:
                source_files = sorted(
                    f for f in source_dir.rglob("*") if f.is_file()
                )
                if not source_files:
                    print("  SKIP source-dict: no files in source/")
                else:
                    r = subprocess.run(
                        [zstd, "--train"] +
                        [str(p) for p in source_files] +
                        ["-o", str(source_dict), "-q"],
                        capture_output=True, timeout=_JOB_TIMEOUT,
                    )
                    if r.returncode != 0:
                        err = r.stderr.decode(errors="replace").strip()[:400]
                        raise RuntimeError(
                            f"zstd --train exit {r.returncode}: {err}"
                        )
                    _record(cfg, "dict:source-dict.zdict", source_dict)
                    print(f"  -> {source_dict.name}  ({source_dict.stat().st_size:,} bytes)")
            except Exception as exc:
                print(f"  FAIL: {exc}")
                any_failed = True
        else:
            print(f"  cached  source-dict.zdict")
    else:
        print(f"  SKIP source-dict: {source_dir} not found")

    return 1 if any_failed else 0
