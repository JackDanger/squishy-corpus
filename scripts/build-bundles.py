#!/usr/bin/env python3
"""
Build bundle artifacts (build/bundles/).

Idempotent, parallel, resumable via SQLite-backed build cache (same DB as
build-individual.py). Each bundle is keyed on its ordered input-file hashes +
format/codec flags + all tool versions, so upgrades or input changes trigger
exactly the affected rebuilds.

No combined/everything bundle is generated here — per-set bundles are the
cacheable unit. Adding a new dataset only rebuilds that dataset's bundles.

  python3 scripts/build-bundles.py           # build everything
  python3 scripts/build-bundles.py -j 4      # explicit parallelism
  python3 scripts/build-bundles.py --only silesia modern   # subset of sets
  python3 scripts/build-bundles.py --dry-run # show pending work
  python3 scripts/build-bundles.py --clean   # wipe bundle cache entries

Exit 0 when all bundles built or skipped. Exit 1 on any failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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

ROOT    = Path(__file__).resolve().parent.parent
BUILD   = ROOT / "build"
RAW     = BUILD / "raw"
BUNDLES = BUILD / "bundles"
CACHE   = BUILD / ".build-cache.db"
STAMP   = BUILD / ".bundles.stamp"
SCRIPTS = Path(__file__).resolve().parent

# ─── constants ───────────────────────────────────────────────────────────────

JOB_TIMEOUT  = 3600        # 1 hour — covers xz on large pathological tars
LARGE_BUNDLE = 200 << 20   # 200 MB: use fast codec levels above this raw-set size

SETS             = ["silesia", "modern", "pathological", "squash"]
ORDERINGS        = ["alpha", "random"]
SOLID_ORDERINGS  = ["alpha", "size-desc"]
TAR_CODECS       = ["gz", "bz2", "xz", "zst", "lz4", "br", "lzma"]
SQUASHFS_CODECS  = ["gzip", "xz", "lz4", "zstd"]
SEVENZ_CODECS    = ["lzma2", "ppmd", "bzip2", "deflate"]
ZIP_INTERNALS    = ["store", "deflate", "bzip2", "lzma"]
CONCAT_CODECS    = ["gz", "xz", "zst"]
CONTAINER_KINDS  = ["cpio", "pax", "ar"]

TAR_FLAGS = ["--mtime=@0", "--owner=0", "--group=0", "--numeric-owner",
             "--format=ustar", "--no-recursion"]

# ─── job model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BundleJob:
    key:            str            # content-addressed cache key
    tag:            str            # display label e.g. "tar.gz.alpha"
    out:            Path           # final output path
    set_name:       str
    kind:           str            # "tar_codec"|"sevenz"|"squashfs"|"zip_bundle"|
                                   # "container"|"concat"|"skipframes"|"mixed"
    codec:          str            # codec/method/kind string for dispatch
    ordering:       str            # "alpha"|"random"|"size-desc"
    inputs:         tuple          # ((rel_path_str, sha256), ...) in build order
    large:          bool           # True → use fast quality levels
    estimated_bytes: int           # for sorting (biggest first)

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
        "gtar":       find("gtar") or find("tar"),
        "gzip":       find("gzip"),
        "bzip2":      find("bzip2"),
        "xz":         find("xz"),
        "zstd":       find("zstd"),
        "lz4":        find("lz4"),
        "brotli":     find("brotli"),
        "lzma":       find("lzma"),
        "lzip":       find("lzip"),
        "lzop":       find("lzop"),
        "7z":         find("7z") or find("7zz"),
        "zip":        find("zip"),
        "cpio":       find("cpio"),
        "ar":         find("ar"),
        "mksquashfs": find("mksquashfs"),
        "zdict":      find("zstd"),   # same binary, re-listed for clarity
    }
    return {k: v for k, v in candidates.items() if v}

# ─── ordering helpers ─────────────────────────────────────────────────────────

def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()

def ordered_files(set_dir: Path, ordering: str) -> list[Path]:
    files = [f for f in set_dir.iterdir() if f.is_file()]
    if ordering == "alpha":
        return sorted(files, key=lambda p: p.name)
    if ordering == "random":
        return sorted(files, key=lambda p: hashlib.sha256(p.name.encode()).hexdigest())
    if ordering == "size-desc":
        return sorted(files, key=lambda p: -p.stat().st_size)
    raise ValueError(f"unknown ordering: {ordering}")

# ─── cache key ───────────────────────────────────────────────────────────────

def make_key(inputs: tuple, tag: str, flags: str, tools_sig: str) -> str:
    """Content-addressed bundle key. Inputs must be in build order."""
    payload = json.dumps(
        {"v": 1, "tag": tag, "flags": flags, "tools": tools_sig, "inputs": list(inputs)},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()

# ─── environment for subprocess ───────────────────────────────────────────────

def build_env() -> dict:
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = "0"
    env["TZ"] = "UTC"
    env["LC_ALL"] = "C"
    return env

# ─── pipeline executor ───────────────────────────────────────────────────────

def run_pipe(cmd1: list, cmd2: list, tmp: Path, capture2: bool, env: dict) -> None:
    """Stream cmd1 stdout into cmd2 stdin. If capture2, cmd2 stdout → tmp."""
    p1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        if capture2:
            with open(tmp, "wb") as f:
                p2 = subprocess.Popen(cmd2, stdin=p1.stdout, stdout=f,
                                      stderr=subprocess.PIPE, env=env)
        else:
            p2 = subprocess.Popen(cmd2, stdin=p1.stdout, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, env=env)
        p1.stdout.close()
        try:
            p2.wait(timeout=JOB_TIMEOUT)
        except subprocess.TimeoutExpired:
            p1.kill(); p2.kill()
            raise
        p1.wait(timeout=60)
    except BaseException:
        try: p1.kill()
        except Exception: pass
        raise

    if p1.returncode != 0:
        raise RuntimeError(f"tar exit {p1.returncode}: "
                           f"{p1.stderr.read()[:200].decode(errors='replace')}")
    if p2.returncode != 0:
        raise RuntimeError(f"codec exit {p2.returncode}: "
                           f"{p2.stderr.read()[:200].decode(errors='replace')}")

def run_script(args: list, env: dict, timeout: int = JOB_TIMEOUT) -> None:
    r = subprocess.run(args, env=env, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()[:400]
        raise RuntimeError(f"exit {r.returncode}: {err}")

# ─── bundle builders ─────────────────────────────────────────────────────────

def _tar_cmd(gtar: str, set_dir: Path, ordered: list[Path]) -> list:
    base = [gtar] + TAR_FLAGS + ["-C", str(set_dir.parent), "-cf", "-"]
    return base + [p.relative_to(set_dir.parent).as_posix() for p in ordered]

def build_tar_codec(job: BundleJob, tools: dict, env: dict) -> None:
    set_dir = RAW / job.set_name
    ordered = [RAW / Path(rel) for rel, _ in job.inputs]
    tar = _tar_cmd(tools["gtar"], set_dir, ordered)
    tmp = job.out.parent / (job.out.name + ".bundle.tmp")
    tmp.unlink(missing_ok=True)

    L, c, t = job.large, job.codec, tools
    if c == "gz":
        run_pipe(tar, [t["gzip"], "-n", "-1" if L else "-9", "-c"], tmp, True, env)
    elif c == "bz2":
        run_pipe(tar, [t["bzip2"], "-1" if L else "-9", "-c"], tmp, True, env)
    elif c == "xz":
        run_pipe(tar, [t["xz"], "-T1", "-1" if L else "-9e", "-c"], tmp, True, env)
    elif c == "zst":
        lvl = "-3" if L else "-19"
        run_pipe(tar, [t["zstd"], "-T1", lvl, "-q", "-f", "--no-progress",
                       "-", "-o", str(tmp)], tmp, False, env)
    elif c == "lz4":
        run_pipe(tar, [t["lz4"], "-1" if L else "-9", "-q", "-c"], tmp, True, env)
    elif c == "br":
        # brotli: always use q=1 for bundle tars (always >16MB)
        run_pipe(tar, [t["brotli"], "-q", "1", "-c"], tmp, True, env)
    elif c == "lzma":
        run_pipe(tar, [t["lzma"], "-1" if L else "-9", "-c"], tmp, True, env)
    else:
        raise ValueError(f"unknown tar codec: {c}")

    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError("empty output")
    tmp.rename(job.out)


def build_sevenz(job: BundleJob, env: dict) -> None:
    run_script([str(SCRIPTS / "build-7z.sh"), job.codec, job.ordering,
                str(RAW / job.set_name), str(job.out)], env)


def build_squashfs(job: BundleJob, env: dict) -> None:
    run_script([str(SCRIPTS / "build-squashfs.sh"), job.codec, job.ordering,
                str(RAW / job.set_name), str(job.out)], env)


def build_zip_bundle(job: BundleJob, env: dict) -> None:
    run_script([str(SCRIPTS / "build-zip.sh"), job.codec,
                str(RAW / job.set_name), str(job.out)], env)


def build_container(job: BundleJob, env: dict) -> None:
    run_script([str(SCRIPTS / "build-container.sh"), job.codec,
                str(RAW / job.set_name), str(job.out)], env)


def build_concat(job: BundleJob, env: dict) -> None:
    run_script([str(SCRIPTS / "build-concat.sh"), job.codec,
                str(RAW / job.set_name), str(job.out)], env)


def build_skipframes(job: BundleJob, env: dict) -> None:
    run_script([sys.executable, str(SCRIPTS / "build-zst-skipframes.py"),
                str(RAW / job.set_name), str(job.out)], env)


def build_mixed(job: BundleJob, env: dict) -> None:
    run_script([sys.executable, str(SCRIPTS / "build-mixed-member.py"),
                str(RAW / job.set_name), str(job.out)], env)

# ─── job runner ───────────────────────────────────────────────────────────────

def run_job(job: BundleJob, tools: dict, env: dict) -> tuple[str, str, float]:
    """Returns (status, detail, elapsed_s). status: 'ok'|'fail'."""
    t0 = time.monotonic()
    try:
        job.out.parent.mkdir(parents=True, exist_ok=True)
        dispatch = {
            "tar_codec":  lambda: build_tar_codec(job, tools, env),
            "sevenz":     lambda: build_sevenz(job, env),
            "squashfs":   lambda: build_squashfs(job, env),
            "zip_bundle": lambda: build_zip_bundle(job, env),
            "container":  lambda: build_container(job, env),
            "concat":     lambda: build_concat(job, env),
            "skipframes": lambda: build_skipframes(job, env),
            "mixed":      lambda: build_mixed(job, env),
        }
        dispatch[job.kind]()

        elapsed = time.monotonic() - t0
        if not job.out.exists() or job.out.stat().st_size == 0:
            return ("fail", "output not created or empty", elapsed)
        return ("ok", "", elapsed)

    except subprocess.TimeoutExpired:
        return ("fail", f"timeout after {JOB_TIMEOUT}s", time.monotonic() - t0)
    except Exception as e:
        return ("fail", str(e)[:400], time.monotonic() - t0)

# ─── job planning ─────────────────────────────────────────────────────────────

def plan_jobs(set_name: str, hashes: dict[Path, str], tools: dict,
              tool_vers: dict[str, str]) -> list[BundleJob]:
    set_dir = RAW / set_name
    if not set_dir.exists():
        return []

    all_files = sorted([f for f in set_dir.iterdir() if f.is_file()],
                       key=lambda p: p.name)
    total_raw = sum(f.stat().st_size for f in all_files)
    large = total_raw > LARGE_BUNDLE

    tools_sig = json.dumps({k: tool_vers.get(k, "?") for k in sorted(tools)},
                           separators=(",", ":"))

    out_dir = BUNDLES / set_name
    jobs: list[BundleJob] = []

    def input_sigs(ordered: list[Path]) -> tuple:
        return tuple(
            (f.relative_to(RAW).as_posix(), hashes[f]) for f in ordered
        )

    # ── tar + codec (pipe: no intermediate tar on disk) ───────────────────────
    for codec in TAR_CODECS:
        codec_tool = {"gz": "gzip", "bz2": "bzip2", "xz": "xz", "zst": "zstd",
                      "lz4": "lz4", "br": "brotli", "lzma": "lzma"}.get(codec, codec)
        if not tools.get("gtar") or not tools.get(codec_tool):
            continue
        for ordering in ORDERINGS:
            ordered = ordered_files(set_dir, ordering)
            sigs = input_sigs(ordered)
            ext = f"tar.{codec}"
            lvl = "1" if large else ("19" if codec == "zst" else "9")
            flags = f"tar|{codec}|level={lvl}"
            key = make_key(sigs, f"tar.{codec}.{ordering}", flags, tools_sig)
            jobs.append(BundleJob(
                key=key, tag=f"tar.{codec}.{ordering}",
                out=out_dir / f"{set_name}.{ordering}.{ext}",
                set_name=set_name, kind="tar_codec", codec=codec,
                ordering=ordering, inputs=sigs, large=large,
                estimated_bytes=total_raw,
            ))

    # ── 7z (solid) ────────────────────────────────────────────────────────────
    if tools.get("7z"):
        for codec in SEVENZ_CODECS:
            for ordering in SOLID_ORDERINGS:
                ordered = ordered_files(set_dir, ordering)
                sigs = input_sigs(ordered)
                flags = f"7z|{codec}|solid"
                key = make_key(sigs, f"7z.{codec}.{ordering}", flags, tools_sig)
                jobs.append(BundleJob(
                    key=key, tag=f"7z.{codec}.{ordering}",
                    out=out_dir / f"{set_name}.{ordering}.7z.{codec}",
                    set_name=set_name, kind="sevenz", codec=codec,
                    ordering=ordering, inputs=sigs, large=large,
                    estimated_bytes=total_raw // 2,
                ))

    # ── squashfs ──────────────────────────────────────────────────────────────
    if tools.get("mksquashfs"):
        for codec in SQUASHFS_CODECS:
            for ordering in SOLID_ORDERINGS:
                ordered = ordered_files(set_dir, ordering)
                sigs = input_sigs(ordered)
                flags = f"squashfs|{codec}"
                key = make_key(sigs, f"squashfs.{codec}.{ordering}", flags, tools_sig)
                jobs.append(BundleJob(
                    key=key, tag=f"squashfs.{codec}.{ordering}",
                    out=out_dir / f"{set_name}.{ordering}.squashfs.{codec}",
                    set_name=set_name, kind="squashfs", codec=codec,
                    ordering=ordering, inputs=sigs, large=large,
                    estimated_bytes=total_raw // 2,
                ))

    # ── zip bundles ───────────────────────────────────────────────────────────
    if tools.get("zip") or tools.get("7z"):
        ordering = "alpha"
        ordered = ordered_files(set_dir, ordering)
        sigs = input_sigs(ordered)
        for codec in ZIP_INTERNALS:
            if codec in ("bzip2", "lzma") and not tools.get("7z"):
                continue
            flags = f"zip|{codec}"
            key = make_key(sigs, f"zip.{codec}.alpha", flags, tools_sig)
            jobs.append(BundleJob(
                key=key, tag=f"zip.{codec}.alpha",
                out=out_dir / f"{set_name}.alpha.zip.{codec}",
                set_name=set_name, kind="zip_bundle", codec=codec,
                ordering=ordering, inputs=sigs, large=large,
                estimated_bytes=total_raw // 2,
            ))

    # ── plain containers (cpio / pax / ar) ───────────────────────────────────
    ordering = "alpha"
    ordered = ordered_files(set_dir, ordering)
    sigs = input_sigs(ordered)
    kind_tool = {"cpio": "cpio", "pax": "gtar", "ar": "ar"}
    for kind in CONTAINER_KINDS:
        if not tools.get(kind_tool[kind]):
            continue
        flags = f"container|{kind}"
        key = make_key(sigs, f"container.{kind}.alpha", flags, tools_sig)
        jobs.append(BundleJob(
            key=key, tag=f"container.{kind}.alpha",
            out=out_dir / f"{set_name}.alpha.{kind}",
            set_name=set_name, kind="container", codec=kind,
            ordering=ordering, inputs=sigs, large=large,
            estimated_bytes=total_raw,
        ))

    # ── multi-frame concat ───────────────────────────────────────────────────
    codec_tool = {"gz": "gzip", "xz": "xz", "zst": "zstd"}
    ordering = "alpha"
    ordered = ordered_files(set_dir, ordering)
    sigs = input_sigs(ordered)
    for codec in CONCAT_CODECS:
        if not tools.get(codec_tool[codec]):
            continue
        flags = f"concat|{codec}"
        key = make_key(sigs, f"concat.{codec}.alpha", flags, tools_sig)
        jobs.append(BundleJob(
            key=key, tag=f"concat.{codec}.alpha",
            out=out_dir / f"{set_name}.alpha.concat-{codec}",
            set_name=set_name, kind="concat", codec=codec,
            ordering=ordering, inputs=sigs, large=large,
            estimated_bytes=total_raw // 2,
        ))

    # ── zstd skipframes ──────────────────────────────────────────────────────
    if tools.get("zstd"):
        flags = "concat|zst-skipframes"
        key = make_key(sigs, "concat.zst-skipframes.alpha", flags, tools_sig)
        jobs.append(BundleJob(
            key=key, tag="concat.zst-skipframes.alpha",
            out=out_dir / f"{set_name}.alpha.concat-zst-skipframes",
            set_name=set_name, kind="skipframes", codec="zst",
            ordering=ordering, inputs=sigs, large=large,
            estimated_bytes=total_raw // 2,
        ))

    # ── mixed-member (silesia only) ───────────────────────────────────────────
    if set_name == "silesia" and tools.get("gzip") and tools.get("zstd"):
        out = BUNDLES / "mixed-member" / "silesia-mixed.bin"
        flags = "mixed-member"
        key = make_key(sigs, "mixed-member", flags, tools_sig)
        jobs.append(BundleJob(
            key=key, tag="mixed-member",
            out=out, set_name=set_name, kind="mixed", codec="",
            ordering=ordering, inputs=sigs, large=False,
            estimated_bytes=total_raw // 10,
        ))

    return jobs

# ─── SQLite cache ─────────────────────────────────────────────────────────────

_tls = threading.local()

def get_conn(db_path: Path) -> sqlite3.Connection:
    if not hasattr(_tls, "conn"):
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _tls.conn = conn
    return _tls.conn

# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build bundle artifacts (build/bundles/).")
    ap.add_argument("-j", "--workers", type=int,
                    default=max(1, os.cpu_count() // 2), metavar="N",
                    help="parallel workers (default: ncpu/2 = %(default)s)")
    ap.add_argument("--only", nargs="+", metavar="SET",
                    help="only build bundles for these sets (e.g. silesia modern)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show pending jobs without running them")
    ap.add_argument("--clean", action="store_true",
                    help="wipe bundle cache entries (forces rebuild)")
    args = ap.parse_args()

    if not RAW.exists():
        print("ERROR: build/raw/ not found — run 'make raw' first", file=sys.stderr)
        return 1

    tools = discover()
    if not tools.get("gtar"):
        print("ERROR: gnu tar not found (install gnu-tar)", file=sys.stderr)
        return 1
    tool_vers = {k: tool_version(v) for k, v in tools.items()}
    tool_list = ", ".join(sorted(tools))
    print(f"Tools: {tool_list}")

    active_sets = args.only if args.only else SETS

    # Hash all input files upfront (parallel)
    all_inputs: list[Path] = []
    for s in active_sets:
        sd = RAW / s
        if sd.exists():
            all_inputs += [f for f in sd.iterdir() if f.is_file()]

    print(f"Hashing {len(all_inputs)} input files...", end=" ", flush=True)
    with ThreadPoolExecutor(max_workers=4) as ex:
        hashes: dict[Path, str] = dict(zip(all_inputs, ex.map(file_hash, all_inputs)))
    print("done")

    # Plan jobs
    all_jobs: list[BundleJob] = []
    for s in active_sets:
        all_jobs += plan_jobs(s, hashes, tools, tool_vers)

    # Sort largest first (benefits from disk-budget awareness)
    all_jobs.sort(key=lambda j: -j.estimated_bytes)
    print(f"Planned {len(all_jobs)} bundles across {len(active_sets)} set(s)")

    # Cache setup
    db_path = CACHE
    main_db = get_conn(db_path)
    main_db.execute("""CREATE TABLE IF NOT EXISTS builds (
        key TEXT PRIMARY KEY, out TEXT, status TEXT, detail TEXT, ts REAL)""")
    main_db.commit()

    if args.clean:
        main_db.execute(
            "DELETE FROM builds WHERE out LIKE ?", (f"%{os.sep}bundles{os.sep}%",))
        main_db.commit()
        print("Bundle cache entries cleared.")

    cached: dict[str, str] = {
        r[0]: r[1]
        for r in main_db.execute("SELECT key, status FROM builds").fetchall()
    }

    # Classify jobs
    pending: list[BundleJob] = []
    n_done = n_adopted = 0
    adopt_rows: list[tuple] = []

    for job in all_jobs:
        if cached.get(job.key) == "ok" and job.out.exists():
            n_done += 1
        elif job.out.exists() and job.out.stat().st_size > 0:
            adopt_rows.append((job.key, str(job.out), "ok", "adopted", time.time()))
            n_adopted += 1
        else:
            pending.append(job)

    if adopt_rows:
        main_db.executemany("INSERT OR REPLACE INTO builds VALUES (?,?,?,?,?)", adopt_rows)
        main_db.commit()

    print(f"  {n_done} cached, {n_adopted} adopted, {len(pending)} pending")

    if args.dry_run:
        for job in pending[:30]:
            print(f"  PENDING  {job.out.relative_to(BUILD)}")
        if len(pending) > 30:
            print(f"  … and {len(pending) - 30} more")
        return 0

    if not pending:
        print("All up to date.")
        STAMP.touch()
        return 0

    env = build_env()
    n_ok = n_fail = 0
    failures: list[tuple[str, str]] = []
    done_count = 0
    total = len(pending)
    shutdown = threading.Event()

    def save(key: str, out: Path, status: str, detail: str) -> None:
        conn = get_conn(db_path)
        conn.execute("INSERT OR REPLACE INTO builds VALUES (?,?,?,?,?)",
                     (key, str(out), status, detail, time.time()))
        conn.commit()

    print(f"Building {total} bundles with {args.workers} worker(s)…")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        orig = signal.getsignal(signal.SIGINT)

        def _sigint(sig, frame):
            print("\nInterrupted — waiting for in-flight jobs…", file=sys.stderr)
            shutdown.set()
            signal.signal(signal.SIGINT, orig)

        signal.signal(signal.SIGINT, _sigint)

        futs = {}
        for job in pending:
            if shutdown.is_set():
                break
            futs[ex.submit(run_job, job, tools, env)] = job

        for fut in as_completed(futs):
            if shutdown.is_set():
                break
            job = futs[fut]
            status, detail, elapsed = fut.result()
            save(job.key, job.out, status, detail)
            done_count += 1

            marker = {"ok": "OK  ", "fail": "FAIL"}.get(status, "SKIP")
            t_str = f"  {elapsed:.0f}s" if elapsed >= 1 else ""
            print(f"  [{done_count:4}/{total}] {marker}  {job.tag:<30}  {job.set_name}{t_str}")

            if status == "ok":
                n_ok += 1
            elif status == "fail":
                n_fail += 1
                failures.append((str(job.out.relative_to(BUILD)), detail))

    if shutdown.is_set():
        print("Interrupted — partial results in cache.")
        return 1

    print(f"\nDone: {n_ok} built, {n_adopted} adopted, {n_fail} failed")
    if failures:
        print("Failed:")
        for path, reason in failures:
            print(f"  {path}\n    {reason}")
        return 1

    STAMP.touch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
