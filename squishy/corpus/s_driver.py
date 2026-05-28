"""Structural compressibility (S) measurement driver.

S = 1 − (min compressed bytes over reference set) / raw bytes

Reference set — three disjoint codec families, pinned versions:
  - zstd --long=27 -19  (LZ77 with large window)
  - bzip2 -9             (BWT)
  - zpaq -m5             (context mixing; zpaq v7.15+)

Each codec runs in a subprocess. Results are returned as an SResult with
per-codec compressed sizes and rates, so callers can record which codec won
and recompute S under a different reference set without re-running.

zpaq writes a journaling archive rather than a raw stream, so the driver
uses a temp file. The archive header is ~40–80 bytes overhead; at the
minimum corpus size of 4 MB this is negligible (< 0.002% of file size).

Usage:
    from squishy.corpus.s_driver import measure_s
    result = measure_s(path)
    print(result.S, result.S_bin)
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from squishy.corpus.axes import s_bin, s_label


# Codec tags used in manifest columns
CODEC_ZSTD = "zstd_long27_19"
CODEC_BZIP2 = "bzip2_9"
CODEC_ZPAQ = "zpaq_m5"


@dataclass(frozen=True)
class SResult:
    size_bytes: int
    zstd_bytes: int
    bzip2_bytes: int
    zpaq_bytes: int
    S_min_codec: str   # CODEC_* tag of winner
    S: float
    S_bin: int
    S_label: str
    R_zstd_long27_19: float  # compressed bpb
    R_bzip2_9: float
    R_zpaq_m5: float


def _run_zstd(data: bytes) -> int:
    """Compress data with zstd --long=27 -19 via stdin, return output size."""
    result = subprocess.run(
        ["zstd", "--long=27", "-19", "-q", "-f", "-T1", "-c", "-"],
        input=data,
        capture_output=True,
        check=True,
    )
    return len(result.stdout)


def _run_bzip2(data: bytes) -> int:
    """Compress data with bzip2 -9 via stdin, return output size."""
    result = subprocess.run(
        ["bzip2", "-9", "-c"],
        input=data,
        capture_output=True,
        check=True,
    )
    return len(result.stdout)


def _run_zpaq(path: Path) -> int:
    """Compress file at path with zpaq -m5, return archive size in bytes.

    zpaq cannot read stdin or write stdout; it always writes a named archive.
    The archive includes a ~40–80 byte journaling header; this is negligible
    for files ≥ 4 MB (the minimum v4 tier size).
    """
    with tempfile.NamedTemporaryFile(suffix=".zpaq", delete=False) as tf:
        archive_path = tf.name

    try:
        subprocess.run(
            ["zpaq", "add", archive_path, str(path), "-m5", "-t1"],
            capture_output=True,
            check=True,
        )
        return os.path.getsize(archive_path)
    finally:
        try:
            os.unlink(archive_path)
        except FileNotFoundError:
            pass


def measure_s(path: Path) -> SResult:
    """Measure structural compressibility S for the file at path.

    Runs all three reference codecs. The three subprocesses run sequentially
    (zpaq is single-threaded by design and serializes naturally; parallelizing
    across files at the caller level is more effective than within-file parallelism).

    Raises subprocess.CalledProcessError if any codec binary is missing or
    returns a non-zero exit code.
    """
    data = path.read_bytes()
    n = len(data)

    zstd_bytes = _run_zstd(data)
    bzip2_bytes = _run_bzip2(data)
    zpaq_bytes = _run_zpaq(path)

    min_bytes = min(zstd_bytes, bzip2_bytes, zpaq_bytes)
    if zstd_bytes == min_bytes:
        winner = CODEC_ZSTD
    elif bzip2_bytes == min_bytes:
        winner = CODEC_BZIP2
    else:
        winner = CODEC_ZPAQ

    S = 1.0 - min_bytes / n if n > 0 else 0.0

    def bpb(compressed: int) -> float:
        return (compressed * 8.0 / n) if n > 0 else 0.0

    return SResult(
        size_bytes=n,
        zstd_bytes=zstd_bytes,
        bzip2_bytes=bzip2_bytes,
        zpaq_bytes=zpaq_bytes,
        S_min_codec=winner,
        S=round(S, 6),
        S_bin=s_bin(S),
        S_label=s_label(S),
        R_zstd_long27_19=round(bpb(zstd_bytes), 4),
        R_bzip2_9=round(bpb(bzip2_bytes), 4),
        R_zpaq_m5=round(bpb(zpaq_bytes), 4),
    )
