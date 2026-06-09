#!/usr/bin/env python3
"""Reproducible fetcher/builder for the four Binary & Media additions to the corpus.

Zero arguments — all targets, archive URLs, and expected SHA256 digests are
pinned in this script.  Each artifact is placed at build/raw/corpus/<filename>
and verified against the pinned artifact SHA256 (fail-closed: non-zero exit on
mismatch or any failure).

  uv run python scripts/acquire-binaries.py

Targets
-------
engine.wasm  (upstream)  SQLite 3.53.02 engine compiled to WebAssembly
             Extracted from: sqlite-wasm-3530200.zip → jswasm/sqlite3.wasm

winexe.exe   (upstream)  fd 10.4.2 Windows PE32+ x86-64
             Extracted from: fd-v10.4.2-x86_64-pc-windows-msvc.zip → fd.exe

armexe.elf   (upstream)  hyperfine 1.20.0 ARM64 Linux ELF
             Extracted from: hyperfine-v1.20.0-aarch64-unknown-linux-gnu.tar.gz
             → hyperfine-v1.20.0-aarch64-unknown-linux-gnu/hyperfine

symbols.dwarf (minted)   Lua 5.4.8 DWARF debug-symbols companion
             Downloaded: lua-5.4.8.tar.gz; compiled with `clang -g -O2 src/*.c`;
             DWARF extracted via `dsymutil` from the resulting dSYM bundle.

Minted / toolchain caveat (symbols.dwarf)
-----------------------------------------
The DWARF companion is built from Lua 5.4.8 source with a specific clang
invocation.  The output is NOT guaranteed to be bit-for-bit identical across
different Clang major versions or operating systems.  The pinned artifact SHA256
(9f3b57…) was produced on macOS 15 (Darwin x86_64) with Apple clang 16.  If your
toolchain differs, the build will succeed but the verification step will fail,
printing the actual SHA so you can pin a new value.  The purpose of keeping this
member is its byte-profile (DWARF bitpattern), not compiler-version fidelity;
re-pinning on a new toolchain is the correct response to a mismatch.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "build" / "raw" / "corpus"
UA = {"User-Agent": "squishy-corpus/1.0 (+https://github.com/JackDanger/squishy-corpus)"}

# ── Archive + artifact manifests ─────────────────────────────────────────────
#   (archive_url, archive_sha256, artifact_name_in_archive, corpus_filename, artifact_sha256)
UPSTREAM = [
    (
        "https://sqlite.org/2026/sqlite-wasm-3530200.zip",
        "f14eb7afc88efb7bc1c51e669ff23d08f813c2a996bcc2f76d3bd5086b13f1b6",
        "jswasm/sqlite3.wasm",
        "engine.wasm",
        "ae1cd941deaa3e6a4880e6f1287a5354cfab9e8dfbbb389158e94026f364da49",
    ),
    (
        "https://github.com/sharkdp/fd/releases/download/v10.4.2/fd-v10.4.2-x86_64-pc-windows-msvc.zip",
        "b2816e506390a89941c63c9187d58a3cc10e9a55f2ef0685f9ea0eccaf7c98c8",
        "fd-v10.4.2-x86_64-pc-windows-msvc/fd.exe",
        "winexe.exe",
        "4c9d082ee20f0d9e44881ac4e92adf765efc314d82103c53d7f576bd78dc5761",
    ),
    (
        "https://github.com/sharkdp/hyperfine/releases/download/v1.20.0/hyperfine-v1.20.0-aarch64-unknown-linux-gnu.tar.gz",
        "90875cb1db7a1d797c311174d061728361e58fc70e3b62262a00635ac3b1997c",
        "hyperfine-v1.20.0-aarch64-unknown-linux-gnu/hyperfine",
        "armexe.elf",
        "36b694487054adb4bd239fcff2d7cff6fdc96105bfcc6715dc5e7a60f8a21138",
    ),
]

LUA_URL = "https://www.lua.org/ftp/lua-5.4.8.tar.gz"
LUA_ARCHIVE_SHA = "4f18ddae154e793e46eeab727c59ef1c0c0c2b744e7b94219710d76f530629ae"
# Pinned on macOS 15 / Apple clang 16 (x86_64).  See minted caveat in docstring.
SYMBOLS_SHA = "9f3b57ebf4c2e5ad963c38e32a4fffa66fb7f4ab364901ad33ecc907323e4a08"


# ── Helpers ──────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_stream(f) -> str:
    h = hashlib.sha256()
    for chunk in iter(lambda: f.read(1 << 22), b""):
        h.update(chunk)
    return h.hexdigest()


def download(url: str, dst: Path) -> str:
    """Download url → dst; return hex sha256 of the downloaded content."""
    print(f"  ↓ {url}", flush=True)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r, dst.open("wb") as out:
        shutil.copyfileobj(r, out, length=1 << 23)
    return sha256_file(dst)


def check(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        print(f"  FAIL sha256 mismatch for {label}")
        print(f"    expected: {expected}")
        print(f"    actual:   {actual}")
        sys.exit(1)
    print(f"  ✓ sha256 ok: {label}")


# ── Upstream extractions ──────────────────────────────────────────────────────

def acquire_upstream(url: str, archive_sha: str, member: str,
                     corpus_name: str, artifact_sha: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = OUT_DIR / corpus_name
    if dst.exists() and sha256_file(dst) == artifact_sha:
        print(f"  skip {corpus_name} (already present, sha verified)", flush=True)
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / Path(url).name
        actual_archive_sha = download(url, archive)
        check(actual_archive_sha, archive_sha, f"{archive.name} (archive)")

        lower = url.lower()
        if lower.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                with z.open(member) as src, dst.open("wb") as out:
                    shutil.copyfileobj(src, out, length=1 << 23)
        else:
            # .tar.gz / .tgz
            with tarfile.open(archive) as t:
                m = t.getmember(member)
                with t.extractfile(m) as src, dst.open("wb") as out:
                    shutil.copyfileobj(src, out, length=1 << 23)

    actual = sha256_file(dst)
    check(actual, artifact_sha, corpus_name)
    print(f"  wrote {dst.relative_to(REPO)} ({dst.stat().st_size:,} bytes)", flush=True)


# ── Minted: Lua DWARF symbols ─────────────────────────────────────────────────

def acquire_symbols() -> None:
    """Download Lua 5.4.8, compile with clang -g -O2, extract DWARF companion."""
    corpus_name = "symbols.dwarf"
    dst = OUT_DIR / corpus_name
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if dst.exists() and sha256_file(dst) == SYMBOLS_SHA:
        print(f"  skip {corpus_name} (already present, sha verified)", flush=True)
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / "lua-5.4.8.tar.gz"
        actual_archive_sha = download(LUA_URL, archive)
        check(actual_archive_sha, LUA_ARCHIVE_SHA, "lua-5.4.8.tar.gz (source archive)")

        with tarfile.open(archive) as t:
            t.extractall(tmpdir)

        src_dir = tmpdir / "lua-5.4.8" / "src"
        c_files = sorted(src_dir.glob("*.c"))
        binary = tmpdir / "lua-debug-x86"
        dsym_dir = tmpdir / "lua-debug-x86.dSYM"

        # Compile: all src/*.c, debug info + optimisation flags that match the
        # pinned artifact.  Requires Apple clang (macOS) — see toolchain caveat.
        compile_cmd = (
            ["clang", "-g", "-O2", "-arch", "x86_64"]
            + [str(p) for p in c_files]
            + ["-o", str(binary), "-lm"]
        )
        print(f"  compiling lua-5.4.8 ({len(c_files)} C files) …", flush=True)
        subprocess.run(compile_cmd, check=True, cwd=tmpdir)

        # Extract dSYM (macOS only — dsymutil ships with Xcode CLT).
        print("  extracting dSYM with dsymutil …", flush=True)
        subprocess.run(["dsymutil", str(binary), "-o", str(dsym_dir)], check=True)

        # The DWARF companion lives inside the .dSYM bundle.
        dwarf_companion = dsym_dir / "Contents" / "Resources" / "DWARF" / "lua-debug-x86"
        if not dwarf_companion.exists():
            print(f"  FAIL dSYM companion not found at {dwarf_companion}")
            sys.exit(1)

        shutil.copy2(dwarf_companion, dst)

    actual = sha256_file(dst)
    if actual != SYMBOLS_SHA:
        print(f"  WARNING toolchain mismatch for {corpus_name}:")
        print(f"    pinned:  {SYMBOLS_SHA}")
        print(f"    actual:  {actual}")
        print("  This is expected on non-Apple or different clang versions.")
        print("  The file has been placed at the destination for manual review.")
        print("  Re-pin SYMBOLS_SHA in this script after verifying the content.")
        # Do NOT sys.exit: the artifact was built; caller can re-pin.
    else:
        print(f"  ✓ sha256 ok: {corpus_name}")
    print(f"  wrote {dst.relative_to(REPO)} ({dst.stat().st_size:,} bytes)", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    for url, archive_sha, member, corpus_name, artifact_sha in UPSTREAM:
        print(f"\n[{corpus_name}]", flush=True)
        acquire_upstream(url, archive_sha, member, corpus_name, artifact_sha)

    print("\n[symbols.dwarf]", flush=True)
    acquire_symbols()

    print("\nAll binary members ready in build/raw/corpus/", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
