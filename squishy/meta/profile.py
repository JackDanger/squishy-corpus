"""Compute per-source statistical profile for every file under cfg.raw_dir.

Metrics per file:
- size_uncompressed: exact byte count
- source_sha256: SHA-256 of raw bytes
- entropy_bits_per_byte: Shannon entropy from byte frequency histogram
- entropy_bigram: 2-gram conditional entropy
- lz_match_density_32k: fraction of 4-byte grams appearing earlier within 32 KiB
- compression_class: content category label
- representative_ratios: {gzip-9, zstd-3, zstd-19, lz4} actual ratios

Output: cfg.meta_dir/profile.json

Public interface: run(cfg: BuildConfig) -> int
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from squishy.core.config import BuildConfig
from squishy.core.fs import sha256_file, write_bytes_atomic

# ── compression class taxonomy ────────────────────────────────────────────────

# Manual overrides keyed by filename stem (no extension)
_MANUAL_SILESIA: dict[str, str] = {
    "dickens":  "natural-text",
    "mozilla":  "binary-executable",
    "mr":       "binary-media",
    "nci":      "structured-text",
    "ooffice":  "binary-executable",
    "osdb":     "structured-binary",
    "reymont":  "natural-text",
    "samba":    "source-code",
    "sao":      "binary-media",
    "webster":  "natural-text",
    "x-ray":    "binary-media",
    "xml":      "structured-text",
}

_MANUAL_SQUASH: dict[str, str] = {
    "bootstrap-3.3.6.min.css": "source-code",
    "eff.html":                 "natural-text",
    "jquery-2.1.4.min.js":     "source-code",
    "MG44-MathGuide.tar":       "binary-media",
    "random":                   "near-random",
    "zlib.wasm":                "binary-executable",
}

# Extension → class
_EXT_CLASS: dict[str, str] = {
    ".js":       "source-code",
    ".ts":       "source-code",
    ".css":      "source-code",
    ".py":       "source-code",
    ".rs":       "source-code",
    ".c":        "source-code",
    ".json":     "structured-text",
    ".ndjson":   "structured-text",
    ".csv":      "structured-text",
    ".xml":      "structured-text",
    ".log":      "structured-text",
    ".parquet":  "structured-binary",
    ".arrow":    "structured-binary",
    ".sqlite":   "structured-binary",
    ".msgpack":  "structured-binary",
    ".protobuf": "structured-binary",
    ".wasm":     "binary-executable",
    ".woff2":    "binary-media",
    ".ttf":      "binary-media",
    ".otf":      "binary-media",
}

# Name-substring → class for pathological set
_PATHO_NEAR_RANDOM = ("urandom", "random")
_PATHO_ALREADY_COMPRESSED = ("already-compressed-blob",)
_PATHO_CALIBRATED_SUBSTRINGS = (
    "zeros-", "repeat-", "ascii-", "empty-", "tiny-", "small-", "short-",
    "page-", "phrase-", "pi-digits",
)
_PATHO_ADVERSARIAL_SUBSTRINGS = (
    "thue-morse", "debruijn", "window-", "alternating", "mixed-entropy",
    "near-dup", "sparse", "onebyte", "lz4-block", "max-match",
    "dict-poison", "long-distance", "huffman-max", "entropy-oscillator",
    "literal-flood", "overlap-match",
)

# Calibrated set: all files → synthetic-calibrated
_CALIBRATED_SETS = {"calibrated"}

# ── compressor definitions ────────────────────────────────────────────────────

class _CompressorDef(NamedTuple):
    key: str
    cmd: list[str]  # {input} replaced with input path; stdout is compressed bytes


_COMPRESSORS: list[_CompressorDef] = [
    _CompressorDef("gzip-9",  ["gzip",  "-9", "-c", "{input}"]),
    _CompressorDef("zstd-3",  ["zstd",  "-3", "-T1", "-q", "-c", "{input}"]),
    _CompressorDef("zstd-19", ["zstd",  "-19", "-T1", "-q", "-c", "{input}"]),
    _CompressorDef("lz4",     ["lz4",   "-9", "-q", "-c", "{input}"]),
]

_MAX_SAMPLE_COMPRESS = 8 * 1024 * 1024   # 8 MiB
_MAX_SAMPLE_LZ = 4 * 1024 * 1024         # 4 MiB
_LZ_WINDOW = 32768
_LZ_GRAM = 4

# ── statistical functions ─────────────────────────────────────────────────────


def shannon_entropy(data: bytes) -> float:
    """Shannon entropy in bits per byte from byte frequency histogram."""
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    total = 0.0
    for c in counts:
        if c > 0:
            p = c / n
            total -= p * math.log2(p)
    return total


def entropy_bigram(data: bytes) -> float:
    """2-gram conditional entropy H(byte | prev_byte).

    Weighted average over all prev bytes of the conditional entropy of the
    distribution of next bytes given prev.
    """
    if len(data) < 2:
        return 0.0

    # transitions[prev][curr] = count
    transitions: list[list[int]] = [[0] * 256 for _ in range(256)]
    marginal = [0] * 256

    prev = data[0]
    for i in range(1, len(data)):
        curr = data[i]
        transitions[prev][curr] += 1
        marginal[prev] += 1
        prev = curr

    total_transitions = len(data) - 1
    h = 0.0
    for prev_b in range(256):
        m = marginal[prev_b]
        if m == 0:
            continue
        weight = m / total_transitions
        # conditional entropy of next byte given prev_b
        cond_h = 0.0
        row = transitions[prev_b]
        for c in row:
            if c > 0:
                p = c / m
                cond_h -= p * math.log2(p)
        h += weight * cond_h

    return h


def lz_match_density(data: bytes) -> float:
    """Fraction of 4-byte grams that appear earlier within a 32 KiB window.

    For files larger than 4 MiB, operates on the first 4 MiB.
    Returns the density and the sample size used.
    """
    if len(data) < _LZ_GRAM:
        return 0.0

    sample = data[:_MAX_SAMPLE_LZ]
    seen: dict[bytes, int] = {}
    matches = 0
    total = 0

    for i in range(len(sample) - _LZ_GRAM + 1):
        gram = sample[i:i + _LZ_GRAM]
        last_pos = seen.get(gram)
        if last_pos is not None and i - last_pos <= _LZ_WINDOW:
            matches += 1
        seen[gram] = i
        total += 1

    return matches / total if total > 0 else 0.0


# ── compression class assignment ──────────────────────────────────────────────


def assign_compression_class(
    file_path: Path,
    set_name: str,
    name: str,
    entropy: float,
) -> str:
    """Determine the compression_class for a source file."""

    # Manual overrides by set
    if set_name == "silesia":
        if name in _MANUAL_SILESIA:
            return _MANUAL_SILESIA[name]
    elif set_name == "squash":
        if name in _MANUAL_SQUASH:
            return _MANUAL_SQUASH[name]

    # Calibrated set
    if set_name in _CALIBRATED_SETS:
        return "synthetic-calibrated"

    # Logs set
    if set_name == "logs":
        if file_path.suffix == ".log" or file_path.suffix == ".ndjson":
            return "structured-text"

    # Pathological set name-based detection
    if set_name == "pathological":
        for sub in _PATHO_NEAR_RANDOM:
            if sub in name:
                return "near-random"
        for sub in _PATHO_ALREADY_COMPRESSED:
            if sub in name:
                return "already-compressed"
        for sub in _PATHO_CALIBRATED_SUBSTRINGS:
            if name.startswith(sub) or sub in name:
                return "synthetic-calibrated"
        for sub in _PATHO_ADVERSARIAL_SUBSTRINGS:
            if sub in name:
                return "synthetic-adversarial"
        return "synthetic-calibrated"

    # Extension-based detection
    ext = file_path.suffix.lower()
    if ext in _EXT_CLASS:
        return _EXT_CLASS[ext]

    # Entropy-based fallback
    if entropy >= 7.8:
        return "near-random"
    if entropy >= 6.0:
        return "binary-media"
    if entropy >= 4.0:
        return "natural-text"
    return "structured-binary"


# ── compressor helpers ────────────────────────────────────────────────────────


def _check_tool_version(binary: str) -> str | None:
    """Return version string for a tool, or None if not on PATH."""
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        out = (result.stdout + result.stderr).strip()
        return out.split("\n")[0] if out else "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _probe_tools() -> dict[str, str]:
    """Return a dict of available compressor tool versions."""
    tools: dict[str, str] = {}
    binaries = {c.cmd[0] for c in _COMPRESSORS}
    for binary in sorted(binaries):
        ver = _check_tool_version(binary)
        if ver is not None:
            tools[binary] = ver
    return tools


def _available_compressors(tools: dict[str, str]) -> list[_CompressorDef]:
    return [c for c in _COMPRESSORS if c.cmd[0] in tools]


def _compress_sample(
    path: Path,
    sample_size: int,
    cmd_template: list[str],
) -> int | None:
    """Run compressor on path (or first sample_size bytes via stdin), return compressed length."""
    input_size = path.stat().st_size

    if input_size <= sample_size:
        cmd = [part.replace("{input}", str(path)) for part in cmd_template]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode == 0:
                return len(result.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None
    else:
        # Feed first sample_size bytes via stdin
        cmd = [part.replace("{input}", "-") for part in cmd_template]
        try:
            with path.open("rb") as fh:
                sample = fh.read(sample_size)
            result = subprocess.run(cmd, input=sample, capture_output=True, timeout=120)
            if result.returncode == 0:
                return len(result.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None


def _compute_ratios(
    path: Path,
    available: list[_CompressorDef],
) -> tuple[dict[str, float], int | None]:
    """Compute representative compression ratios.

    Returns (ratios_dict, sample_bytes) where sample_bytes is None if the full
    file was used, or the sample size if truncated.
    """
    input_size = path.stat().st_size
    sample_bytes: int | None = None
    if input_size > _MAX_SAMPLE_COMPRESS:
        sample_bytes = _MAX_SAMPLE_COMPRESS
        denominator = _MAX_SAMPLE_COMPRESS
    else:
        denominator = input_size

    ratios: dict[str, float] = {}
    for comp in available:
        compressed_len = _compress_sample(path, _MAX_SAMPLE_COMPRESS, comp.cmd)
        if compressed_len is not None and denominator > 0:
            ratios[comp.key] = compressed_len / denominator

    return ratios, sample_bytes


# ── per-file profiling ────────────────────────────────────────────────────────


def _profile_file(
    path: Path,
    set_name: str,
    available_compressors: list[_CompressorDef],
) -> dict:
    """Compute the full profile for a single raw source file."""
    name = path.name
    size = path.stat().st_size

    digest = sha256_file(path)

    # Read data for entropy / LZ computations
    read_limit = max(_MAX_SAMPLE_LZ, _MAX_SAMPLE_COMPRESS)
    with path.open("rb") as fh:
        data = fh.read(read_limit)

    entropy = shannon_entropy(data[:_MAX_SAMPLE_LZ] if len(data) > _MAX_SAMPLE_LZ else data)
    bigram = entropy_bigram(data[:_MAX_SAMPLE_LZ] if len(data) > _MAX_SAMPLE_LZ else data)
    lz_density = lz_match_density(data)

    comp_class = assign_compression_class(path, set_name, name, entropy)
    ratios, sample_bytes = _compute_ratios(path, available_compressors)

    entry: dict = {
        "size_uncompressed":      size,
        "source_sha256":          digest,
        "entropy_bits_per_byte":  round(entropy, 6),
        "entropy_bigram":         round(bigram, 6),
        "lz_match_density_32k":   round(lz_density, 6),
        "compression_class":      comp_class,
        "representative_ratios":  ratios,
    }
    if sample_bytes is not None:
        entry["sample_bytes"] = sample_bytes
    if size > _MAX_SAMPLE_LZ:
        entry["sample_size"] = _MAX_SAMPLE_LZ

    return entry


# ── main entry point ──────────────────────────────────────────────────────────


def run(cfg: BuildConfig) -> int:
    """Profile all raw source files and write profile.json."""
    raw_dir = cfg.raw_dir
    meta_dir = cfg.meta_dir

    if not raw_dir.exists():
        print(f"profile: raw_dir {raw_dir} does not exist, skipping", file=sys.stderr)
        return 0

    meta_dir.mkdir(parents=True, exist_ok=True)

    tools = _probe_tools()
    available = _available_compressors(tools)

    if not available:
        print("profile: warning — no compressors found on PATH; representative_ratios will be empty", file=sys.stderr)

    sources: dict[str, dict] = {}
    errors = 0

    for set_dir in sorted(raw_dir.iterdir()):
        if not set_dir.is_dir():
            continue
        set_name = set_dir.name

        for file_path in sorted(set_dir.iterdir()):
            if not file_path.is_file():
                continue
            source_key = f"{set_name}/{file_path.name}"
            try:
                entry = _profile_file(file_path, set_name, available)
                sources[source_key] = entry
                print(
                    f"  profile: {source_key}  "
                    f"entropy={entry['entropy_bits_per_byte']:.3f}  "
                    f"class={entry['compression_class']}",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(f"profile: error processing {source_key}: {exc}", file=sys.stderr)
                errors += 1

    profile_data = {
        "version":      1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tools":        tools,
        "sources":      sources,
    }

    write_bytes_atomic(
        meta_dir / "profile.json",
        (__import__("json").dumps(profile_data, indent=2) + "\n").encode(),
    )

    print(
        f"profile: {len(sources)} sources profiled, {errors} errors",
        file=sys.stderr,
    )
    return 0 if errors == 0 else 1
