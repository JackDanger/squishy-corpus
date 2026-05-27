"""Codec registry for the squishy compression pipeline.

Every compression codec that the build can emit is described here as a
CodecSpec.  The registry is the single source of truth for tags, file
extensions, required binaries, and how to turn (input, output) paths into a
subprocess argv.

Design notes
------------
- cmd_template is a plain callable: (inp: Path, out: Path) -> list[str].
  It encapsulates level flags, special options, and environment quirks so that
  callers never have to know about them.
- The 'tmp' output pattern: most codecs write to stdout (capture=True) or
  accept an -o flag (capture=False, file written directly).  The caller is
  responsible for renaming the tmp file to the final path.
- zpaq writes its output directly and cannot be renamed atomically; the
  caller must remove the destination first.
- 7z and zip commands must be executed with cwd=inp.parent; the template
  does NOT embed paths—the caller sets cwd.
"""
from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Callable

# File size thresholds for adaptive quality selection.
_LARGE = 64 * 1024 * 1024       # 64 MiB: drop to fast level above this
_BR_LARGE = 16 * 1024 * 1024    # brotli window limit is 24-bit; be conservative


CmdTemplate = Callable[[Path, Path], list[str]]


@dataclasses.dataclass(frozen=True)
class CodecSpec:
    """Description of one compression codec variant."""

    tag: str            # short label: "gz", "zst.l22", "zip.deflate", …
    ext: str            # dot-prefixed file extension: ".gz", ".zst.l1", …
    binary: str         # canonical tool name for PATH lookup: "gzip", "zstd", …
    cmd_template: CmdTemplate = dataclasses.field(
        compare=False, hash=False,
    )
    level: int | None = None
    capture: bool = True   # True → stdout → tmp; False → tool writes directly
    # When cwd_is_inp_parent=True, the caller must chdir to inp.parent before
    # running the command.  The template uses inp.name instead of the full path.
    cwd_is_inp_parent: bool = False
    # When direct=True, the tool writes directly to the *final* out path, not a
    # tmp.  The caller must remove the destination before invoking.
    direct: bool = False


# ---------------------------------------------------------------------------
# Template factories
# ---------------------------------------------------------------------------

def _stdout_cmd(binary: str, flags: list[str]) -> CmdTemplate:
    """Tool writes compressed data to stdout; caller captures it."""
    def _t(inp: Path, _out: Path) -> list[str]:
        return [binary] + flags + [str(inp)]
    return _t


def _outfile_cmd(binary: str, flags: list[str]) -> CmdTemplate:
    """Tool accepts -o <out> and writes directly to that path."""
    def _t(inp: Path, out: Path) -> list[str]:
        return [binary] + flags + [str(inp), "-o", str(out)]
    return _t


def _gz_cmd(level: int | None = None) -> CmdTemplate:
    """gzip with optional explicit level; uses adaptive default if None."""
    def _t(inp: Path, _out: Path) -> list[str]:
        l = level
        if l is None:
            l = 1 if inp.stat().st_size > _LARGE else 9
        return ["gzip", "-n", "-k", "-c", f"-{l}", str(inp)]
    return _t


def _bz2_cmd() -> CmdTemplate:
    def _t(inp: Path, _out: Path) -> list[str]:
        l = 1 if inp.stat().st_size > _LARGE else 9
        return ["bzip2", "-k", "-c", f"-{l}", str(inp)]
    return _t


def _xz_cmd(level: int | None = None) -> CmdTemplate:
    def _t(inp: Path, _out: Path) -> list[str]:
        if level is not None:
            lf = f"-{level}"
        else:
            lf = "-1" if inp.stat().st_size > _LARGE else "-9e"
        return ["xz", "-k", "-c", "-T1", lf, str(inp)]
    return _t


def _zst_cmd(level: int | None = None) -> CmdTemplate:
    def _t(inp: Path, out: Path) -> list[str]:
        if level is not None:
            l = level
        else:
            l = 3 if inp.stat().st_size > _LARGE else 19
        extra = ["--ultra"] if l > 19 else []
        return ["zstd", "-k", "-T1", "-q", "-f", "--no-progress"] + extra + [
            f"-{l}", str(inp), "-o", str(out)
        ]
    return _t


def _lz4_cmd() -> CmdTemplate:
    def _t(inp: Path, _out: Path) -> list[str]:
        l = 1 if inp.stat().st_size > _LARGE else 9
        return ["lz4", "-k", "-c", "-q", f"-{l}", str(inp)]
    return _t


def _br_cmd(level: int | None = None) -> CmdTemplate:
    def _t(inp: Path, _out: Path) -> list[str]:
        if level is not None:
            q = str(level)
        else:
            q = "1" if inp.stat().st_size > _BR_LARGE else "11"
        return ["brotli", "-k", "-c", "-q", q, str(inp)]
    return _t


def _lzma_cmd() -> CmdTemplate:
    def _t(inp: Path, _out: Path) -> list[str]:
        l = 1 if inp.stat().st_size > _LARGE else 9
        return ["lzma", "-k", "-c", f"-{l}", str(inp)]
    return _t


def _lzip_cmd() -> CmdTemplate:
    def _t(inp: Path, _out: Path) -> list[str]:
        l = 1 if inp.stat().st_size > _LARGE else 9
        return ["lzip", "-k", "-c", f"-{l}", str(inp)]
    return _t


def _lzop_cmd() -> CmdTemplate:
    def _t(inp: Path, _out: Path) -> list[str]:
        l = 1 if inp.stat().st_size > _LARGE else 9
        return ["lzop", "-k", "-c", "-n", f"-{l}", str(inp)]
    return _t


def _zpaq_cmd() -> CmdTemplate:
    """zpaq writes directly to out; caller must delete out first."""
    def _t(inp: Path, out: Path) -> list[str]:
        m = "1" if inp.stat().st_size > _LARGE else "5"
        return ["zpaq", "add", str(out), str(inp), f"-m{m}"]
    return _t


def _7z_cmd(level: int | None = None) -> CmdTemplate:
    """7z requires cwd=inp.parent; uses inp.name not full path."""
    def _t(inp: Path, out: Path) -> list[str]:
        mx = "1" if (level is None and inp.stat().st_size > _LARGE) else (
            "1" if level == 1 else "9"
        )
        # out is the .tmp.7z path; rename is done by the caller
        return ["7z", "a", "-mtm=off", "-mtc=off", "-mta=off",
                "-bd", "-bb0", f"-mx={mx}", "-y", str(out), inp.name]
    return _t


def _zip_cmd(flags: list[str]) -> CmdTemplate:
    """zip requires cwd=inp.parent; uses inp.name not full path."""
    def _t(inp: Path, out: Path) -> list[str]:
        return ["zip", "-X", "-q"] + flags + [str(out), inp.name]
    return _t


def _7z_zip_cmd(codec: str) -> CmdTemplate:
    """7z writing a .zip container; requires cwd=inp.parent."""
    def _t(inp: Path, out: Path) -> list[str]:
        return ["7z", "a", "-tzip", f"-mm={codec}", "-mx=9",
                "-mtm=off", "-mtc=off", "-mta=off",
                "-bd", "-bb0", "-y", str(out), inp.name]
    return _t


# ---------------------------------------------------------------------------
# Codec list
# ---------------------------------------------------------------------------

def _cs(tag: str, ext: str, binary: str, tpl: CmdTemplate, level: int | None,
        capture: bool = True, cwd: bool = False, direct: bool = False) -> CodecSpec:
    return CodecSpec(tag=tag, ext=ext, binary=binary, cmd_template=tpl,
                     level=level, capture=capture, cwd_is_inp_parent=cwd,
                     direct=direct)


ALL_CODECS: list[CodecSpec] = [
    # ── gzip ────────────────────────────────────────────────────────────────
    _cs("gz",     ".gz",     "gzip",   _gz_cmd(),    None),
    _cs("gz.l1",  ".gz.l1",  "gzip",   _gz_cmd(1),   1),
    _cs("gz.l6",  ".gz.l6",  "gzip",   _gz_cmd(6),   6),
    _cs("gz.l9",  ".gz.l9",  "gzip",   _gz_cmd(9),   9),

    # ── bzip2 ───────────────────────────────────────────────────────────────
    _cs("bz2",    ".bz2",    "bzip2",  _bz2_cmd(),   None),

    # ── xz ──────────────────────────────────────────────────────────────────
    _cs("xz",     ".xz",     "xz",     _xz_cmd(),    None),
    _cs("xz.l0",  ".xz.l0",  "xz",     _xz_cmd(0),   0),
    _cs("xz.l6",  ".xz.l6",  "xz",     _xz_cmd(6),   6),
    _cs("xz.l9",  ".xz.l9",  "xz",     _xz_cmd(9),   9),

    # ── zstd ────────────────────────────────────────────────────────────────
    _cs("zst",     ".zst",     "zstd",  _zst_cmd(),    None,  capture=False),
    _cs("zst.l1",  ".zst.l1",  "zstd",  _zst_cmd(1),   1,     capture=False),
    _cs("zst.l3",  ".zst.l3",  "zstd",  _zst_cmd(3),   3,     capture=False),
    _cs("zst.l9",  ".zst.l9",  "zstd",  _zst_cmd(9),   9,     capture=False),
    _cs("zst.l19", ".zst.l19", "zstd",  _zst_cmd(19),  19,    capture=False),
    _cs("zst.l22", ".zst.l22", "zstd",  _zst_cmd(22),  22,    capture=False),

    # ── lz4 ─────────────────────────────────────────────────────────────────
    _cs("lz4",    ".lz4",    "lz4",    _lz4_cmd(),    None),

    # ── brotli ──────────────────────────────────────────────────────────────
    _cs("br",     ".br",     "brotli", _br_cmd(),     None),
    _cs("br.l1",  ".br.l1",  "brotli", _br_cmd(1),    1),
    _cs("br.l6",  ".br.l6",  "brotli", _br_cmd(6),    6),
    _cs("br.l11", ".br.l11", "brotli", _br_cmd(11),   11),

    # ── lzma ────────────────────────────────────────────────────────────────
    _cs("lzma",   ".lzma",   "lzma",   _lzma_cmd(),   None),

    # ── lzip ────────────────────────────────────────────────────────────────
    _cs("lz",     ".lz",     "lzip",   _lzip_cmd(),   None),

    # ── lzop ────────────────────────────────────────────────────────────────
    _cs("lzo",    ".lzo",    "lzop",   _lzop_cmd(),   None),

    # ── zpaq ────────────────────────────────────────────────────────────────
    _cs("zpaq",   ".zpaq",   "zpaq",   _zpaq_cmd(),   None,
        capture=False, direct=True),

    # ── 7z ──────────────────────────────────────────────────────────────────
    _cs("7z",     ".7z",     "7z",     _7z_cmd(),     None,
        capture=False, cwd=True),

    # ── zip (default = deflate -9) ────────────────────────────────────────
    _cs("zip",         ".zip",         "zip",  _zip_cmd(["-9"]),           None,
        capture=False, cwd=True),
    _cs("zip.store",   ".zip.store",   "zip",  _zip_cmd(["-0"]),           None,
        capture=False, cwd=True),
    _cs("zip.deflate", ".zip.deflate", "zip",  _zip_cmd(["-Z", "deflate", "-9"]),  None,
        capture=False, cwd=True),
    _cs("zip.bzip2",   ".zip.bzip2",   "7z",   _7z_zip_cmd("bzip2"),      None,
        capture=False, cwd=True),
    _cs("zip.lzma",    ".zip.lzma",    "7z",   _7z_zip_cmd("LZMA"),       None,
        capture=False, cwd=True),
]

_BY_TAG: dict[str, CodecSpec] = {c.tag: c for c in ALL_CODECS}


def codec_for_tag(tag: str) -> CodecSpec | None:
    """Return the CodecSpec for *tag*, or None if not registered."""
    return _BY_TAG.get(tag)


def available_codecs(tools: dict[str, str]) -> list[CodecSpec]:
    """Return codecs whose required binary is present in *tools*.

    *tools* is the dict returned by ``squishy.core.tools.discover()``:
    ``{tool_name: full_path}``.  A codec is available when its ``binary``
    key appears in that dict.
    """
    return [c for c in ALL_CODECS if c.binary in tools]


def cmd_flags_sig(cmd: list[str]) -> str:
    """Extract a stable, path-free flags signature for use in cache keys.

    Filesystem paths (any token containing os.sep that resolves to an existing
    file) are stripped; everything else is joined with spaces.  This matches
    the approach used in the original build-individual.py build_key function.
    """
    import os
    return " ".join(
        tok for tok in cmd
        if not (os.sep in tok and Path(tok).exists())
    )


def bundle_cache_key(
    inputs: tuple[tuple[str, str], ...],
    tag: str,
    flags: str,
    tools_sig: str,
) -> str:
    """Deterministic bundle cache key.

    *inputs* is an ordered sequence of (rel_path_posix, sha256) pairs.
    """
    import json
    payload = json.dumps(
        {"v": 1, "tag": tag, "flags": flags, "tools": tools_sig,
         "inputs": list(inputs)},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
