#!/usr/bin/env python3
"""
Build per-file compression artifacts (build/individual/).

Idempotent, parallel, resumable via SQLite-backed build cache.
Replaces the Make pattern rules for the `individual` target.

  python3 scripts/build-individual.py           # build everything
  python3 scripts/build-individual.py -j 4      # explicit parallelism
  python3 scripts/build-individual.py --dry-run # show pending work
  python3 scripts/build-individual.py --clean   # wipe cache, rebuild all

Exit 0 when all artifacts are built or intentionally skipped (no_tool).
Exit 1 when any artifact fails; the stamp file is not written, so the
next Make invocation re-runs this script to retry the failures.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ─── paths ───────────────────────────────────────────────────────────────────

ROOT  = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
RAW   = BUILD / "raw"
INDIV = BUILD / "individual"
CACHE = BUILD / ".build-cache.db"
STAMP = BUILD / ".individual.stamp"

# ─── constants ───────────────────────────────────────────────────────────────

LARGE       = 64 * 1024 * 1024   # bytes: drop to fast level above this
BR_LARGE    = 16 * 1024 * 1024   # brotli-specific: window size limit
JOB_TIMEOUT = 1800               # seconds per job (30 min covers brotli -q11 on 200 MB)

SETS    = ["silesia", "modern", "pathological", "squash"]
LEVELED = {"silesia", "modern", "squash"}   # squash gets per-level variants too

LEVELS_GZ  = [1, 6, 9]
LEVELS_XZ  = [0, 6, 9]
LEVELS_ZST = [1, 3, 9, 19, 22]
LEVELS_BR  = [1, 6, 11]

MACHINE = platform.machine()  # arm64 / x86_64 — included in cache key

# ─── job model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Job:
    inp:     Path
    out:     Path
    tmp:     Optional[Path]  # None → tool writes directly to out (zpaq)
    tag:     str             # codec label: "xz.l9", "zip.store", …
    cmd:     tuple           # subprocess argv (resolved, not a template)
    cwd:     Optional[Path]  # working dir for 7z / zip
    capture: bool            # True → stdout → tmp; False → cmd writes tmp itself
    skip:    Optional[str]   # "no_tool" | None

# ─── tool discovery ───────────────────────────────────────────────────────────

def find(name: str) -> Optional[str]:
    return shutil.which(name)

def tool_version(binary: str) -> str:
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
        return ((r.stdout or r.stderr).splitlines() or ["?"])[0].strip()[:100]
    except Exception:
        return "?"

def discover() -> dict[str, str]:
    candidates = {
        "gzip":   find("gzip"),
        "bzip2":  find("bzip2"),
        "xz":     find("xz"),
        "zstd":   find("zstd"),
        "lz4":    find("lz4"),
        "brotli": find("brotli"),
        "lzma":   find("lzma"),
        "lzip":   find("lzip"),
        "lzop":   find("lzop"),
        "zpaq":   find("zpaq"),
        "7z":     find("7z") or find("7zz"),
        "zip":    find("zip"),
    }
    return {k: v for k, v in candidates.items() if v}

# ─── job builders ─────────────────────────────────────────────────────────────

def _tmp(out: Path, extra: str = "") -> Path:
    return out.parent / (out.name + ".tmp" + extra)

def _stdout(inp: Path, out: Path, cmd: list, tag: str) -> Job:
    return Job(inp=inp, out=out, tmp=_tmp(out), tag=tag,
               cmd=tuple(cmd + [str(inp)]), cwd=None, capture=True, skip=None)

def _file(inp: Path, out: Path, cmd: list, tag: str,
          cwd: Optional[Path] = None, tmp_ext: str = "") -> Job:
    return Job(inp=inp, out=out, tmp=_tmp(out, tmp_ext), tag=tag,
               cmd=tuple(cmd), cwd=cwd, capture=False, skip=None)

def _direct(inp: Path, out: Path, cmd: list, tag: str) -> Job:
    return Job(inp=inp, out=out, tmp=None, tag=tag,
               cmd=tuple(cmd), cwd=None, capture=False, skip=None)


def gz_jobs(inp: Path, out_dir: Path, sz: int, binary: str, leveled: bool) -> list[Job]:
    base = [binary, "-n", "-k", "-c"]
    lvl = "-1" if sz > LARGE else "-9"
    jobs = [_stdout(inp, out_dir / (inp.name + ".gz"), base + [lvl], "gz")]
    if leveled:
        for l in LEVELS_GZ:
            jobs.append(_stdout(inp, out_dir / (inp.name + f".gz.l{l}"),
                                base + [f"-{l}"], f"gz.l{l}"))
    return jobs


def bz2_jobs(inp: Path, out_dir: Path, sz: int, binary: str) -> list[Job]:
    lvl = "-1" if sz > LARGE else "-9"
    return [_stdout(inp, out_dir / (inp.name + ".bz2"), [binary, "-k", "-c", lvl], "bz2")]


def xz_jobs(inp: Path, out_dir: Path, sz: int, binary: str, leveled: bool) -> list[Job]:
    base = [binary, "-k", "-c", "-T1"]
    lvl = "-1" if sz > LARGE else "-9e"
    jobs = [_stdout(inp, out_dir / (inp.name + ".xz"), base + [lvl], "xz")]
    if leveled:
        for l in LEVELS_XZ:
            jobs.append(_stdout(inp, out_dir / (inp.name + f".xz.l{l}"),
                                base + [f"-{l}"], f"xz.l{l}"))
    return jobs


def zst_jobs(inp: Path, out_dir: Path, sz: int, binary: str, leveled: bool) -> list[Job]:
    base = [binary, "-k", "-T1", "-q", "-f", "--no-progress"]

    def _at(level: int, tag: str) -> Job:
        out = out_dir / (inp.name + (f".zst.l{level}" if tag != "zst" else ".zst"))
        tmp = _tmp(out)
        extra = ["--ultra"] if level > 19 else []
        cmd = base + extra + [f"-{level}", str(inp), "-o", str(tmp)]
        return Job(inp=inp, out=out, tmp=tmp, tag=tag, cmd=tuple(cmd),
                   cwd=None, capture=False, skip=None)

    lvl = 3 if sz > LARGE else 19
    jobs = [_at(lvl, "zst")]
    if leveled:
        for l in LEVELS_ZST:
            jobs.append(_at(l, f"zst.l{l}"))
    return jobs


def lz4_jobs(inp: Path, out_dir: Path, sz: int, binary: str) -> list[Job]:
    lvl = "-1" if sz > LARGE else "-9"
    return [_stdout(inp, out_dir / (inp.name + ".lz4"),
                    [binary, "-k", "-c", "-q", lvl], "lz4")]


def br_jobs(inp: Path, out_dir: Path, sz: int, binary: str, leveled: bool) -> list[Job]:
    base = [binary, "-k", "-c"]
    q = "1" if sz > BR_LARGE else "11"
    jobs = [_stdout(inp, out_dir / (inp.name + ".br"), base + ["-q", q], "br")]
    if leveled:
        for l in LEVELS_BR:
            jobs.append(_stdout(inp, out_dir / (inp.name + f".br.l{l}"),
                                base + ["-q", str(l)], f"br.l{l}"))
    return jobs


def lzma_jobs(inp: Path, out_dir: Path, sz: int, binary: str) -> list[Job]:
    lvl = "-1" if sz > LARGE else "-9"
    return [_stdout(inp, out_dir / (inp.name + ".lzma"), [binary, "-k", "-c", lvl], "lzma")]


def lzip_jobs(inp: Path, out_dir: Path, sz: int, binary: str) -> list[Job]:
    lvl = "-1" if sz > LARGE else "-9"
    return [_stdout(inp, out_dir / (inp.name + ".lz"), [binary, "-k", "-c", lvl], "lz")]


def lzop_jobs(inp: Path, out_dir: Path, sz: int, binary: str) -> list[Job]:
    lvl = "-1" if sz > LARGE else "-9"
    return [_stdout(inp, out_dir / (inp.name + ".lzo"),
                    [binary, "-k", "-c", "-n", lvl], "lzo")]


def zpaq_jobs(inp: Path, out_dir: Path, sz: int, binary: str) -> list[Job]:
    out = out_dir / (inp.name + ".zpaq")
    m = "1" if sz > LARGE else "5"
    return [_direct(inp, out, [binary, "add", str(out), str(inp), f"-m{m}"], "zpaq")]


def sevenz_jobs(inp: Path, out_dir: Path, sz: int, binary: str) -> list[Job]:
    out = out_dir / (inp.name + ".7z")
    tmp = out.with_suffix(".tmp.7z")   # e.g. dickens.tmp.7z → renamed to dickens.7z
    mx = "1" if sz > LARGE else "9"
    cmd = [binary, "a", "-mtm=off", "-mtc=off", "-mta=off", "-bd", "-bb0",
           f"-mx={mx}", "-y", str(tmp), inp.name]
    return [Job(inp=inp, out=out, tmp=tmp, tag="7z", cmd=tuple(cmd),
                cwd=inp.parent, capture=False, skip=None)]


def zip_jobs(inp: Path, out_dir: Path, zip_bin: Optional[str],
             p7z_bin: Optional[str]) -> list[Job]:
    jobs = []

    if zip_bin:
        def _z(suffix: str, flags: list, tag: str) -> Job:
            out = out_dir / (inp.name + suffix)
            tmp = _tmp(out)
            cmd = tuple([zip_bin, "-X", "-q"] + flags + [str(tmp), inp.name])
            return Job(inp=inp, out=out, tmp=tmp, tag=tag, cmd=cmd,
                       cwd=inp.parent, capture=False, skip=None)
        jobs.append(_z(".zip",         ["-9"],                    "zip"))
        jobs.append(_z(".zip.store",   ["-0"],                    "zip.store"))
        jobs.append(_z(".zip.deflate", ["-Z", "deflate", "-9"],   "zip.deflate"))

    if p7z_bin:
        def _pz(suffix: str, codec: str, tag: str) -> Job:
            out = out_dir / (inp.name + suffix)
            tmp = _tmp(out, ".zip")
            cmd = tuple([p7z_bin, "a", "-tzip", f"-mm={codec}", "-mx=9",
                         "-bd", "-bb0", "-y", str(tmp), inp.name])
            return Job(inp=inp, out=out, tmp=tmp, tag=tag, cmd=cmd,
                       cwd=inp.parent, capture=False, skip=None)
        jobs.append(_pz(".zip.bzip2", "bzip2", "zip.bzip2"))
        jobs.append(_pz(".zip.lzma",  "LZMA",  "zip.lzma"))

    return jobs


def make_jobs(inp: Path, out_dir: Path, sz: int, tools: dict,
              leveled: bool) -> list[Job]:
    t = tools
    jobs: list[Job] = []
    if t.get("gzip"):   jobs += gz_jobs(inp, out_dir, sz, t["gzip"],   leveled)
    if t.get("bzip2"):  jobs += bz2_jobs(inp, out_dir, sz, t["bzip2"])
    if t.get("xz"):     jobs += xz_jobs(inp, out_dir, sz, t["xz"],     leveled)
    if t.get("zstd"):   jobs += zst_jobs(inp, out_dir, sz, t["zstd"],  leveled)
    if t.get("lz4"):    jobs += lz4_jobs(inp, out_dir, sz, t["lz4"])
    if t.get("brotli"): jobs += br_jobs(inp, out_dir, sz, t["brotli"], leveled)
    if t.get("lzma"):   jobs += lzma_jobs(inp, out_dir, sz, t["lzma"])
    if t.get("lzip"):   jobs += lzip_jobs(inp, out_dir, sz, t["lzip"])
    if t.get("lzop"):   jobs += lzop_jobs(inp, out_dir, sz, t["lzop"])
    if t.get("zpaq"):   jobs += zpaq_jobs(inp, out_dir, sz, t["zpaq"])
    if t.get("7z"):     jobs += sevenz_jobs(inp, out_dir, sz, t["7z"])
    jobs += zip_jobs(inp, out_dir, t.get("zip"), t.get("7z"))
    return jobs

# ─── cache ────────────────────────────────────────────────────────────────────

_tls = threading.local()

def get_conn(db_path: Path) -> sqlite3.Connection:
    if not hasattr(_tls, "conn"):
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _tls.conn = conn
    return _tls.conn

def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()

def build_key(inp_hash: str, tag: str, cmd: tuple, binary: str, binary_ver: str) -> str:
    flags_sig = " ".join(
        str(c) for c in cmd
        if not (os.sep in str(c) and Path(str(c)).exists())
    )
    raw = "\0".join([inp_hash, tag, flags_sig, binary, binary_ver, MACHINE])
    return hashlib.sha256(raw.encode()).hexdigest()

# ─── execution ────────────────────────────────────────────────────────────────

def run_job(job: Job) -> tuple[str, str, float]:
    """Returns (status, detail, elapsed_s). status: 'ok'|'skip:…'|'fail'."""
    if job.skip:
        return (f"skip:{job.skip}", "", 0.0)

    t0 = time.monotonic()
    try:
        job.out.parent.mkdir(parents=True, exist_ok=True)

        if job.tmp is None:
            job.out.unlink(missing_ok=True)
        else:
            job.tmp.unlink(missing_ok=True)

        if job.capture:
            with open(job.tmp, "wb") as f:
                r = subprocess.run(list(job.cmd), stdout=f, stderr=subprocess.PIPE,
                                   cwd=job.cwd, timeout=JOB_TIMEOUT)
        else:
            r = subprocess.run(list(job.cmd), stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, cwd=job.cwd, timeout=JOB_TIMEOUT)

        elapsed = time.monotonic() - t0

        if r.returncode != 0:
            if job.tmp:
                job.tmp.unlink(missing_ok=True)
            detail = r.stderr.decode(errors="replace").strip() or f"exit {r.returncode}"
            return ("fail", detail[:500], elapsed)

        check = job.tmp if job.tmp is not None else job.out
        if not check.exists():
            return ("fail", "output not created", elapsed)
        if check.stat().st_size == 0 and job.inp.stat().st_size > 0:
            check.unlink(missing_ok=True)
            return ("fail", "empty output (incompressible data?)", elapsed)

        if job.tmp is not None:
            job.tmp.rename(job.out)

        return ("ok", "", elapsed)

    except subprocess.TimeoutExpired:
        if job.tmp:
            job.tmp.unlink(missing_ok=True)
        return ("fail", f"timeout after {JOB_TIMEOUT}s", time.monotonic() - t0)
    except Exception as e:
        if job.tmp:
            job.tmp.unlink(missing_ok=True)
        return ("fail", str(e), time.monotonic() - t0)

# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build per-file compression artifacts (build/individual/).")
    ap.add_argument("-j", "--workers", type=int,
                    default=max(1, os.cpu_count() // 2),
                    metavar="N",
                    help="parallel workers (default: ncpu/2 = %(default)s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show pending jobs without running them")
    ap.add_argument("--clean", action="store_true",
                    help="wipe build cache before running")
    args = ap.parse_args()

    if not RAW.exists():
        print("ERROR: build/raw/ not found — run 'make raw' first", file=sys.stderr)
        return 1

    # Tool discovery
    tools = discover()
    if not tools:
        print("ERROR: no compression tools found on PATH", file=sys.stderr)
        return 1
    missing = sorted({"gzip","bzip2","xz","zstd","lz4","brotli","lzma"} - set(tools))
    print(f"Tools: {', '.join(sorted(tools))}"
          + (f"  [missing: {', '.join(missing)}]" if missing else ""))
    tool_vers = {k: tool_version(v) for k, v in tools.items()}

    # Cache setup
    if args.clean and CACHE.exists():
        CACHE.unlink()
        print("Cache cleared.")

    db_path = CACHE
    main_db = get_conn(db_path)
    main_db.execute("""CREATE TABLE IF NOT EXISTS builds (
        key TEXT PRIMARY KEY, out TEXT, status TEXT, detail TEXT, ts REAL)""")
    main_db.commit()

    # Hash all input files upfront (parallelised)
    all_inputs: list[Path] = []
    for s in SETS:
        sd = RAW / s
        if sd.exists():
            all_inputs += sorted(f for f in sd.iterdir() if f.is_file())

    print(f"Hashing {len(all_inputs)} input files...", end=" ", flush=True)
    with ThreadPoolExecutor(max_workers=4) as ex:
        hashes: dict[Path, str] = dict(zip(all_inputs, ex.map(file_hash, all_inputs)))
    print("done")

    # Generate all jobs
    all_jobs: list[Job] = []
    for inp in all_inputs:
        set_name = inp.parent.name
        leveled = set_name in LEVELED
        sz = inp.stat().st_size
        all_jobs += make_jobs(inp, INDIV / set_name, sz, tools, leveled)

    print(f"Planned {len(all_jobs)} artifacts")

    # Load cache into memory
    cached: dict[str, str] = {
        r[0]: r[1]
        for r in main_db.execute("SELECT key, status FROM builds").fetchall()
    }

    # Classify each job
    pending: list[tuple[Job, str]] = []
    n_done = n_skip_tool = n_adopted = 0
    adopt_rows: list[tuple] = []

    for job in all_jobs:
        if job.skip == "no_tool":
            n_skip_tool += 1
            continue
        tool_name = job.tag.split(".")[0]
        binary = tools.get(tool_name, "")
        ver = tool_vers.get(tool_name, "?")
        key = build_key(hashes[job.inp], job.tag, job.cmd, binary, ver)
        if cached.get(key) == "ok" and job.out.exists():
            n_done += 1
        elif job.out.exists() and job.out.stat().st_size > 0:
            # Output exists from a prior build (e.g. old Make rules) — adopt it.
            adopt_rows.append((key, str(job.out), "ok", "adopted", time.time()))
            n_adopted += 1
        else:
            pending.append((job, key))

    if adopt_rows:
        main_db.executemany("INSERT OR REPLACE INTO builds VALUES (?,?,?,?,?)", adopt_rows)
        main_db.commit()

    print(f"  {n_done} cached, {n_adopted} adopted, {n_skip_tool} tool-skipped, {len(pending)} pending")

    if args.dry_run:
        for job, _ in pending[:25]:
            print(f"  PENDING  {job.out.relative_to(BUILD)}")
        if len(pending) > 25:
            print(f"  … and {len(pending) - 25} more")
        return 0

    if not pending:
        print("All up to date.")
        STAMP.touch()
        return 0

    # Build in parallel
    print(f"Building {len(pending)} artifacts with {args.workers} worker(s)…")
    n_ok = n_fail = 0
    failures: list[tuple[str, str]] = []
    done = 0
    total = len(pending)
    shutdown = threading.Event()

    def save(key: str, out: Path, status: str, detail: str) -> None:
        conn = get_conn(db_path)
        conn.execute("INSERT OR REPLACE INTO builds VALUES (?,?,?,?,?)",
                     (key, str(out), status, detail, time.time()))
        conn.commit()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        orig = signal.getsignal(signal.SIGINT)

        def _sigint(sig, frame):
            print("\nInterrupted — waiting for in-flight jobs…", file=sys.stderr)
            shutdown.set()
            signal.signal(signal.SIGINT, orig)

        signal.signal(signal.SIGINT, _sigint)

        futs = {}
        for job, key in pending:
            if shutdown.is_set():
                break
            futs[ex.submit(run_job, job)] = (job, key)

        for fut in as_completed(futs):
            if shutdown.is_set():
                break
            job, key = futs[fut]
            status, detail, elapsed = fut.result()
            save(key, job.out, status, detail)
            done += 1

            cat = status.split(":")[0]
            marker = {"ok": "OK  ", "fail": "FAIL"}.get(cat, "SKIP")
            t_str = f"  {elapsed:.1f}s" if elapsed >= 0.1 else ""
            print(f"  [{done:4}/{total}] {marker}  {job.tag:<18}  {job.inp.name}{t_str}")

            if cat == "ok":
                n_ok += 1
            elif cat == "fail":
                n_fail += 1
                failures.append((str(job.out.relative_to(BUILD)), detail))

    if shutdown.is_set():
        print("Interrupted — partial results recorded in cache.")
        return 1

    print(f"\nDone: {n_ok} built, {n_skip_tool} tool-skipped, {n_fail} failed")
    if failures:
        print("Failed:")
        for path, reason in failures:
            print(f"  {path}\n    {reason}")
        return 1

    STAMP.touch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
