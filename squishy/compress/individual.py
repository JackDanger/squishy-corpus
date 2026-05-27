"""Build per-file compression artifacts into build/individual/.

Public interface: ``run(cfg: BuildConfig) -> int``

Each raw file is compressed with every available codec.  Results are cached
by content-addressed key so re-runs only rebuild what changed.  Sets are
discovered dynamically from the contents of cfg.raw_dir rather than being
hardcoded — any directory present under raw/ is treated as a set.

Leveled codecs (gz, xz, zst, br) are built for every set; this matches the
original script's behaviour, which generated leveled variants for all sets
it processed.

Special cases:
- zpaq: writes directly to the final output path (no stdout capture, no tmp
  rename).  The caller removes the destination before invoking.
- 7z / zip: require cwd=inp.parent.  The command template uses inp.name, not
  the full path.
- zstd: uses -o <tmp> instead of stdout capture.
"""
from __future__ import annotations

import os
import platform
import signal
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
from squishy.compress.codecs import (
    ALL_CODECS,
    CodecSpec,
    available_codecs,
    cmd_flags_sig,
)
from squishy.core import tools as _tools

JOB_TIMEOUT = 1800  # 30 minutes; covers brotli -q11 on large files


@dataclass(frozen=True)
class _Job:
    inp: Path
    out: Path
    tmp: Optional[Path]  # None when direct=True (zpaq)
    codec: CodecSpec
    cmd: tuple[str, ...]
    cwd: Optional[Path]


def _plan_jobs(
    inp: Path,
    set_dir: Path,
    out_dir: Path,
    codecs: list[CodecSpec],
    tool_map: dict[str, str],
) -> list[_Job]:
    jobs: list[_Job] = []
    for cs in codecs:
        out = out_dir / (inp.name + cs.ext)
        resolved_binary = tool_map.get(cs.binary, cs.binary)

        def _resolve(cmd: tuple[str, ...]) -> tuple[str, ...]:
            return (resolved_binary,) + cmd[1:]

        if cs.direct:
            # zpaq: writes to out directly; no tmp
            cmd = _resolve(tuple(cs.cmd_template(inp, out)))
            jobs.append(_Job(inp=inp, out=out, tmp=None, codec=cs,
                             cmd=cmd, cwd=None))
        elif cs.cwd_is_inp_parent:
            # 7z / zip: tmp lives beside out; cwd = inp.parent
            tmp = out.with_suffix(out.suffix + ".tmp")
            cmd = _resolve(tuple(cs.cmd_template(inp, tmp)))
            jobs.append(_Job(inp=inp, out=out, tmp=tmp, codec=cs,
                             cmd=cmd, cwd=inp.parent))
        elif cs.capture:
            # stdout → tmp
            tmp = out.with_suffix(out.suffix + ".tmp")
            cmd = _resolve(tuple(cs.cmd_template(inp, out)))
            jobs.append(_Job(inp=inp, out=out, tmp=tmp, codec=cs,
                             cmd=cmd, cwd=None))
        else:
            # tool writes to tmp directly (-o flag)
            tmp = out.with_suffix(out.suffix + ".tmp")
            cmd = _resolve(tuple(cs.cmd_template(inp, tmp)))
            jobs.append(_Job(inp=inp, out=out, tmp=tmp, codec=cs,
                             cmd=cmd, cwd=None))
    return jobs


def _cache_key(
    inp_hash: str,
    job: _Job,
    binary_path: str,
    binary_ver: str,
    machine: str,
) -> str:
    import hashlib
    flags = cmd_flags_sig(list(job.cmd))
    raw = "\0".join([inp_hash, job.codec.tag, flags,
                     binary_path, binary_ver, machine])
    return hashlib.sha256(raw.encode()).hexdigest()


def _run_one(job: _Job) -> tuple[str, str, float]:
    """Execute a single compression job.

    Returns (status, detail, elapsed_s).
    status is 'ok' or 'fail'.
    """
    t0 = time.monotonic()
    try:
        job.out.parent.mkdir(parents=True, exist_ok=True)

        if job.tmp is None:
            # Direct write (zpaq) — remove destination first
            job.out.unlink(missing_ok=True)
        else:
            job.tmp.unlink(missing_ok=True)

        if job.codec.capture:
            with open(job.tmp, "wb") as f:  # type: ignore[arg-type]
                r = subprocess.run(
                    list(job.cmd),
                    stdout=f, stderr=subprocess.PIPE,
                    cwd=job.cwd, timeout=JOB_TIMEOUT,
                )
        else:
            r = subprocess.run(
                list(job.cmd),
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                cwd=job.cwd, timeout=JOB_TIMEOUT,
            )

        elapsed = time.monotonic() - t0

        if r.returncode != 0:
            if job.tmp:
                job.tmp.unlink(missing_ok=True)
            detail = r.stderr.decode(errors="replace").strip()[:500]
            return ("fail", detail or f"exit {r.returncode}", elapsed)

        check = job.tmp if job.tmp is not None else job.out
        if not check.exists():
            return ("fail", "output not created", elapsed)
        if check.stat().st_size == 0 and job.inp.stat().st_size > 0:
            check.unlink(missing_ok=True)
            return ("fail", "empty output", elapsed)

        if job.tmp is not None:
            job.tmp.rename(job.out)

        return ("ok", "", elapsed)

    except subprocess.TimeoutExpired:
        if job.tmp:
            job.tmp.unlink(missing_ok=True)
        return ("fail", f"timeout after {JOB_TIMEOUT}s", time.monotonic() - t0)
    except Exception as exc:
        if job.tmp:
            job.tmp.unlink(missing_ok=True)
        return ("fail", str(exc)[:400], time.monotonic() - t0)


def run(cfg: BuildConfig) -> int:
    """Build all per-file compression artifacts.

    Returns 0 on full success, 1 if any job failed.
    """
    if not cfg.raw_dir.exists():
        print(f"ERROR: {cfg.raw_dir} not found — run raw stage first")
        return 1

    tool_map = _tools.discover()
    if not tool_map:
        print("ERROR: no compression tools found on PATH")
        return 1

    missing = sorted({"gzip", "bzip2", "xz", "zstd", "lz4"} - set(tool_map))
    print(f"Tools: {', '.join(sorted(tool_map))}"
          + (f"  [missing: {', '.join(missing)}]" if missing else ""))

    tool_vers: dict[str, str] = {k: _tools.version(v) for k, v in tool_map.items()}
    machine = platform.machine()
    codecs = available_codecs(tool_map)

    # Discover input files from all set subdirectories under raw_dir
    all_inputs: list[Path] = []
    for set_dir in sorted(cfg.raw_dir.iterdir()):
        if set_dir.is_dir():
            all_inputs += sorted(f for f in set_dir.iterdir() if f.is_file())

    if not all_inputs:
        print("No input files found in raw_dir")
        return 0

    print(f"Hashing {len(all_inputs)} input files...", end=" ", flush=True)
    with ThreadPoolExecutor(max_workers=min(4, cfg.workers)) as ex:
        hashes: dict[Path, str] = dict(
            zip(all_inputs, ex.map(sha256_file, all_inputs))
        )
    print("done")

    # Plan all jobs
    all_jobs: list[_Job] = []
    for inp in all_inputs:
        set_name = inp.parent.name
        out_dir = cfg.individual_dir / set_name
        all_jobs += _plan_jobs(inp, inp.parent, out_dir, codecs, tool_map)

    print(f"Planned {len(all_jobs)} artifacts across {len(all_inputs)} files")

    # Classify against cache
    pending: list[tuple[_Job, str]] = []
    n_cached = n_adopted = 0
    adopt_pairs: list[tuple[str, _Job]] = []

    for job in all_jobs:
        binary_path = tool_map.get(job.codec.binary, "")
        ver = tool_vers.get(job.codec.binary, "?")
        key = _cache_key(hashes[job.inp], job, binary_path, ver, machine)

        if lookup(cfg.cache_db, key) == "ok" and job.out.exists():
            n_cached += 1
        elif job.out.exists() and job.out.stat().st_size > 0:
            adopt_pairs.append((key, job))
            n_adopted += 1
        else:
            pending.append((job, key))

    for key, job in adopt_pairs:
        sha = sha256_file(job.out)
        record(cfg.cache_db, key, "ok", str(job.out), sha, job.out.stat().st_size)

    print(f"  {n_cached} cached, {n_adopted} adopted, {len(pending)} pending")

    if not pending:
        print("All up to date.")
        return 0

    n_ok = n_fail = 0
    failures: list[tuple[str, str]] = []
    done = 0
    total = len(pending)
    shutdown = threading.Event()

    def _save(key: str, job: _Job, status: str) -> None:
        if status == "ok" and job.out.exists():
            sha = sha256_file(job.out)
            sz = job.out.stat().st_size
        else:
            sha = ""
            sz = 0
        record(cfg.cache_db, key, status, str(job.out), sha, sz)

    print(f"Building {total} artifacts with {cfg.workers} worker(s)...")

    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        orig = signal.getsignal(signal.SIGINT)

        def _sigint(sig: int, frame: object) -> None:
            print("\nInterrupted — waiting for in-flight jobs...", flush=True)
            shutdown.set()
            signal.signal(signal.SIGINT, orig)  # type: ignore[arg-type]

        signal.signal(signal.SIGINT, _sigint)

        futs = {}
        for job, key in pending:
            if shutdown.is_set():
                break
            futs[ex.submit(_run_one, job)] = (job, key)

        for fut in as_completed(futs):
            if shutdown.is_set():
                break
            job, key = futs[fut]
            status, detail, elapsed = fut.result()
            _save(key, job, status)
            done += 1

            marker = "OK  " if status == "ok" else "FAIL"
            t_str = f"  {elapsed:.1f}s" if elapsed >= 0.1 else ""
            rel = job.out.relative_to(cfg.build_dir)
            print(f"  [{done:4}/{total}] {marker}  {job.codec.tag:<18}  {job.inp.name}{t_str}")

            if status == "ok":
                n_ok += 1
            else:
                n_fail += 1
                failures.append((str(rel), detail))

    if shutdown.is_set():
        print("Interrupted — partial results recorded in cache.")
        return 1

    print(f"\nDone: {n_ok} built, {n_cached} cached, {n_fail} failed")
    if failures:
        print("Failed:")
        for path, reason in failures:
            print(f"  {path}\n    {reason}")
        return 1

    return 0
