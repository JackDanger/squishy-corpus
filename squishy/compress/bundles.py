"""Build bundle archives into build/bundles/.

Public interface: ``run(cfg: BuildConfig) -> int``

A bundle is a multi-file archive built from the files in one set under
raw_dir.  For each set the following bundle families are produced (where the
required tool is present):

  tar + codec      alpha and random orderings
  7z (solid)       alpha and size-desc orderings, four codecs
  squashfs         alpha and size-desc orderings, four codecs
  zip bundles      alpha ordering, store/deflate/bzip2/lzma codecs
  containers       alpha ordering, cpio/pax/ar
  concat           alpha ordering, gz/xz/zst
  concat-skipframes alpha ordering (zstd skippable frames between members)
  mixed-member     silesia set only (gzip + zstd-skippable + gzip)

Sets are discovered dynamically from cfg.raw_dir.

Ordering semantics
------------------
- alpha:     sort by filename (deterministic, locale-independent via bytes)
- random:    deterministic shuffle keyed on sha256(filename)
- size-desc: largest file first (best for solid-archive compression ratio)

Each bundle job is content-addressed: the cache key covers the ordered
(path, sha256) pairs for all inputs, the format+codec flags, and all tool
versions.  Upgrading a tool or changing an input automatically invalidates
exactly the affected bundles.

All subprocess work is done inline rather than delegating to shell scripts,
so the package has no external script dependencies.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import platform
import signal
import struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from squishy.core.cache import lookup, record
from squishy.core.config import BuildConfig
from squishy.core.fs import sha256_file
from squishy.core import tools as _tools

JOB_TIMEOUT = 3600          # 1 hour
LARGE_BUNDLE = 200 << 20    # 200 MiB raw → use fast codec levels

TAR_CODECS = ["gz", "bz2", "xz", "zst", "lz4", "br", "lzma"]
SQUASHFS_CODECS = ["gzip", "xz", "lz4", "zstd"]
SEVENZ_CODECS = ["lzma2", "ppmd", "bzip2", "deflate"]
ZIP_INTERNALS = ["store", "deflate", "bzip2", "lzma"]
CONCAT_CODECS = ["gz", "xz", "zst"]
CONTAINER_KINDS = ["cpio", "pax", "ar"]

ORDERINGS = ["alpha", "random"]
SOLID_ORDERINGS = ["alpha", "size-desc"]

TAR_FLAGS = [
    "--mtime=@0", "--owner=0", "--group=0",
    "--numeric-owner", "--format=ustar", "--no-recursion",
]


# ---------------------------------------------------------------------------
# Ordering helpers
# ---------------------------------------------------------------------------

def _ordered_files(set_dir: Path, ordering: str) -> list[Path]:
    files = [f for f in set_dir.iterdir() if f.is_file()]
    if ordering == "alpha":
        return sorted(files, key=lambda p: p.name)
    if ordering == "random":
        return sorted(files,
                      key=lambda p: hashlib.sha256(p.name.encode()).hexdigest())
    if ordering == "size-desc":
        return sorted(files, key=lambda p: -p.stat().st_size)
    raise ValueError(f"unknown ordering: {ordering}")


def _input_sigs(
    ordered: list[Path], raw_dir: Path, hashes: dict[Path, str]
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (f.relative_to(raw_dir).as_posix(), hashes[f]) for f in ordered
    )


def _tools_sig(tool_map: dict[str, str], tool_vers: dict[str, str]) -> str:
    return json.dumps(
        {k: tool_vers.get(k, "?") for k in sorted(tool_map)},
        separators=(",", ":"),
    )


def _bundle_key(
    sigs: tuple[tuple[str, str], ...],
    tag: str,
    flags: str,
    tsig: str,
) -> str:
    payload = json.dumps(
        {"v": 1, "tag": tag, "flags": flags, "tools": tsig,
         "inputs": list(sigs)},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _BundleJob:
    key: str
    tag: str
    out: Path
    set_name: str
    kind: str      # "tar_codec" | "sevenz" | "squashfs" | "zip_bundle" |
                   # "container" | "concat" | "skipframes" | "mixed"
    codec: str
    ordering: str
    inputs: tuple[tuple[str, str], ...]
    large: bool
    estimated_bytes: int


# ---------------------------------------------------------------------------
# Bundle builders
# ---------------------------------------------------------------------------

def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = "0"
    env["TZ"] = "UTC"
    env["LC_ALL"] = "C"
    return env


def _run_pipe(cmd1: list[str], cmd2: list[str], tmp: Path,
              capture2: bool, env: dict[str, str]) -> None:
    p1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env)
    try:
        if capture2:
            with open(tmp, "wb") as f:
                p2 = subprocess.Popen(cmd2, stdin=p1.stdout, stdout=f,
                                      stderr=subprocess.PIPE, env=env)
        else:
            p2 = subprocess.Popen(cmd2, stdin=p1.stdout,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, env=env)
        assert p1.stdout is not None
        p1.stdout.close()
        try:
            p2.wait(timeout=JOB_TIMEOUT)
        except subprocess.TimeoutExpired:
            p1.kill()
            p2.kill()
            raise
        p1.wait(timeout=60)
    except BaseException:
        try:
            p1.kill()
        except Exception:
            pass
        raise

    if p1.returncode != 0:
        err = (p1.stderr.read() if p1.stderr else b"")[:200].decode(errors="replace")
        raise RuntimeError(f"tar exit {p1.returncode}: {err}")
    if p2.returncode != 0:
        err = (p2.stderr.read() if p2.stderr else b"")[:200].decode(errors="replace")
        raise RuntimeError(f"codec exit {p2.returncode}: {err}")


def _tar_cmd(gtar: str, raw_dir: Path, set_name: str,
             ordered: list[Path]) -> list[str]:
    set_dir = raw_dir / set_name
    base = [gtar] + TAR_FLAGS + ["-C", str(raw_dir), "-cf", "-"]
    return base + [p.relative_to(raw_dir).as_posix() for p in ordered]


def _build_tar_codec(
    job: _BundleJob, raw_dir: Path, tool_map: dict[str, str],
    env: dict[str, str],
) -> None:
    ordered = [raw_dir / Path(rel) for rel, _ in job.inputs]
    tar = _tar_cmd(tool_map["gtar"], raw_dir, job.set_name, ordered)
    tmp = job.out.parent / (job.out.name + ".bundle.tmp")
    tmp.unlink(missing_ok=True)

    L, c, t = job.large, job.codec, tool_map
    if c == "gz":
        _run_pipe(tar, [t["gzip"], "-n", "-1" if L else "-9", "-c"],
                  tmp, True, env)
    elif c == "bz2":
        _run_pipe(tar, [t["bzip2"], "-1" if L else "-9", "-c"],
                  tmp, True, env)
    elif c == "xz":
        _run_pipe(tar, [t["xz"], "-T1", "-1" if L else "-9e", "-c"],
                  tmp, True, env)
    elif c == "zst":
        lvl = "-3" if L else "-19"
        _run_pipe(tar, [t["zstd"], "-T1", lvl, "-q", "-f", "--no-progress",
                        "-", "-o", str(tmp)], tmp, False, env)
    elif c == "lz4":
        _run_pipe(tar, [t["lz4"], "-1" if L else "-9", "-q", "-c"],
                  tmp, True, env)
    elif c == "br":
        _run_pipe(tar, [t["brotli"], "-q", "1", "-c"],
                  tmp, True, env)
    elif c == "lzma":
        _run_pipe(tar, [t["lzma"], "-1" if L else "-9", "-c"],
                  tmp, True, env)
    else:
        raise ValueError(f"unknown tar codec: {c}")

    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError("empty output")
    tmp.rename(job.out)


def _build_sevenz(
    job: _BundleJob, raw_dir: Path, tool_map: dict[str, str],
    env: dict[str, str],
) -> None:
    """Build a solid 7z archive.

    Total size is computed from the job's inputs; no need to hit the
    filesystem for the threshold check here since BundleJob.large covers it.
    """
    p7z = tool_map["7z"]
    set_dir = raw_dir / job.set_name
    parent = str(set_dir.parent)

    ordered = [raw_dir / Path(rel) for rel, _ in job.inputs]
    tmp = job.out.parent / (job.out.name + ".tmp.7z")
    tmp.unlink(missing_ok=True)

    L = job.large
    c = job.codec
    if c == "lzma2":
        method = f"-m0=lzma2 -mx={'1' if L else '9'} -ms=on -md={'32m' if L else '128m'}"
    elif c == "ppmd":
        method = f"-m0=ppmd -mx={'1' if L else '9'} -ms=on"
    elif c == "bzip2":
        method = f"-m0=bzip2 -mx={'1' if L else '9'} -ms=on"
    elif c == "deflate":
        method = f"-m0=deflate -mx={'1' if L else '9'} -ms=on"
    else:
        raise ValueError(f"unknown 7z codec: {c}")

    method_args = method.split()

    # Build explicit file list in the chosen order
    names = [p.relative_to(set_dir.parent).as_posix() for p in ordered]
    cmd = ([p7z, "a", "-mtm=off", "-mtc=off", "-mta=off",
             "-bd", "-bb0", "-y"] + method_args + [str(tmp)] + names)

    r = subprocess.run(cmd, cwd=parent, env=env, capture_output=True,
                       timeout=JOB_TIMEOUT)
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()[:400]
        raise RuntimeError(f"7z exit {r.returncode}: {err}")
    tmp.rename(job.out)


def _build_squashfs(
    job: _BundleJob, raw_dir: Path, tool_map: dict[str, str],
    env: dict[str, str],
) -> None:
    set_dir = raw_dir / job.set_name
    ordered = [raw_dir / Path(rel) for rel, _ in job.inputs]
    tmp = job.out.parent / (job.out.name + ".tmp")
    tmp.unlink(missing_ok=True)

    # Build sort file: mksquashfs sort priority (higher = placed first)
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sort", delete=False) as sf:
        sortfile = sf.name
        prio = 32767
        for p in ordered:
            rel = p.relative_to(set_dir).as_posix()
            sf.write(f"{rel} {prio}\n")
            prio -= 1

    try:
        base_cmd = [
            "mksquashfs", str(set_dir), str(tmp),
            "-comp", job.codec,
            "-no-exports", "-no-recovery", "-no-progress",
            "-sort", sortfile,
            "-noappend", "-quiet",
        ]
        r = subprocess.run(base_cmd, env=env, capture_output=True,
                           timeout=JOB_TIMEOUT)
        if r.returncode != 0:
            # Retry without -no-progress / -quiet (older mksquashfs versions)
            retry_cmd = [
                "mksquashfs", str(set_dir), str(tmp),
                "-comp", job.codec,
                "-no-exports", "-no-recovery",
                "-sort", sortfile, "-noappend",
            ]
            r2 = subprocess.run(retry_cmd, env=env, capture_output=True,
                                timeout=JOB_TIMEOUT)
            if r2.returncode != 0:
                err = r2.stderr.decode(errors="replace").strip()[:400]
                raise RuntimeError(f"mksquashfs exit {r2.returncode}: {err}")
    finally:
        try:
            os.unlink(sortfile)
        except OSError:
            pass

    tmp.rename(job.out)


def _build_zip_bundle(
    job: _BundleJob, raw_dir: Path, tool_map: dict[str, str],
    env: dict[str, str],
) -> None:
    set_dir = raw_dir / job.set_name
    parent = str(set_dir.parent)
    base = set_dir.name
    tmp = job.out.parent / (job.out.name + ".tmp.zip")
    tmp.unlink(missing_ok=True)

    codec = job.codec
    p7z = tool_map.get("7z", "")

    if codec == "store":
        r = subprocess.run(
            [tool_map["zip"], "-X", "-q", "-0", str(tmp), "-r", base],
            cwd=parent, env=env, capture_output=True, timeout=JOB_TIMEOUT,
        )
    elif codec == "deflate":
        r = subprocess.run(
            [tool_map["zip"], "-X", "-q", "-Z", "deflate", "-9",
             str(tmp), "-r", base],
            cwd=parent, env=env, capture_output=True, timeout=JOB_TIMEOUT,
        )
    elif codec == "bzip2":
        r = subprocess.run(
            [p7z, "a", "-mtm=off", "-mtc=off", "-mta=off",
             "-tzip", "-mm=bzip2", "-mx=9", "-bd", "-bb0", "-y",
             str(tmp), base],
            cwd=parent, env=env, capture_output=True, timeout=JOB_TIMEOUT,
        )
    elif codec == "lzma":
        r = subprocess.run(
            [p7z, "a", "-mtm=off", "-mtc=off", "-mta=off",
             "-tzip", "-mm=LZMA", "-mx=9", "-bd", "-bb0", "-y",
             str(tmp), base],
            cwd=parent, env=env, capture_output=True, timeout=JOB_TIMEOUT,
        )
    else:
        raise ValueError(f"unknown zip internal codec: {codec}")

    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()[:400]
        raise RuntimeError(f"zip exit {r.returncode}: {err}")
    tmp.rename(job.out)


def _build_container(
    job: _BundleJob, raw_dir: Path, tool_map: dict[str, str],
    env: dict[str, str],
) -> None:
    set_dir = raw_dir / job.set_name
    parent = str(set_dir.parent)
    base = set_dir.name
    tmp = job.out.parent / (job.out.name + ".tmp")
    tmp.unlink(missing_ok=True)

    kind = job.codec

    if kind == "cpio":
        find = subprocess.run(
            ["find", base, "-type", "f"],
            cwd=parent, env=env, capture_output=True, text=True,
        )
        file_list = sorted(find.stdout.splitlines())
        sorted_input = "\n".join(file_list) + "\n"
        r = subprocess.run(
            [tool_map["cpio"], "-o", "-H", "newc"],
            input=sorted_input.encode(), cwd=parent,
            stdout=open(tmp, "wb"), stderr=subprocess.PIPE,
            env=env, timeout=JOB_TIMEOUT,
        )
        if r.returncode != 0:
            err = r.stderr.decode(errors="replace").strip()[:400]
            raise RuntimeError(f"cpio exit {r.returncode}: {err}")

    elif kind == "pax":
        cmd = [
            tool_map["gtar"],
            "--sort=name", "--mtime=@0", "--owner=0", "--group=0",
            "--numeric-owner", "--format=pax",
            "-C", parent, "-cf", str(tmp), base,
        ]
        r = subprocess.run(cmd, env=env, capture_output=True,
                           timeout=JOB_TIMEOUT)
        if r.returncode != 0:
            err = r.stderr.decode(errors="replace").strip()[:400]
            raise RuntimeError(f"pax exit {r.returncode}: {err}")

    elif kind == "ar":
        find = subprocess.run(
            ["find", base, "-type", "f"],
            cwd=parent, env=env, capture_output=True, text=True,
        )
        file_list = sorted(find.stdout.splitlines())
        r = subprocess.run(
            [tool_map["ar"], "-rcD", str(tmp)] + file_list,
            cwd=parent, env=env, capture_output=True, timeout=JOB_TIMEOUT,
        )
        if r.returncode != 0:
            # ar fallback: drop the D (deterministic) flag for older versions
            r2 = subprocess.run(
                [tool_map["ar"], "-rc", str(tmp)] + file_list,
                cwd=parent, env=env, capture_output=True, timeout=JOB_TIMEOUT,
            )
            if r2.returncode != 0:
                err = r2.stderr.decode(errors="replace").strip()[:400]
                raise RuntimeError(f"ar exit {r2.returncode}: {err}")
    else:
        raise ValueError(f"unknown container kind: {kind}")

    tmp.rename(job.out)


def _build_concat(
    job: _BundleJob, raw_dir: Path, tool_map: dict[str, str],
    env: dict[str, str],
) -> None:
    set_dir = raw_dir / job.set_name
    ordered = sorted(
        (f for f in set_dir.iterdir() if f.is_file()),
        key=lambda p: p.name,
    )
    tmp = job.out.parent / (job.out.name + ".tmp")
    tmp.unlink(missing_ok=True)

    codec = job.codec
    if codec == "gz":
        compress_cmd = [tool_map["gzip"], "-n", "-9", "-c"]
    elif codec == "xz":
        compress_cmd = [tool_map["xz"], "-T1", "-9e", "-c"]
    elif codec == "zst":
        compress_cmd = [tool_map["zstd"], "-T1", "-19", "-q", "-f",
                        "--no-progress", "-c"]
    else:
        raise ValueError(f"unknown concat codec: {codec}")

    with open(tmp, "wb") as out_f:
        for fp in ordered:
            r = subprocess.run(
                compress_cmd + [str(fp)],
                stdout=out_f, stderr=subprocess.PIPE,
                env=env, timeout=JOB_TIMEOUT,
            )
            if r.returncode != 0:
                err = r.stderr.decode(errors="replace").strip()[:400]
                raise RuntimeError(
                    f"concat {codec} failed on {fp.name}: {err}"
                )

    tmp.rename(job.out)


_SKIPPABLE_MAGIC = 0x184D2A50


def _build_skipframes(
    job: _BundleJob, raw_dir: Path, tool_map: dict[str, str],
    env: dict[str, str],
) -> None:
    set_dir = raw_dir / job.set_name
    files = sorted(f for f in set_dir.rglob("*") if f.is_file())
    tmp = job.out.parent / (job.out.name + ".tmp")
    tmp.unlink(missing_ok=True)

    with open(tmp, "wb") as out_f:
        for fp in files:
            meta = json.dumps({
                "filename": fp.relative_to(set_dir.parent).as_posix(),
                "size": fp.stat().st_size,
            }, sort_keys=True).encode()
            pad = (-len(meta)) & 3
            payload = meta + b"\x00" * pad
            out_f.write(struct.pack("<II", _SKIPPABLE_MAGIC, len(payload)))
            out_f.write(payload)

            r = subprocess.run(
                [tool_map["zstd"], "-T1", "-19", "-q", "-f",
                 "--no-progress", "-c", str(fp)],
                stdout=out_f, stderr=subprocess.PIPE,
                env=env, timeout=JOB_TIMEOUT,
            )
            if r.returncode != 0:
                err = r.stderr.decode(errors="replace").strip()[:400]
                raise RuntimeError(f"zstd skipframes failed: {err}")

    tmp.rename(job.out)


def _build_mixed(
    job: _BundleJob, raw_dir: Path, tool_map: dict[str, str],
    env: dict[str, str],
) -> None:
    set_dir = raw_dir / job.set_name
    files = sorted(f for f in set_dir.iterdir() if f.is_file())[:3]
    if len(files) < 3:
        raise RuntimeError(
            f"mixed-member requires at least 3 files in {set_dir}; "
            f"found {len(files)}"
        )
    tmp = job.out.parent / (job.out.name + ".tmp")
    tmp.unlink(missing_ok=True)

    def _gz(payload: bytes) -> bytes:
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0,
                           compresslevel=9) as g:
            g.write(payload)
        return buf.getvalue()

    def _skippable(meta: dict) -> bytes:
        encoded = json.dumps(meta, sort_keys=True).encode()
        pad = (-len(encoded)) & 3
        encoded += b"\x00" * pad
        return struct.pack("<II", _SKIPPABLE_MAGIC, len(encoded)) + encoded

    with open(tmp, "wb") as f:
        f.write(_gz(files[0].read_bytes()))
        f.write(_skippable({"note": "mixed-member-stream",
                             "between": [files[0].name, files[1].name]}))
        f.write(_gz(files[1].read_bytes()))
        f.write(_skippable({"note": "end-marker"}))
        f.write(_gz(files[2].read_bytes()))

    tmp.rename(job.out)


# ---------------------------------------------------------------------------
# Job dispatch
# ---------------------------------------------------------------------------

def _run_one(
    job: _BundleJob,
    raw_dir: Path,
    tool_map: dict[str, str],
    env: dict[str, str],
) -> tuple[str, str, float]:
    t0 = time.monotonic()
    try:
        job.out.parent.mkdir(parents=True, exist_ok=True)
        dispatch = {
            "tar_codec":  lambda: _build_tar_codec(job, raw_dir, tool_map, env),
            "sevenz":     lambda: _build_sevenz(job, raw_dir, tool_map, env),
            "squashfs":   lambda: _build_squashfs(job, raw_dir, tool_map, env),
            "zip_bundle": lambda: _build_zip_bundle(job, raw_dir, tool_map, env),
            "container":  lambda: _build_container(job, raw_dir, tool_map, env),
            "concat":     lambda: _build_concat(job, raw_dir, tool_map, env),
            "skipframes": lambda: _build_skipframes(job, raw_dir, tool_map, env),
            "mixed":      lambda: _build_mixed(job, raw_dir, tool_map, env),
        }
        dispatch[job.kind]()
        elapsed = time.monotonic() - t0
        if not job.out.exists() or job.out.stat().st_size == 0:
            return ("fail", "output not created or empty", elapsed)
        return ("ok", "", elapsed)
    except subprocess.TimeoutExpired:
        return ("fail", f"timeout after {JOB_TIMEOUT}s", time.monotonic() - t0)
    except Exception as exc:
        return ("fail", str(exc)[:400], time.monotonic() - t0)


# ---------------------------------------------------------------------------
# Job planning
# ---------------------------------------------------------------------------

def _plan_set(
    set_name: str,
    raw_dir: Path,
    bundles_dir: Path,
    hashes: dict[Path, str],
    tool_map: dict[str, str],
    tool_vers: dict[str, str],
) -> list[_BundleJob]:
    set_dir = raw_dir / set_name
    if not set_dir.exists():
        return []

    all_files = sorted(f for f in set_dir.iterdir() if f.is_file())
    total_raw = sum(f.stat().st_size for f in all_files)
    large = total_raw > LARGE_BUNDLE
    tsig = _tools_sig(tool_map, tool_vers)
    out_dir = bundles_dir / set_name
    jobs: list[_BundleJob] = []

    def _sigs(ordered: list[Path]) -> tuple[tuple[str, str], ...]:
        return _input_sigs(ordered, raw_dir, hashes)

    # ── tar + codec ──────────────────────────────────────────────────────────
    codec_to_tool = {
        "gz": "gzip", "bz2": "bzip2", "xz": "xz", "zst": "zstd",
        "lz4": "lz4", "br": "brotli", "lzma": "lzma",
    }
    if tool_map.get("gtar"):
        for codec in TAR_CODECS:
            tool_key = codec_to_tool.get(codec, codec)
            if not tool_map.get(tool_key):
                continue
            for ordering in ORDERINGS:
                ordered = _ordered_files(set_dir, ordering)
                sigs = _sigs(ordered)
                lvl = "1" if large else ("19" if codec == "zst" else "9")
                flags = f"tar|{codec}|level={lvl}"
                key = _bundle_key(sigs, f"tar.{codec}.{ordering}", flags, tsig)
                jobs.append(_BundleJob(
                    key=key, tag=f"tar.{codec}.{ordering}",
                    out=out_dir / f"{set_name}.{ordering}.tar.{codec}",
                    set_name=set_name, kind="tar_codec", codec=codec,
                    ordering=ordering, inputs=sigs, large=large,
                    estimated_bytes=total_raw,
                ))

    # ── 7z (solid) ───────────────────────────────────────────────────────────
    if tool_map.get("7z"):
        for codec in SEVENZ_CODECS:
            for ordering in SOLID_ORDERINGS:
                ordered = _ordered_files(set_dir, ordering)
                sigs = _sigs(ordered)
                flags = f"7z|{codec}|solid"
                key = _bundle_key(sigs, f"7z.{codec}.{ordering}", flags, tsig)
                jobs.append(_BundleJob(
                    key=key, tag=f"7z.{codec}.{ordering}",
                    out=out_dir / f"{set_name}.{ordering}.7z.{codec}",
                    set_name=set_name, kind="sevenz", codec=codec,
                    ordering=ordering, inputs=sigs, large=large,
                    estimated_bytes=total_raw // 2,
                ))

    # ── squashfs ─────────────────────────────────────────────────────────────
    if tool_map.get("mksquashfs"):
        for codec in SQUASHFS_CODECS:
            for ordering in SOLID_ORDERINGS:
                ordered = _ordered_files(set_dir, ordering)
                sigs = _sigs(ordered)
                flags = f"squashfs|{codec}"
                key = _bundle_key(sigs, f"squashfs.{codec}.{ordering}", flags, tsig)
                jobs.append(_BundleJob(
                    key=key, tag=f"squashfs.{codec}.{ordering}",
                    out=out_dir / f"{set_name}.{ordering}.squashfs.{codec}",
                    set_name=set_name, kind="squashfs", codec=codec,
                    ordering=ordering, inputs=sigs, large=large,
                    estimated_bytes=total_raw // 2,
                ))

    # ── zip bundles ──────────────────────────────────────────────────────────
    has_zip = bool(tool_map.get("zip"))
    has_7z = bool(tool_map.get("7z"))
    if has_zip or has_7z:
        ordered = _ordered_files(set_dir, "alpha")
        sigs = _sigs(ordered)
        for codec in ZIP_INTERNALS:
            if codec in ("bzip2", "lzma") and not has_7z:
                continue
            if codec in ("store", "deflate") and not has_zip:
                continue
            flags = f"zip|{codec}"
            key = _bundle_key(sigs, f"zip.{codec}.alpha", flags, tsig)
            jobs.append(_BundleJob(
                key=key, tag=f"zip.{codec}.alpha",
                out=out_dir / f"{set_name}.alpha.zip.{codec}",
                set_name=set_name, kind="zip_bundle", codec=codec,
                ordering="alpha", inputs=sigs, large=large,
                estimated_bytes=total_raw // 2,
            ))

    # ── plain containers ──────────────────────────────────────────────────────
    kind_tool = {"cpio": "cpio", "pax": "gtar", "ar": "ar"}
    ordered = _ordered_files(set_dir, "alpha")
    sigs = _sigs(ordered)
    for kind in CONTAINER_KINDS:
        if not tool_map.get(kind_tool[kind]):
            continue
        flags = f"container|{kind}"
        key = _bundle_key(sigs, f"container.{kind}.alpha", flags, tsig)
        jobs.append(_BundleJob(
            key=key, tag=f"container.{kind}.alpha",
            out=out_dir / f"{set_name}.alpha.{kind}",
            set_name=set_name, kind="container", codec=kind,
            ordering="alpha", inputs=sigs, large=large,
            estimated_bytes=total_raw,
        ))

    # ── multi-frame concat ────────────────────────────────────────────────────
    codec_tool_map = {"gz": "gzip", "xz": "xz", "zst": "zstd"}
    ordered = _ordered_files(set_dir, "alpha")
    sigs = _sigs(ordered)
    for codec in CONCAT_CODECS:
        if not tool_map.get(codec_tool_map[codec]):
            continue
        flags = f"concat|{codec}"
        key = _bundle_key(sigs, f"concat.{codec}.alpha", flags, tsig)
        jobs.append(_BundleJob(
            key=key, tag=f"concat.{codec}.alpha",
            out=out_dir / f"{set_name}.alpha.concat-{codec}",
            set_name=set_name, kind="concat", codec=codec,
            ordering="alpha", inputs=sigs, large=large,
            estimated_bytes=total_raw // 2,
        ))

    # ── zstd skipframes ───────────────────────────────────────────────────────
    if tool_map.get("zstd"):
        flags = "concat|zst-skipframes"
        key = _bundle_key(sigs, "concat.zst-skipframes.alpha", flags, tsig)
        jobs.append(_BundleJob(
            key=key, tag="concat.zst-skipframes.alpha",
            out=out_dir / f"{set_name}.alpha.concat-zst-skipframes",
            set_name=set_name, kind="skipframes", codec="zst",
            ordering="alpha", inputs=sigs, large=large,
            estimated_bytes=total_raw // 2,
        ))

    # ── mixed-member (silesia only) ───────────────────────────────────────────
    if set_name == "silesia" and tool_map.get("gzip") and tool_map.get("zstd"):
        mixed_out = bundles_dir / "mixed-member" / "silesia-mixed.bin"
        flags = "mixed-member"
        key = _bundle_key(sigs, "mixed-member", flags, tsig)
        jobs.append(_BundleJob(
            key=key, tag="mixed-member",
            out=mixed_out, set_name=set_name, kind="mixed", codec="",
            ordering="alpha", inputs=sigs, large=False,
            estimated_bytes=total_raw // 10,
        ))

    return jobs


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(cfg: BuildConfig) -> int:
    """Build all bundle artifacts.

    Returns 0 on success, 1 if any job failed.
    """
    if not cfg.raw_dir.exists():
        print(f"ERROR: {cfg.raw_dir} not found — run raw stage first")
        return 1

    tool_map = _tools.discover()
    if not tool_map.get("gtar"):
        print("ERROR: GNU tar not found — install gnu-tar (brew install gnu-tar)")
        return 1

    tool_vers: dict[str, str] = {k: _tools.version(v) for k, v in tool_map.items()}
    print(f"Tools: {', '.join(sorted(tool_map))}")

    # Discover all sets from raw_dir
    set_names = sorted(
        d.name for d in cfg.raw_dir.iterdir() if d.is_dir()
    )

    all_inputs: list[Path] = []
    for set_name in set_names:
        sd = cfg.raw_dir / set_name
        all_inputs += [f for f in sd.iterdir() if f.is_file()]

    print(f"Hashing {len(all_inputs)} input files...", end=" ", flush=True)
    with ThreadPoolExecutor(max_workers=min(4, cfg.workers)) as ex:
        hashes: dict[Path, str] = dict(
            zip(all_inputs, ex.map(sha256_file, all_inputs))
        )
    print("done")

    all_jobs: list[_BundleJob] = []
    for set_name in set_names:
        all_jobs += _plan_set(
            set_name, cfg.raw_dir, cfg.bundles_dir,
            hashes, tool_map, tool_vers,
        )

    all_jobs.sort(key=lambda j: -j.estimated_bytes)
    print(f"Planned {len(all_jobs)} bundles across {len(set_names)} set(s)")

    pending: list[_BundleJob] = []
    n_cached = n_adopted = 0
    adopt_list: list[_BundleJob] = []

    for job in all_jobs:
        if lookup(cfg.cache_db, job.key) == "ok" and job.out.exists():
            n_cached += 1
        elif job.out.exists() and job.out.stat().st_size > 0:
            adopt_list.append(job)
            n_adopted += 1
        else:
            pending.append(job)

    for job in adopt_list:
        sha = sha256_file(job.out)
        record(cfg.cache_db, job.key, "ok", str(job.out),
               sha, job.out.stat().st_size)

    print(f"  {n_cached} cached, {n_adopted} adopted, {len(pending)} pending")

    if not pending:
        print("All up to date.")
        return 0

    env = _build_env()
    n_ok = n_fail = 0
    failures: list[tuple[str, str]] = []
    done_count = 0
    total = len(pending)
    shutdown = threading.Event()

    def _save(job: _BundleJob, status: str) -> None:
        if status == "ok" and job.out.exists():
            sha = sha256_file(job.out)
            sz = job.out.stat().st_size
        else:
            sha = ""
            sz = 0
        record(cfg.cache_db, job.key, status, str(job.out), sha, sz)

    print(f"Building {total} bundles with {cfg.workers} worker(s)...")

    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        orig = signal.getsignal(signal.SIGINT)

        def _sigint(sig: int, frame: object) -> None:
            print("\nInterrupted — waiting for in-flight jobs...", flush=True)
            shutdown.set()
            signal.signal(signal.SIGINT, orig)  # type: ignore[arg-type]

        signal.signal(signal.SIGINT, _sigint)

        futs = {}
        for job in pending:
            if shutdown.is_set():
                break
            futs[ex.submit(_run_one, job, cfg.raw_dir, tool_map, env)] = job

        for fut in as_completed(futs):
            if shutdown.is_set():
                break
            job = futs[fut]
            status, detail, elapsed = fut.result()
            _save(job, status)
            done_count += 1

            marker = "OK  " if status == "ok" else "FAIL"
            t_str = f"  {elapsed:.0f}s" if elapsed >= 1 else ""
            print(f"  [{done_count:4}/{total}] {marker}  {job.tag:<30}  {job.set_name}{t_str}")

            if status == "ok":
                n_ok += 1
            else:
                n_fail += 1
                rel = job.out.relative_to(cfg.build_dir)
                failures.append((str(rel), detail))

    if shutdown.is_set():
        print("Interrupted — partial results in cache.")
        return 1

    print(f"\nDone: {n_ok} built, {n_adopted} adopted, {n_fail} failed")
    if failures:
        print("Failed:")
        for path, reason in failures:
            print(f"  {path}\n    {reason}")
        return 1

    return 0
