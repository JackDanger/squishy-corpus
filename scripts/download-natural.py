#!/usr/bin/env python3
"""Download and prepare natural corpus files for calibrated-corpus cell validation.

Each source is a self-contained object with a run() method that handles
both download and extraction. Adding a new source means adding a Source
subclass and an entry in SOURCES — main() never changes.

Files produced in build/raw/natural/<key>/
  mnist/        H≈1.9,   M≈0 (norm)   — MNIST pixel bytes (IID floor collapses M_norm)
  gharchive/    H≈5.3,   M≈0.91       — GitHub event NDJSON
  genomic/      H≈2.2,   M≈0.99       — human chrY alpha-satellite tandem repeats
  video/        H≈7.1,   M≈0.44       — Xiph CIF video luma plane
  neural/       H≈7.3,   M≈0          — BERT float32 weight bytes
  ecg/          H≈4.5-5.5, M≈0.2-0.4 — MIT-BIH ECG Format-212 signal
  vcf/          H≈1.7,   M≈0.85+      — 1000 Genomes chr22 GT columns
  cifar10/      H≈7.5+,  M≈0.15-0.35  — raw 32×32 RGB pixel bytes
  cpython/      H≈4.5-5.3, M≈0.35-0.55 — CPython .c source files concatenated
  librispeech/  H≈6-7,   M≈0.05-0.25  — LibriSpeech 16kHz 16-bit speech PCM
  gutenberg/    H≈4.0-4.3, M≈0.25-0.40 — Project Gutenberg plain-ASCII (Shakespeare)
  video_4cif/   H≈6.5-7.3, M≈0.25-0.40 — Xiph 4CIF (704×576) luma, more inter-frame match
  genomic_arm/  H≈1.9-2.2, M≈0.10-0.40 — chr1 arm (SINEs/LINEs, less repetitive than centromere)
  genomic_micro/ H≈1.6-1.9, M≈0.40-0.70 — chr2 pericentromeric (short tandem repeats)
  wikisql/      H≈4.5-5.5, M≈0.40-0.60 — Wikipedia SQL dump (repeated INSERT structure)
  uniprot/      H≈4.0-4.3, M≈0.40-0.60 — UniProt SwissProt amino acid sequences
  jpeg_scan/    H≈7.5+,    M≈0          — JPEG entropy-coded scan bytes (NASA Blue Marble)
  parkrun/      H≈6.5-7.2, M≈0.20-0.40 — Xiph park_joy 1080p50 luma
  mp3_audio/    H≈7.0-7.5, M≈0.05-0.15 — MP3 audio bitstream (LibriVox)
  wiki_xml/     H≈4.5-5.5, M≈0.40-0.60 — Wikipedia pages-articles XML (bz2 dump)
  arm64_binary/ H≈5.5-6.5, M≈0.20-0.40 — ARM64 machine code (.text section from clang)
  video_1080p/  H≈6.5-7.2, M≈0.40-0.60 — Xiph in_to_tree 1080p50 luma
  gencode_gff3/ H≈4.5-5.5, M≈0.60-0.80 — GENCODE human genome annotation GFF3
  noaa_gsod/    H≈5.0-6.0, M≈0.40-0.60 — NOAA Global Surface Summary of the Day CSV
  pdb_coords/   H≈3.5-4.0, M≈0.80+      — RCSB PDB mmCIF ATOM/HETATM coordinate records
  repcorpus_cere/    H≈1.9, M0.80+     — Pizza-Chili yeast genome (highly repetitive)
  repcorpus_einstein/ H≈3.5-4.5, M0.80+ — Pizza-Chili German Wikipedia revision history
  t2t_alphasat/ H≈1.5-2.0, M0.80+     — T2T-CHM13 chrY DYZ3 HOR alpha-satellite array
  osm_geofabrik/ H≈4.5-5.5, M≈0.20-0.40 — OpenStreetMap XML (Latvia extract)
  repcorpus_cere_straddle/ H≈1.9-2.3, M0.80+, L-long — cere genome-boundary straddle
  gencode_gtf/   H≈4.0-4.4, M≈0.80+, L-long — GENCODE GTF (compact attribute syntax)
  gutenberg_multi/ H≈4.5-5.0, M≈0.40-0.60, L-short — 8 different 19th-c Gutenberg authors
  uniref50/      H≈4.2-4.4, M≈0.60-0.80, L-short — UniRef50 protein cluster sequences
  t2t_hsat2/     H≈1.0-1.4, M0.99, L-medium — T2T-CHM13 chr1 HSat2 array
  t2t_gsat/      H≈1.0-1.5, M0.99, L-long — T2T-CHM13 chr13 GSAT array
  plasmodium/    H≈1.7-2.3, M0.80+, L-short — P. falciparum chr14 var-gene repeats
  repcorpus_ecoli/ H≈2.0, M0.99, L-long — E. coli rRNA operon repeats (5 kb, 7 copies/genome)
  repcorpus_influenza/ H≈2.0-2.5, M0.99, L-long — influenza concatenation (near-identical strains)
  pdb_nmr/       H≈3.5-4.0, M0.99, L-medium — NMR ensemble mmCIF (2K2E, 20 models, L_p90≈17)
  ecoli_k12/     H≈2.0, M0.99, L-long — E. coli K-12 chr (7 rRNA operons, close together)
  dblp_xml/      H≈4.3-4.5, M≈0.90-0.95, L-long — DBLP bibliography XML; repeated tag structure + diverse author/title text
  ncbi_taxonomy/ H≈3.8-4.2, M≈0.65-0.75, L-short — NCBI Taxonomy names.dmp; repeated name_class field + diverse taxon names
  opensubtitles/  H≈4.7-5.1, M≈0.35-0.55, L-short — OpenSubtitles English movie dialogue; diverse + short lines
  pfam_a/        H≈4.0-4.3, M≈0.60-0.75, L-short — Pfam-A domain FASTA; sequences grouped by family → conserved motifs

After running this script, measure with:
    uv run scripts/measure-corpus.py \\
        --dirs build/raw/natural \\
        --out build/bench/natural-measurements.csv

Usage:
    uv run scripts/download-natural.py
    uv run scripts/download-natural.py --only ecg vcf
    uv run scripts/download-natural.py --list
"""
from __future__ import annotations

import argparse
import bz2
import gzip
import struct
import subprocess
import sys
import tarfile as _tf
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEST_BASE = ROOT / "build" / "raw" / "natural"

_CHUNK = 1 << 20  # 1 MiB
_4M = 4 * 1024 * 1024
_256K = 256 * 1024


# ---------------------------------------------------------------------------
# Download / IO helpers
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path, byte_range: tuple[int, int] | None = None,
              skip_existing: bool = True) -> bool:
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.relative_to(ROOT)}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    headers: dict[str, str] = {"User-Agent": "squishy-corpus/2.0"}
    if byte_range:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    print(f"    ↓ {url[:80]}{'…' if len(url) > 80 else ''}")
    if byte_range:
        print(f"      Range: bytes={byte_range[0]}-{byte_range[1]}")
    print(f"      → {dest.relative_to(ROOT)}", flush=True)
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            t0 = time.monotonic()
            with open(tmp, "wb") as f:
                while chunk := resp.read(_CHUNK):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        elapsed = time.monotonic() - t0
                        rate = downloaded / elapsed / 1e6 if elapsed > 0.1 else 0
                        print(f"\r      {pct:3d}%  {downloaded/1e6:.1f}/{total/1e6:.1f} MB"
                              f"  {rate:.1f} MB/s", end="", flush=True)
        print()
        tmp.rename(dest)
        print(f"      {dest.stat().st_size/1e6:.2f} MB written")
        return True
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        print(f"\n    ERROR downloading {url[:60]}: {e}", file=sys.stderr)
        return False


def _download_partial(url: str, dest: Path, max_bytes: int,
                      skip_existing: bool = True) -> bool:
    """Download at most max_bytes from url (early-stop streaming)."""
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.relative_to(ROOT)}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"    ↓ (first {max_bytes//1024//1024} MB) {url[:70]}…")
    print(f"      → {dest.relative_to(ROOT)}", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "squishy-corpus/2.0"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            downloaded = 0
            with open(tmp, "wb") as f:
                while downloaded < max_bytes:
                    chunk = resp.read(min(_CHUNK, max_bytes - downloaded))
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    print(f"\r      {downloaded/1e6:.1f} MB", end="", flush=True)
        print()
        tmp.rename(dest)
        print(f"      {dest.stat().st_size/1e6:.2f} MB written")
        return True
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        print(f"\n    ERROR: {e}", file=sys.stderr)
        return False


def _decompress_gz(src: Path, dest: Path, skip_existing: bool = True) -> bool:
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.name}")
        return True
    print(f"    decompress → {dest.name}", flush=True)
    try:
        with gzip.open(src, "rb") as f_in, open(dest, "wb") as f_out:
            written = 0
            while chunk := f_in.read(_CHUNK):
                f_out.write(chunk)
                written += len(chunk)
                print(f"\r      {written/1e6:.1f} MB", end="", flush=True)
        print(f"\r      {written/1e6:.2f} MB decompressed   ")
        return True
    except Exception as e:
        print(f"\n    ERROR decompressing {src.name}: {e}", file=sys.stderr)
        if dest.exists():
            dest.unlink()
        return False


def _write_slice(src: Path, dest: Path, start: int, length: int,
                 skip_existing: bool = True) -> bool:
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.name}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(src, "rb") as f:
            f.seek(start)
            data = f.read(length)
        dest.write_bytes(data)
        print(f"    slice [{start}:{start+length}] → {dest.name}  ({len(data)/1024:.0f} KiB)")
        return True
    except Exception as e:
        print(f"    ERROR slicing: {e}", file=sys.stderr)
        return False


def _skip_if_all_exist(paths: list[Path]) -> bool:
    return all(p.exists() for p in paths)


# ---------------------------------------------------------------------------
# Source base class
# ---------------------------------------------------------------------------

@dataclass
class Source:
    key: str
    license: str
    note: str

    def run(self, dest_dir: Path, *, force: bool) -> bool:
        dest_dir.mkdir(parents=True, exist_ok=True)
        return self._run(dest_dir, skip_existing=not force)

    def _run(self, dest_dir: Path, skip_existing: bool) -> bool:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Concrete source types
# ---------------------------------------------------------------------------

@dataclass
class GzSource(Source):
    """Single gzipped URL → decompress → skip header → write slices."""
    url: str
    gz_name: str
    raw_name: str
    header_skip: int = 0
    slices: list[tuple[str, int, int]] = field(default_factory=list)
    # (out_name, offset_within_payload, length)

    def _run(self, d: Path, skip_existing: bool) -> bool:
        gz = d / self.gz_name
        raw = d / self.raw_name
        if not _download(self.url, gz, skip_existing=skip_existing):
            return False
        if not _decompress_gz(gz, raw, skip_existing=skip_existing):
            return False
        ok = all(
            _write_slice(raw, d / name, self.header_skip + off, length, skip_existing)
            for name, off, length in self.slices
        )
        if ok and raw.exists():
            raw.unlink()
            print(f"    removed {raw.name}")
        return ok


@dataclass
class PlainSource(Source):
    """Download a plain (non-compressed) URL → write slices directly."""
    url: str
    raw_name: str
    slices: list[tuple[str, int, int]] = field(default_factory=list)

    def _run(self, d: Path, skip_existing: bool) -> bool:
        out_paths = [d / name for name, _, _ in self.slices]
        if _skip_if_all_exist(out_paths) and skip_existing:
            for p in out_paths:
                print(f"    skip (exists): {p.name}")
            return True
        raw = d / self.raw_name
        if not _download(self.url, raw, skip_existing=skip_existing):
            return False
        size = raw.stat().st_size
        ok = all(
            _write_slice(raw, d / name, off, min(length, size - off), skip_existing)
            for name, off, length in self.slices
            if size > off
        )
        raw.unlink(missing_ok=True)
        return ok


@dataclass
class RangeSource(Source):
    """HTTP byte-range request → custom extractor callable."""
    url: str
    raw_name: str
    byte_range: tuple[int, int]
    extractor: object = None  # callable(raw: Path, d: Path, skip: bool) -> bool

    def _run(self, d: Path, skip_existing: bool) -> bool:
        raw = d / self.raw_name
        if not _download(self.url, raw, byte_range=self.byte_range,
                         skip_existing=skip_existing):
            return False
        return self.extractor(raw, d, skip_existing)  # type: ignore[call-arg]


@dataclass
class FastaSource(Source):
    """Plain-text FASTA URL → strip headers → write slices."""
    url: str
    fasta_name: str
    nt_name: str
    slices: list[tuple[str, int, int]] = field(default_factory=list)

    def _run(self, d: Path, skip_existing: bool) -> bool:
        fasta = d / self.fasta_name
        nt = d / self.nt_name
        if not _download(self.url, fasta, skip_existing=skip_existing):
            return False
        if not _fasta_to_nt(fasta, nt, skip_existing):
            return False
        size = nt.stat().st_size
        return all(
            _write_slice(nt, d / name, off, min(length, size - off), skip_existing)
            for name, off, length in self.slices
            if size > off
        )


@dataclass
class Y4MSource(Source):
    """Y4M video URL → extract luma plane → write slices."""
    url: str
    y4m_name: str
    prefix: str  # output filename prefix (e.g. "foreman-cif")
    partial_bytes: int = 0  # 0 = full download; >0 stops streaming after N bytes

    def _run(self, d: Path, skip_existing: bool) -> bool:
        y4m = d / self.y4m_name
        if self.partial_bytes:
            if not _download_partial(self.url, y4m, self.partial_bytes,
                                     skip_existing=skip_existing):
                return False
        else:
            if not _download(self.url, y4m, skip_existing=skip_existing):
                return False
        return _y4m_luma(y4m, d / self.prefix, skip_existing)


@dataclass
class MultiFileSource(Source):
    """Download multiple files, concatenate, write slices."""
    urls: list[str]
    file_names: list[str]
    slices: list[tuple[str, int, int]] = field(default_factory=list)

    def _run(self, d: Path, skip_existing: bool) -> bool:
        parts: list[Path] = []
        for url, name in zip(self.urls, self.file_names):
            dest = d / name
            if not _download(url, dest, skip_existing=skip_existing):
                return False
            parts.append(dest)

        cat = d / "_concat.bin"
        if not cat.exists() or not skip_existing:
            with open(cat, "wb") as out:
                for p in parts:
                    out.write(p.read_bytes())

        ok = all(
            _write_slice(cat, d / name, off, length, skip_existing)
            for name, off, length in self.slices
        )
        for p in parts + [cat]:
            if p.exists():
                p.unlink()
        return ok


@dataclass
class TarConcatSource(Source):
    """Stream-extract files matching a suffix from a tar archive, concatenate, slice.

    archive_mode: 'gz', 'bz2', or 'xz'
    partial_bytes: if >0, stop downloading after this many bytes (early-stop streaming)
    transform_fn: optional callable(bytes) -> bytes applied to each extracted file
    """
    url: str
    archive_mode: str
    partial_bytes: int
    file_suffix: str
    transform_fn: object = None
    slices: list[tuple[str, int, int]] = field(default_factory=list)

    def _run(self, d: Path, skip_existing: bool) -> bool:
        out_paths = [d / name for name, _, _ in self.slices]
        if _skip_if_all_exist(out_paths) and skip_existing:
            for p in out_paths:
                print(f"    skip (exists): {p.name}")
            return True

        ext = f".tar.{self.archive_mode}"
        archive = d / f"_archive{ext}"
        if self.partial_bytes:
            if not _download_partial(self.url, archive, self.partial_bytes,
                                     skip_existing=skip_existing):
                return False
            mode = f"r|{self.archive_mode}"  # streaming (tolerates truncation)
        else:
            if not _download(self.url, archive, skip_existing=skip_existing):
                return False
            mode = f"r:{self.archive_mode}"  # seekable

        buf = bytearray()
        needed = max(off + length for _, off, length in self.slices)
        files_read = 0
        try:
            with _tf.open(str(archive), mode=mode) as tar:
                for member in tar:
                    if not member.name.endswith(self.file_suffix):
                        continue
                    f = tar.extractfile(member)
                    if f is None:
                        continue
                    raw = f.read()
                    out = self.transform_fn(raw) if self.transform_fn else raw  # type: ignore
                    if out:
                        buf.extend(out)
                        files_read += 1
                    if len(buf) >= needed:
                        break
        except (_tf.TarError, EOFError, Exception) as e:
            min_needed = min(length for _, _, length in self.slices)
            if len(buf) < min_needed:
                print(f"    ERROR: {e} ({len(buf)} bytes collected)", file=sys.stderr)
                archive.unlink(missing_ok=True)
                return False
            print(f"    (archive ended after {files_read} files: {type(e).__name__})")

        archive.unlink(missing_ok=True)
        print(f"    extracted {files_read} {self.file_suffix} files → {len(buf)/1e6:.2f} MB")
        data = bytes(buf)
        for name, off, length in self.slices:
            dest = d / name
            avail = max(0, len(data) - off)
            dest.write_bytes(data[off: off + length])
            print(f"    → {dest.name}  ({min(length, avail)//1024} KiB)")
        return True


@dataclass
class FlacTarSource(Source):
    """Partially stream a tar.gz of FLAC files, decode to raw PCM, slice.

    Requires the 'flac' CLI to be installed.
    Produces signed 16-bit little-endian raw PCM (no WAV header).
    """
    url: str
    partial_bytes: int
    slices: list[tuple[str, int, int]] = field(default_factory=list)

    def _run(self, d: Path, skip_existing: bool) -> bool:
        out_paths = [d / name for name, _, _ in self.slices]
        if _skip_if_all_exist(out_paths) and skip_existing:
            for p in out_paths:
                print(f"    skip (exists): {p.name}")
            return True

        archive = d / "_archive.tar.gz"
        if not _download_partial(self.url, archive, self.partial_bytes,
                                 skip_existing=skip_existing):
            return False

        buf = bytearray()
        needed = max(off + length for _, off, length in self.slices)
        files_decoded = 0

        try:
            with _tf.open(str(archive), mode="r|gz") as tar:
                for member in tar:
                    if not member.name.endswith(".flac"):
                        continue
                    f = tar.extractfile(member)
                    if f is None:
                        continue
                    flac_data = f.read()
                    result = subprocess.run(
                        ["flac", "--decode", "--force-raw-format",
                         "--endian=little", "--sign=signed",
                         "--stdout", "--silent", "-"],
                        input=flac_data, capture_output=True, timeout=30,
                    )
                    if result.returncode == 0:
                        buf.extend(result.stdout)
                        files_decoded += 1
                        print(f"\r      {len(buf)/1e6:.2f} MB PCM ({files_decoded} files)",
                              end="", flush=True)
                    if len(buf) >= needed:
                        break
        except (_tf.TarError, EOFError, Exception) as e:
            min_needed = min(length for _, _, length in self.slices)
            if len(buf) < min_needed:
                print(f"\n    ERROR: {e} ({len(buf)} bytes)", file=sys.stderr)
                archive.unlink(missing_ok=True)
                return False
            print(f"\n    (archive ended: {type(e).__name__})")

        archive.unlink(missing_ok=True)
        print(f"\n    decoded {files_decoded} FLAC → {len(buf)/1e6:.2f} MB PCM")
        data = bytes(buf)
        for name, off, length in self.slices:
            dest = d / name
            avail = max(0, len(data) - off)
            dest.write_bytes(data[off: off + length])
            print(f"    → {dest.name}  ({min(length, avail)//1024} KiB)")
        return True


@dataclass
class StreamingGzLineSource(Source):
    """Stream a gzipped or bz2-compressed file, apply a line filter, stop when buffer full."""
    url: str
    partial_name: str
    partial_bytes: int
    line_fn: object  # callable(line: bytes) -> bytes | None
    slices: list[tuple[str, int, int]] = field(default_factory=list)
    add_newline: bool = True  # whether to append \n after each filtered line
    decompressor: str = "gz"  # 'gz' or 'bz2'

    def _run(self, d: Path, skip_existing: bool) -> bool:
        out_paths = [d / name for name, _, _ in self.slices]
        if _skip_if_all_exist(out_paths) and skip_existing:
            for p in out_paths:
                print(f"    skip (exists): {p.name}")
            return True

        partial = d / self.partial_name
        if not _download_partial(self.url, partial, self.partial_bytes,
                                 skip_existing=skip_existing):
            return False

        buf = bytearray()
        needed = max(off + length for _, off, length in self.slices)
        try:
            if self.decompressor == "bz2":
                fobj = bz2.open(str(partial), "rb")
            else:
                fobj = gzip.open(str(partial), "rb")
            with fobj as gz:
                for raw_line in gz:
                    out = self.line_fn(raw_line.rstrip(b"\n"))  # type: ignore[call-arg]
                    if out is not None:
                        buf += out
                        if self.add_newline:
                            buf += b"\n"
                    if len(buf) >= needed:
                        break
        except (EOFError, gzip.BadGzipFile, OSError):
            pass  # expected for partial download
        except Exception as e:
            print(f"    ERROR decompressing: {e}", file=sys.stderr)

        partial.unlink(missing_ok=True)

        if len(buf) < _256K:
            print(f"    ERROR: only {len(buf)} bytes collected", file=sys.stderr)
            return False

        data = bytes(buf)
        for name, off, length in self.slices:
            dest = d / name
            dest.write_bytes(data[off: off + length])
            print(f"    → {dest.name}  ({min(length, len(data)-off)//1024} KiB)")
        return True


@dataclass
class PartialGzSliceSource(Source):
    """Download first N bytes of a raw .gz file, decompress (tolerates truncation), write slices.

    Unlike StreamingGzLineSource, this works for non-line-oriented binary / nucleotide files.
    """
    url: str
    partial_name: str
    partial_bytes: int
    slices: list[tuple[str, int, int]] = field(default_factory=list)

    def _run(self, d: Path, skip_existing: bool) -> bool:
        out_paths = [d / name for name, _, _ in self.slices]
        if _skip_if_all_exist(out_paths) and skip_existing:
            for p in out_paths:
                print(f"    skip (exists): {p.name}")
            return True

        partial = d / self.partial_name
        if not _download_partial(self.url, partial, self.partial_bytes,
                                 skip_existing=skip_existing):
            return False

        needed = max(off + length for _, off, length in self.slices)
        buf = bytearray()
        try:
            with gzip.open(str(partial), "rb") as gz:
                while len(buf) < needed:
                    chunk = gz.read(min(_CHUNK, needed - len(buf)))
                    if not chunk:
                        break
                    buf.extend(chunk)
                    print(f"\r      {len(buf)/1e6:.1f} MB decompressed", end="", flush=True)
        except (EOFError, gzip.BadGzipFile, OSError):
            pass  # expected for partial compressed download
        print()
        partial.unlink(missing_ok=True)

        if len(buf) < _256K:
            print(f"    ERROR: only {len(buf)} bytes extracted", file=sys.stderr)
            return False

        data = bytes(buf)
        print(f"    extracted {len(data)/1e6:.2f} MB from partial gzip")
        for name, off, length in self.slices:
            dest = d / name
            avail = max(0, len(data) - off)
            dest.write_bytes(data[off: off + min(length, avail)])
            print(f"    → {dest.name}  ({min(length, avail)//1024} KiB)")
        return True


@dataclass
class JpegScanSource(Source):
    """Download a JPEG, extract entropy-coded scan bytes, write slices."""
    url: str
    jpeg_name: str
    slices: list[tuple[str, int, int]] = field(default_factory=list)

    def _run(self, d: Path, skip_existing: bool) -> bool:
        out_paths = [d / name for name, _, _ in self.slices]
        if _skip_if_all_exist(out_paths) and skip_existing:
            for p in out_paths:
                print(f"    skip (exists): {p.name}")
            return True

        jpeg = d / self.jpeg_name
        if not _download(self.url, jpeg, skip_existing=skip_existing):
            return False

        data = _extract_jpeg_scan(jpeg.read_bytes())
        jpeg.unlink(missing_ok=True)

        if len(data) < _256K:
            print(f"    ERROR: only {len(data)} scan bytes extracted", file=sys.stderr)
            return False

        print(f"    JPEG scan: {len(data)/1e6:.2f} MB entropy-coded bytes")
        for name, off, length in self.slices:
            dest = d / name
            avail = max(0, len(data) - off)
            if avail > 0:
                dest.write_bytes(data[off: off + min(length, avail)])
                print(f"    → {dest.name}  ({min(length, avail)//1024} KiB)")
        return True


@dataclass
class LocalBinarySource(Source):
    """Read slices from a local file (no download required)."""
    src_path: str  # absolute path
    slices: list[tuple[str, int, int]] = field(default_factory=list)
    # (out_name, offset_within_src, length)

    def _run(self, d: Path, skip_existing: bool) -> bool:
        src = Path(self.src_path)
        if not src.exists():
            print(f"    ERROR: {src} not found", file=sys.stderr)
            return False

        out_paths = [d / name for name, _, _ in self.slices]
        if _skip_if_all_exist(out_paths) and skip_existing:
            for p in out_paths:
                print(f"    skip (exists): {p.name}")
            return True

        return all(
            _write_slice(src, d / name, off, length, skip_existing)
            for name, off, length in self.slices
        )


# ---------------------------------------------------------------------------
# Extraction helpers (used by specific sources)
# ---------------------------------------------------------------------------

def _fasta_to_nt(src: Path, dest: Path, skip_existing: bool = True) -> bool:
    if dest.exists() and skip_existing:
        print(f"    skip (exists): {dest.name}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(src, "rb") as f_in, open(dest, "wb") as f_out:
        for line in f_in:
            stripped = line.strip()
            if stripped.startswith(b">") or stripped.startswith(b";"):
                continue
            f_out.write(stripped.upper())
            written += len(stripped)
    print(f"    fasta_strip → {dest.name}  ({written/1e6:.2f} MB nucleotide bytes)")
    return True


def _y4m_luma(src: Path, dest_prefix: Path, skip_existing: bool = True) -> bool:
    dest_4m = Path(str(dest_prefix) + "-luma-4M.bin")
    dest_256k = Path(str(dest_prefix) + "-luma-256K.bin")
    if dest_4m.exists() and dest_256k.exists() and skip_existing:
        print(f"    skip (exists): {dest_4m.name}, {dest_256k.name}")
        return True
    dest_4m.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as f:
        header_line = f.readline().decode("ascii", errors="replace")
        if not header_line.startswith("YUV4MPEG2"):
            print(f"    ERROR: not a Y4M file", file=sys.stderr)
            return False
        w = h = 0
        for token in header_line.split():
            if token.startswith("W"):
                w = int(token[1:])
            elif token.startswith("H"):
                h = int(token[1:])
        y_size = w * h
        uv_size = (w // 2) * (h // 2) * 2
        luma_buf = bytearray()
        frames = 0
        while len(luma_buf) < _4M:
            tag = f.read(5)
            if len(tag) < 5 or tag != b"FRAME":
                break
            f.readline()
            y_data = f.read(y_size)
            if len(y_data) < y_size:
                break
            luma_buf.extend(y_data)
            f.read(uv_size)
            frames += 1
    print(f"    y4m_luma: {frames} frames, {w}×{h}, {len(luma_buf)/1e6:.2f} MB luma")
    dest_4m.write_bytes(bytes(luma_buf[:_4M]))
    dest_256k.write_bytes(bytes(luma_buf[:_256K]))
    print(f"    → {dest_4m.name}, {dest_256k.name}")
    return True


def _safetensors_extract(raw: Path, d: Path, skip_existing: bool) -> bool:
    dest_4m = d / "bert-base-float32-4M.bin"
    dest_256k = d / "bert-base-float32-256K.bin"
    if dest_4m.exists() and dest_256k.exists() and skip_existing:
        print(f"    skip (exists): {dest_4m.name}, {dest_256k.name}")
        return True
    data = raw.read_bytes()
    header_size = struct.unpack_from("<Q", data, 0)[0]
    weight_start = 8 + header_size
    weights = data[weight_start:]
    print(f"    safetensors: header={header_size}B, weights={len(weights)/1e6:.2f} MB")
    dest_4m.write_bytes(weights[:_4M])
    dest_256k.write_bytes(weights[:_256K])
    print(f"    → {dest_4m.name}, {dest_256k.name}")
    return True


def _vcf_gt_line(line: bytes) -> bytes | None:
    """Extract sample columns (GT only) from a VCF data line."""
    if not line or line[0] == ord("#"):
        return None
    pos = 0
    for _ in range(9):  # skip CHROM POS ID REF ALT QUAL FILTER INFO FORMAT
        nxt = line.find(b"\t", pos)
        if nxt < 0:
            return None
        pos = nxt + 1
    return line[pos:] if pos < len(line) else None


def _fasta_seq_line(line: bytes) -> bytes | None:
    """Return amino-acid/nucleotide sequence lines from FASTA, skip header lines."""
    if not line or line[0] == ord(">"):
        return None
    return line.upper()


def _sql_data_line(line: bytes) -> bytes | None:
    """Keep only INSERT INTO lines from a MySQL/MariaDB SQL dump."""
    if not line or line[0] in (ord("-"), ord("/"), ord("!")):
        return None
    if line.startswith(b"INSERT INTO"):
        return line
    return None


def _gff3_data_line(line: bytes) -> bytes | None:
    """Keep data lines from GFF3 annotation; skip pragma/comment lines."""
    if not line or line[0] == ord("#"):
        return None
    return line


def _pdb_atom_line(line: bytes) -> bytes | None:
    """Keep ATOM and HETATM coordinate records from PDB format files."""
    if line.startswith(b"ATOM  ") or line.startswith(b"HETATM"):
        return line
    return None


def _mmcif_atom_line(line: bytes) -> bytes | None:
    """Keep ATOM/HETATM data rows from mmCIF _atom_site loop (start with 'ATOM ' or 'HETATM ')."""
    if line.startswith(b"ATOM ") or line.startswith(b"HETATM "):
        return line
    return None


def _osm_data_line(line: bytes) -> bytes | None:
    """Pass through non-empty lines from OSM XML (skip blank lines only)."""
    stripped = line.strip()
    return stripped if stripped else None


def _xml_keep_line(line: bytes) -> bytes | None:
    """Pass through non-empty lines from an XML dump."""
    stripped = line.strip()
    return stripped if stripped else None


def _extract_jpeg_scan(data: bytes) -> bytes:
    """Extract entropy-coded scan bytes from a JPEG (bytes after the first SOS segment header).

    For high-quality photos the scan is near-random (H≈7.5+) and dominates file size.
    """
    pos = data.find(b"\xff\xda")  # SOS marker
    if pos < 0:
        return b""
    seg_len = int.from_bytes(data[pos + 2: pos + 4], "big")
    scan_start = pos + 2 + seg_len
    eoi = data.rfind(b"\xff\xd9")  # EOI marker
    if eoi > scan_start:
        return data[scan_start:eoi]
    return data[scan_start:]


def _cifar_pixels(data: bytes) -> bytes:
    """Extract pixel bytes from a CIFAR-10 binary batch (skip 1-byte label per image)."""
    # Each image: 1 label byte (0-9) + 3072 pixel bytes (32×32 RGB) = 3073 bytes total
    pixels = bytearray()
    i = 0
    while i + 3073 <= len(data):
        pixels.extend(data[i + 1: i + 3073])
        i += 3073
    return bytes(pixels)


def _wav_pcm(data: bytes) -> bytes:
    """Extract raw PCM payload from a RIFF WAV file (find 'data' chunk)."""
    pos = data.find(b"data")
    if pos < 0:
        return b""
    return data[pos + 8:]  # skip "data" fourcc + 4-byte chunk size


# ---------------------------------------------------------------------------
# Source catalogue
# ---------------------------------------------------------------------------

_MITBIH_BASE = "https://physionet.org/files/mitdb/1.0.0"

SOURCES: list[Source] = [
    GzSource(
        key="mnist",
        license="CC-BY-SA (Yann LeCun, Corinna Cortes, Christopher Burges)",
        note="H≈1.9, M≈0 (norm) — 60k MNIST training image pixels",
        url="https://storage.googleapis.com/cvdf-datasets/mnist/train-images-idx3-ubyte.gz",
        gz_name="train-images.gz",
        raw_name="train-images-idx3-ubyte",
        header_skip=16,  # IDX3 header: magic + #images + rows + cols
        slices=[
            ("mnist-train-4M.bin",  0, _4M),
            ("mnist-train-256K.bin", 0, _256K),
        ],
    ),
    GzSource(
        key="gharchive",
        license="CC-BY (GitHub Archive, github.com/igrigorik/gharchive.org)",
        note="H≈5.3, M≈0.91 — GitHub event NDJSON with repeated keys and URL prefixes",
        url="https://data.gharchive.org/2024-01-01-12.json.gz",
        gz_name="2024-01-01-12.json.gz",
        raw_name="2024-01-01-12.json",
        slices=[
            ("gharchive-4M.bin",  0, _4M),
            ("gharchive-256K.bin", 0, _256K),
        ],
    ),
    FastaSource(
        key="genomic",
        license="PD-US-gov (NCBI GRCh38 human reference genome)",
        note="H≈2.2, M≈0.99 — 171 bp alpha-satellite tandem repeats, 4-symbol alphabet",
        url=(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=nuccore&id=NC_000024.10&seq_start=10000001&seq_stop=14200000"
            "&rettype=fasta&retmode=text"
        ),
        fasta_name="grch38-chry-alphasat.fasta",
        nt_name="grch38-chry-alphasat.nt",
        slices=[
            ("grch38-chry-alphasat-4M.bin",  0, _4M),
            ("grch38-chry-alphasat-256K.bin", 0, _256K),
        ],
    ),
    Y4MSource(
        key="video",
        license="xiph.org test media (freely redistributable)",
        note="H≈7.1, M≈0.44 — CIF 352×288 video, luma plane only",
        url="https://media.xiph.org/video/derf/y4m/foreman_cif.y4m",
        y4m_name="foreman_cif.y4m",
        prefix="foreman-cif",
    ),
    RangeSource(
        key="bert",
        license="Apache-2.0 (Google BERT, huggingface.co/bert-base-uncased)",
        note="H≈7.3, M≈0 — float32 weights; period-4 exponent-byte clustering",
        url="https://huggingface.co/bert-base-uncased/resolve/main/model.safetensors",
        raw_name="bert-safetensors-head8M.bin",
        byte_range=(0, 8 * 1024 * 1024 - 1),
        extractor=_safetensors_extract,
    ),
    MultiFileSource(
        key="ecg",
        license="Open Data Commons Attribution License (MIT-BIH Arrhythmia DB, physionet.org/content/mitdb)",
        note="H≈4.5-5.5, M≈0.2-0.4 — raw Format-212 ECG bytes, 2-ch interleaved 12-bit samples",
        urls=[f"{_MITBIH_BASE}/{r}.dat" for r in ["100", "101", "102"]],
        file_names=[f"mitbih-{r}.dat" for r in ["100", "101", "102"]],
        slices=[
            ("mitbih-ecg-4M.bin",  0, _4M),
            ("mitbih-ecg-256K.bin", 0, _256K),
        ],
    ),
    StreamingGzLineSource(
        key="vcf",
        license="ODC-PDDL (1000 Genomes Project Phase 3, phase3_shapeit2_mvncall)",
        note="H≈1.7, M≈0.85+ — 2504-sample GT columns (0|0/0|1/1|1) per chr22 variant site",
        url=(
            "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/"
            "ALL.chr22.phase3_shapeit2_mvncall_integrated_v5b.20130502.genotypes.vcf.gz"
        ),
        partial_name="chr22.partial.vcf.gz",
        partial_bytes=20 * 1024 * 1024,  # 20 MB compressed → plenty of GT data
        line_fn=_vcf_gt_line,
        slices=[
            ("1kg-chr22-gt-4M.bin",  0, _4M),
            ("1kg-chr22-gt-256K.bin", 0, _256K),
        ],
    ),
    TarConcatSource(
        key="cifar10",
        license="Freely redistributable for research (CIFAR-10, cs.toronto.edu/~kriz/cifar.html)",
        note="H≈7.5+, M≈0.15-0.35 — raw 32×32 RGB pixel bytes, natural image spatial correlation",
        url="https://www.cs.toronto.edu/~kriz/cifar-10-binary.tar.gz",
        archive_mode="gz",
        partial_bytes=40 * 1024 * 1024,  # first batch is 30.73 MB; 40MB covers it fully
        file_suffix=".bin",
        transform_fn=_cifar_pixels,
        slices=[
            ("cifar10-pixels-4M.bin",  0, _4M),
            ("cifar10-pixels-256K.bin", 0, _256K),
        ],
    ),
    TarConcatSource(
        key="cpython",
        license="Python Software Foundation License v2 (PSF-2.0, python.org)",
        note="H≈4.5-5.3, M≈0.35-0.55 — CPython .c source files concatenated",
        url="https://www.python.org/ftp/python/3.13.0/Python-3.13.0.tar.xz",
        archive_mode="xz",
        partial_bytes=0,  # full download ~27 MB; xz streaming needs full file
        file_suffix=".c",
        transform_fn=None,
        slices=[
            ("cpython-source-4M.bin",  0, _4M),
            ("cpython-source-256K.bin", 0, _256K),
        ],
    ),
    FlacTarSource(
        key="librispeech",
        license="CC-BY 4.0 (LibriSpeech ASR corpus, openslr.org/12)",
        note="H≈6-7, M≈0.05-0.25 — 16kHz 16-bit mono speech PCM decoded from FLAC",
        url="https://www.openslr.org/resources/12/dev-clean.tar.gz",
        partial_bytes=25 * 1024 * 1024,  # first 25 MB covers 150+ utterances
        slices=[
            ("librispeech-pcm-4M.bin",  0, _4M),
            ("librispeech-pcm-256K.bin", 0, _256K),
        ],
    ),
    PlainSource(
        key="gutenberg",
        license="Public domain (Project Gutenberg, gutenberg.org)",
        note="H≈4.0-4.3, M≈0.25-0.40 — Shakespeare complete works, plain ASCII",
        url="https://www.gutenberg.org/cache/epub/100/pg100.txt",
        raw_name="shakespeare-complete.txt",
        slices=[
            ("gutenberg-shakespeare-4M.bin",   0, _4M),
            ("gutenberg-shakespeare-256K.bin",  0, _256K),
        ],
    ),
    Y4MSource(
        key="video_4cif",
        license="xiph.org test media (freely redistributable)",
        note="H≈6.5-7.3, M≈0.25-0.40 — 4CIF 704×576 luma; larger frames → longer LZ77 matches",
        url="https://media.xiph.org/video/derf/y4m/crew_4cif.y4m",
        y4m_name="crew_4cif.y4m",
        prefix="crew-4cif",
    ),
    FastaSource(
        key="genomic_arm",
        license="PD-US-gov (NCBI GRCh38, chr1 200–204 Mb arm region)",
        note="H≈1.9-2.2, M≈0.10-0.40 — SINE/LINE-rich arm (older, diverged repeats; lower M_norm)",
        url=(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=nuccore&id=NC_000001.11&seq_start=200000001&seq_stop=204200001"
            "&rettype=fasta&retmode=text"
        ),
        fasta_name="grch38-chr1-arm.fasta",
        nt_name="grch38-chr1-arm.nt",
        slices=[
            ("grch38-chr1-arm-4M.bin",   0, _4M),
            ("grch38-chr1-arm-256K.bin",  0, _256K),
        ],
    ),
    FastaSource(
        key="genomic_micro",
        license="PD-US-gov (NCBI GRCh38, chr2 pericentromeric region)",
        note="H≈1.6-1.9, M≈0.40-0.70 — chr2 pericentromeric short tandem repeats",
        url=(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=nuccore&id=NC_000002.12&seq_start=91000001&seq_stop=95000001"
            "&rettype=fasta&retmode=text"
        ),
        fasta_name="grch38-chr2-pericent.fasta",
        nt_name="grch38-chr2-pericent.nt",
        slices=[
            ("grch38-chr2-pericent-4M.bin",   0, _4M),
            ("grch38-chr2-pericent-256K.bin",  0, _256K),
        ],
    ),
    StreamingGzLineSource(
        key="wikisql",
        license="CC-BY-SA 4.0 (Wikimedia Foundation, dumps.wikimedia.org)",
        note="H≈4.5-5.5, M≈0.40-0.60 — Wikipedia SQL dump; repeated INSERT INTO structure",
        url="https://dumps.wikimedia.org/simplewiki/latest/simplewiki-latest-categorylinks.sql.gz",
        partial_name="simplewiki-categorylinks.partial.sql.gz",
        partial_bytes=30 * 1024 * 1024,
        line_fn=_sql_data_line,
        slices=[
            ("wikisql-categorylinks-4M.bin",   0, _4M),
            ("wikisql-categorylinks-256K.bin",  0, _256K),
        ],
    ),
    StreamingGzLineSource(
        key="uniprot",
        license="CC-BY 4.0 (UniProt Consortium, uniprot.org/help/license)",
        note="H≈4.0-4.3, M≈0.40-0.60 — SwissProt amino-acid sequences (20-symbol alphabet)",
        url="https://ftp.ebi.ac.uk/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_sprot.fasta.gz",
        partial_name="uniprot_sprot.partial.fasta.gz",
        partial_bytes=100 * 1024 * 1024,  # ~100 MB compressed → ~750 MB sequences
        line_fn=_fasta_seq_line,
        add_newline=False,  # concatenate sequences without inter-sequence newlines
        slices=[
            ("uniprot-swissprot-4M.bin",   0, _4M),
            ("uniprot-swissprot-256K.bin",  0, _256K),
        ],
    ),
    JpegScanSource(
        key="jpeg_scan",
        license="PD-US-gov (NASA Blue Marble photograph, via Wikimedia Commons)",
        note="H≈7.5+, M≈0 — JPEG entropy-coded scan bytes; near-random after Huffman coding",
        url="https://upload.wikimedia.org/wikipedia/commons/9/97/The_Earth_seen_from_Apollo_17.jpg",
        jpeg_name="apollo17-earth.jpg",
        slices=[
            ("jpeg-scan-4M.bin",   0, _4M),
            ("jpeg-scan-256K.bin",  0, _256K),
        ],
    ),
    Y4MSource(
        key="parkrun",
        license="xiph.org test media (freely redistributable)",
        note="H≈6.5-7.2, M≈0.20-0.40 — park_joy 1080p50 luma; high motion → lower inter-frame match density",
        url="https://media.xiph.org/video/derf/y4m/park_joy_1080p50.y4m",
        y4m_name="park_joy_1080p50.y4m",
        prefix="park-joy-1080p50",
        partial_bytes=80 * 1024 * 1024,  # 80 MB → ~25 frames of 1080p50 luma
    ),
    PlainSource(
        key="mp3_audio",
        license="Public domain recording (LibriVox, librivox.org)",
        note="H≈7.0-7.5, M≈0.05-0.15 — MP3 audio bitstream; Huffman-coded with repeated frame sync headers",
        url="https://archive.org/download/study_scarlet_1203_librivox/studyinscarlet_01_conandoyle.mp3",
        raw_name="study-in-scarlet-01.mp3",
        slices=[
            ("mp3-librivox-4M.bin",   0, _4M),
            ("mp3-librivox-256K.bin",  0, _256K),
        ],
    ),
    StreamingGzLineSource(
        key="wiki_xml",
        license="CC-BY-SA 4.0 (Wikimedia Foundation, dumps.wikimedia.org)",
        note="H≈4.5-5.5, M≈0.40-0.60 — Wikipedia pages-articles XML; repeated tag boilerplate → L-long",
        url="https://dumps.wikimedia.org/simplewiki/latest/simplewiki-latest-pages-articles.xml.bz2",
        partial_name="simplewiki-pages.partial.xml.bz2",
        partial_bytes=30 * 1024 * 1024,
        line_fn=_xml_keep_line,
        decompressor="bz2",
        slices=[
            ("wiki-xml-pages-4M.bin",   0, _4M),
            ("wiki-xml-pages-256K.bin",  0, _256K),
        ],
    ),
    LocalBinarySource(
        key="arm64_binary",
        license="Apple Public Source License (Xcode Command Line Tools)",
        note="H≈5.5-6.5, M≈0.20-0.40 — ARM64 machine code from clang universal binary",
        src_path="/Library/Developer/CommandLineTools/usr/bin/clang",
        # ARM64 slice confirmed at offset 0x8E60000 = 148,897,792 bytes
        slices=[
            ("arm64-clang-text-4M.bin",   148897792, _4M),
            ("arm64-clang-text-256K.bin", 148897792, _256K),
        ],
    ),
    Y4MSource(
        key="video_1080p",
        license="xiph.org test media (freely redistributable)",
        note="H≈6.5-7.2, M≈0.40-0.60 — in_to_tree 1080p50 luma; large static tree segments → L-medium",
        url="https://media.xiph.org/video/derf/y4m/in_to_tree_1080p50.y4m",
        y4m_name="in_to_tree_1080p50.y4m",
        prefix="in-to-tree-1080p50",
        partial_bytes=80 * 1024 * 1024,
    ),
    StreamingGzLineSource(
        key="gencode_gff3",
        license="CC-BY 4.0 (GENCODE / Ensembl, gencodegenes.org)",
        note="H≈4.5-5.5, M≈0.60-0.80 — human gene annotation GFF3; repeated attribute keys + diverse coords",
        url="https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_44/gencode.v44.annotation.gff3.gz",
        partial_name="gencode.v44.partial.gff3.gz",
        partial_bytes=30 * 1024 * 1024,
        line_fn=_gff3_data_line,
        slices=[
            ("gencode-gff3-4M.bin",   0, _4M),
            ("gencode-gff3-256K.bin",  0, _256K),
        ],
    ),
    TarConcatSource(
        key="noaa_gsod",
        license="Public domain (NOAA/NCEI Global Surface Summary of the Day, ncei.noaa.gov)",
        note="H≈5.0-6.0, M≈0.40-0.60 — NOAA GSOD: daily weather station CSV, repeated coords + diverse values",
        url="https://www.ncei.noaa.gov/data/global-summary-of-the-day/archive/2023.tar.gz",
        archive_mode="gz",
        partial_bytes=30 * 1024 * 1024,
        file_suffix=".csv",
        transform_fn=None,
        slices=[
            ("noaa-gsod-2023-4M.bin",   0, _4M),
            ("noaa-gsod-2023-256K.bin",  0, _256K),
        ],
    ),
    StreamingGzLineSource(
        key="pdb_coords",
        license="CC0 (RCSB PDB / wwPDB, rcsb.org/pages/policies)",
        note="H≈3.5-4.0, M≈0.80+, L≈14 — ATOM/HETATM from 4V9D mmCIF (E. coli 70S ribosome, ~200K atoms)",
        url="https://files.rcsb.org/download/4V9D.cif.gz",
        partial_name="4v9d.partial.cif.gz",
        partial_bytes=50 * 1024 * 1024,  # ribosome mmCIF is ~8-15 MB compressed; 50 MB ensures full download
        line_fn=_mmcif_atom_line,
        slices=[
            ("pdb-coords-4v9d-4M.bin",   0, _4M),
            ("pdb-coords-4v9d-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 6: Pizza-Chili repetitive corpus ────────────────────────────────
    # Files are plain .gz (not tar.gz); cere is raw ASCII nucleotides (no FASTA headers).
    PartialGzSliceSource(
        key="repcorpus_cere",
        license="Research use (Pizza-Chili repetitive corpus, pizzachili.dcc.uchile.cl)",
        note="H≈1.9, M0.80+, L_p90>>60 — S. cerevisiae 16-genome concatenation; tandem-repeat HOR arrays",
        url="https://pizzachili.dcc.uchile.cl/repcorpus/real/cere.gz",
        partial_name="cere.partial.gz",
        # cere is 440 MB uncompressed / 120 MB gzipped → 3.6× ratio.
        # 10 MB compressed → ~36 MB decompressed (>> 4 MB needed).
        partial_bytes=10 * 1024 * 1024,
        slices=[
            ("cere-4M.bin",   0, _4M),
            ("cere-256K.bin", 0, _256K),
        ],
    ),
    PartialGzSliceSource(
        key="repcorpus_einstein",
        license="Research use (Pizza-Chili repetitive corpus, pizzachili.dcc.uchile.cl)",
        note="H≈3.5-4.5, M0.80+, L_p90>>60 — German Wikipedia revision history; near-duplicate article versions",
        url="https://pizzachili.dcc.uchile.cl/repcorpus/real/einstein.de.txt.gz",
        partial_name="einstein-de.partial.gz",
        # einstein.de.txt is 446 MB uncompressed / 28 MB gzipped → 16× ratio.
        # 5 MB compressed → ~80 MB decompressed (>> 4 MB needed).
        partial_bytes=5 * 1024 * 1024,
        slices=[
            ("einstein-de-4M.bin",   0, _4M),
            ("einstein-de-256K.bin", 0, _256K),
        ],
    ),
    # ── Round 6: T2T-CHM13 alpha-satellite HOR array (via NCBI efetch) ────────
    FastaSource(
        key="t2t_alphasat",
        license="CC0 (T2T Consortium / NCBI; CHM13v2.0 assembly, GCA_009914755.4)",
        note="H≈1.5-2.0, M0.80+, L_p90>>60 — T2T-CHM13 chrY DYZ3 HOR array; near-identical 171bp monomers",
        url=("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
             "?db=nuccore&id=CP086569.2&rettype=fasta&retmode=text"
             "&seq_start=11000000&seq_stop=16000000"),
        fasta_name="t2t-chry-alphasat.fasta",
        nt_name="t2t-chry-alphasat.nt",
        slices=[
            ("t2t-chry-alphasat-4M.bin",   0, _4M),
            ("t2t-chry-alphasat-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 6: OpenStreetMap XML (outside the box — structured geospatial) ──
    StreamingGzLineSource(
        key="osm_berlin",
        license="ODbL (OpenStreetMap contributors, openstreetmap.org/copyright)",
        note="H≈4.5-5.5, M≈0.20-0.40, L≈20-40 — OSM XML Berlin: repeated <node/way/tag> boilerplate + diverse IDs",
        url="https://download.bbbike.org/osm/bbbike/Berlin/Berlin.osm.gz",
        partial_name="berlin-latest.partial.osm.gz",
        partial_bytes=30 * 1024 * 1024,
        line_fn=_osm_data_line,
        decompressor="gz",
        slices=[
            ("osm-berlin-4M.bin",   0, _4M),
            ("osm-berlin-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 7: genome-boundary straddle for L-long at H1.86-3.0 ─────────────
    # The 16 S. cerevisiae genomes in cere.gz are each ~12.1 MB.  A 4 MB slice
    # at offset 0 stays within genome 1 (no inter-strain matches) → L-short.
    # Slicing at offset ~11.5 MB straddles the genome 1→2 junction: identical
    # chromosomes from two strains produce near-verbatim LZ77 matches of
    # thousands of bytes → L_p90 >> 60 → L-long.
    PartialGzSliceSource(
        key="repcorpus_cere_straddle",
        license="Research use (Pizza-Chili repetitive corpus, pizzachili.dcc.uchile.cl)",
        note="H≈1.9-2.3, M0.80+, L_p90>>1000 — cere slice straddling genome 1→2 boundary (inter-strain matches)",
        url="https://pizzachili.dcc.uchile.cl/repcorpus/real/cere.gz",
        partial_name="cere-straddle.partial.gz",
        # 11.5 MB + 4 MB = 15.5 MB decompressed needed; ratio ~3.7x → ~4.2 MB compressed minimum.
        # Download 20 MB compressed to provide headroom.
        partial_bytes=20 * 1024 * 1024,
        slices=[
            ("cere-straddle-4M.bin",   11_500_000, _4M),
            ("cere-straddle-256K.bin", 11_500_000, _256K),
        ],
    ),
    # ── Round 7: GENCODE GTF annotation for H3.0-4.5/M0.80+/L-long ───────────
    # GTF has more compact attribute syntax than GFF3 (H≈4.0-4.4 vs 5.3).
    # Near-verbatim repeated per-transcript attribute blocks → L_p90 ≥ 100.
    StreamingGzLineSource(
        key="gencode_gtf",
        license="CC-BY 4.0 (GENCODE / Ensembl, gencodegenes.org)",
        note="H≈4.0-4.4, M≈0.80+, L_p90≥100 — GENCODE v45 GTF; compact attributes, repeated transcript records",
        url="https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_45/gencode.v45.annotation.gtf.gz",
        partial_name="gencode.v45.partial.gtf.gz",
        partial_bytes=30 * 1024 * 1024,
        line_fn=_gff3_data_line,  # GTF data lines also start with chr name, skip '#' comments
        slices=[
            ("gencode-gtf-4M.bin",   0, _4M),
            ("gencode-gtf-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 7: multi-author Gutenberg concat for H4.5-6.0/M0.40-0.60/L-short ─
    # Single-author Shakespeare (existing gutenberg source) has heavy within-book
    # repetition (character names, stage directions) → M_norm≈0.90, too high.
    # Concatenating 8 different authors breaks the within-author repetition →
    # M_norm drops to ~0.55 while keeping H ≈ 4.7, L_p90 ≈ 6 (L-short).
    MultiFileSource(
        key="gutenberg_multi",
        license="Public domain (Project Gutenberg, gutenberg.org)",
        note="H≈4.5-5.0, M≈0.40-0.60, L-short — 8 different 19th-c authors; cross-author diversity breaks repetition",
        urls=[
            "https://www.gutenberg.org/cache/epub/1342/pg1342.txt",  # Pride and Prejudice (Austen)
            "https://www.gutenberg.org/cache/epub/84/pg84.txt",      # Frankenstein (Shelley)
            "https://www.gutenberg.org/cache/epub/1661/pg1661.txt",  # Sherlock Holmes (Doyle)
            "https://www.gutenberg.org/cache/epub/1400/pg1400.txt",  # Great Expectations (Dickens)
            "https://www.gutenberg.org/cache/epub/2701/pg2701.txt",  # Moby Dick (Melville)
            "https://www.gutenberg.org/cache/epub/98/pg98.txt",      # A Tale of Two Cities (Dickens)
            "https://www.gutenberg.org/cache/epub/11/pg11.txt",      # Alice in Wonderland (Carroll)
            "https://www.gutenberg.org/cache/epub/2814/pg2814.txt",  # Dubliners (Joyce)
        ],
        file_names=[
            "gutenberg-pride-prejudice.txt",
            "gutenberg-frankenstein.txt",
            "gutenberg-sherlock-holmes.txt",
            "gutenberg-great-expectations.txt",
            "gutenberg-moby-dick.txt",
            "gutenberg-tale-two-cities.txt",
            "gutenberg-alice.txt",
            "gutenberg-dubliners.txt",
        ],
        slices=[
            ("gutenberg-multi-4M.bin",   0, _4M),
            ("gutenberg-multi-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 7: UniRef50 protein clusters for H3.0-4.5/M0.60-0.80/L-short ───
    # UniRef50 clusters all known proteins at 50% identity.  Conserved domain
    # motifs within clusters produce higher M than full SwissProt (M_norm≈0.65
    # vs ≈0.42) while keeping H≈4.3 (20-letter alphabet) and L_p90≈7 (L-short).
    StreamingGzLineSource(
        key="uniref50",
        license="CC-BY 4.0 (UniProt Consortium, uniprot.org/help/license)",
        note="H≈4.2-4.4, M≈0.60-0.80, L-short — UniRef50 amino-acid sequences; conserved domain motifs",
        url="https://ftp.uniprot.org/pub/databases/uniprot/uniref/uniref50/uniref50.fasta.gz",
        partial_name="uniref50.partial.fasta.gz",
        partial_bytes=50 * 1024 * 1024,  # ~50 MB compressed → millions of short sequences
        line_fn=_fasta_seq_line,
        add_newline=False,
        slices=[
            ("uniref50-4M.bin",   0, _4M),
            ("uniref50-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 7: T2T-CHM13 HSat2 chr1 q12 for H0.5-1.5/M0.80+/L-medium ──────
    # Human Satellite 2 (HSat2) on T2T-CHM13 chr1 q12 has 26-bp higher-order
    # subunits of the ATTCC pentamer.  Array at ~121-125 Mb: H≈1.0-1.4 (AT-rich),
    # M_norm≈0.99, L_p90≈25-30 → expected to land (1,5,1) H0.5-1.5/M0.80+/L-medium.
    FastaSource(
        key="t2t_hsat2",
        license="CC0 (T2T Consortium / NCBI; CHM13v2.0 assembly)",
        note="H≈1.0-1.4, M0.99, L_p90≈25 — T2T-CHM13 chr1 q12 HSat2 array; 26-bp ATTCC higher-order subunit",
        url=(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=nuccore&id=CP068277.2&rettype=fasta&retmode=text"
            "&seq_start=121000000&seq_stop=125000000"
        ),
        fasta_name="t2t-chr1-hsat2.fasta",
        nt_name="t2t-chr1-hsat2.nt",
        slices=[
            ("t2t-chr1-hsat2-4M.bin",   0, _4M),
            ("t2t-chr1-hsat2-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 7: T2T-CHM13 GSAT chr13 for H0.5-1.5/M0.80+/L-long ───────────
    # Gamma satellite (GSAT) on T2T-CHM13 chr13 acrocentric short arm has 220-bp
    # HOR units (not 171 like alpha-sat).  AT-richness pulls H≈1.0-1.5; very low
    # divergence within HOR → near-verbatim 220-bp matches → L_p90 >> 60 → L-long.
    # Requesting 8 Mb (positions 1-8000000) to ensure ≥4 MB of nucleotide output
    # after stripping FASTA headers.
    FastaSource(
        key="t2t_gsat",
        license="CC0 (T2T Consortium / NCBI; CHM13v2.0 assembly)",
        note="H≈1.0-1.5, M0.99, L_p90≥220 — T2T-CHM13 chr13 GSAT array; 220-bp HOR → L-long",
        url=(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=nuccore&id=CP068267.2&rettype=fasta&retmode=text"
            "&seq_start=1&seq_stop=8000000"
        ),
        fasta_name="t2t-chr13-gsat.fasta",
        nt_name="t2t-chr13-gsat.nt",
        slices=[
            ("t2t-chr13-gsat-4M.bin",   0, _4M),
            ("t2t-chr13-gsat-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 7: P. falciparum chr14 ─────────────────────────────────────────
    # Plasmodium falciparum 3D7 has extreme AT-richness (~80% AT) → H≈2.0-2.3.
    # Chr7 alone is only ~1.5 Mb; chr8 (NC_004319.2) is ~1.4 Mb.  Concatenating
    # chr7+chr8 gives ~2.9 Mb nucleotide, still short of 4 Mb.  We request a
    # 5 Mb window from chr14 (NC_004325.3, ~3.3 Mb, largest chromosome) instead.
    # var-gene cassette repeats produce matches in the 15-40 bp range → L-medium.
    FastaSource(
        key="plasmodium",
        license="PD-US-gov (NCBI RefSeq, GCF_000002765.6, P. falciparum 3D7)",
        note="H≈2.0-2.3, M0.80+, L_p90≈15-40 — P. falciparum chr14 (largest, 3.3 Mb); var-gene repeats, AT-rich",
        url=(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=nuccore&id=NC_004317.2&rettype=fasta&retmode=text"
            "&seq_start=1&seq_stop=5000000"
        ),
        fasta_name="plasmodium-chr14.fasta",
        nt_name="plasmodium-chr14.nt",
        slices=[
            ("plasmodium-chr14-4M.bin",   0, _4M),
            ("plasmodium-chr14-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 8: Pizza-Chili E. coli for H1.86-3.0/M0.80+/L-long ────────────
    # E. coli K-12 has 7 rRNA operons (~5.5 kb each, >99% identical) spread
    # across the genome.  Operons rrnD (0.44 Mb) and rrnH (0.78 Mb) are only
    # ~340 kb apart → well within LZ77's window.  Processing rrnH, the algorithm
    # looks back 340 kb and finds a 5.5 kb verbatim match → L_p90 >> 60 → L-long.
    # Pizza-Chili "Escherichia_Coli" is a concatenation of multiple E. coli genome
    # sequences; even the first 4 MB contains multiple rRNA operon copies.
    PartialGzSliceSource(
        key="repcorpus_ecoli",
        license="Research use (Pizza-Chili repetitive corpus, pizzachili.dcc.uchile.cl)",
        note="H≈2.0, M0.99, L_p90>>60 — E. coli rRNA operon repeats (7×5.5kb per genome, >99% identical)",
        url="https://pizzachili.dcc.uchile.cl/repcorpus/real/Escherichia_Coli.gz",
        partial_name="ecoli.partial.gz",
        # E. coli.gz is 31.5 MB compressed.  First 5 MB decompresses to ~15-20 MB,
        # covering the rrnD and rrnH regions (0.44–0.78 Mb) and several more.
        partial_bytes=5 * 1024 * 1024,
        slices=[
            ("ecoli-4M.bin",   0, _4M),
            ("ecoli-256K.bin", 0, _256K),
        ],
    ),
    # ── Round 8: Pizza-Chili influenza for H1.86-3.0/M0.80+/L-long ──────────
    # The influenza corpus is a concatenation of many influenza genome sequences
    # (8 segments × N strains, total ~10 MB compressed → ~40 MB uncompressed).
    # Near-identical influenza strains (same H/N type, different years) have
    # >98% sequence identity per segment → LZ77 finds 1000-2300 bp exact matches
    # across strains when multiple copies are in the window → L_p90 >> 60 → L-long.
    PartialGzSliceSource(
        key="repcorpus_influenza",
        license="Research use (Pizza-Chili repetitive corpus, pizzachili.dcc.uchile.cl)",
        note="H≈2.0-2.3, M0.99, L_p90>>60 — influenza genome concatenation; near-identical H/N strain copies",
        url="https://pizzachili.dcc.uchile.cl/repcorpus/real/influenza.gz",
        partial_name="influenza.partial.gz",
        # influenza.gz is 10.6 MB compressed.  Download full file to get complete dataset.
        partial_bytes=12 * 1024 * 1024,
        slices=[
            ("influenza-4M.bin",   0, _4M),
            ("influenza-256K.bin", 0, _256K),
        ],
    ),
    # ── Round 8: E. coli K-12 chromosome for H1.86-3.0/M0.80+/L-long ────────
    # E. coli K-12 MG1655 chromosome (NC_000913.3) has 7 rRNA operons.  The
    # rrnH and rrnD operons are only ~340 kb apart at positions ~223 kb and
    # ~442 kb.  A 4 MB slice starting at position 1 captures BOTH operons
    # within the LZ77 window → 5.5 kb exact match → L_p90 >> 60 → L-long.
    # Unlike the Pizza-Chili E. coli collection (109 individual sequences),
    # this is a single contiguous chromosome with the rRNA operon structure intact.
    FastaSource(
        key="ecoli_k12",
        license="PD-US-gov (NCBI RefSeq, E. coli K-12 MG1655 NC_000913.3)",
        note="H≈2.0, M0.99, L_p90>>1000 — E. coli K-12 chr1 4 Mb; rrnH/rrnD operons 340 kb apart → L-long",
        url=(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=nuccore&id=NC_000913.3&rettype=fasta&retmode=text"
            "&seq_start=1&seq_stop=4000000"
        ),
        fasta_name="ecoli-k12-chr.fasta",
        nt_name="ecoli-k12-chr.nt",
        slices=[
            ("ecoli-k12-4M.bin",   0, _4M),
            ("ecoli-k12-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 8: NMR ensemble mmCIF for H3.0-4.5/M0.80+/L-long ─────────────
    # NMR structure 2K2E (human ubiquitin, 8 models).  Each model outputs
    # identical ATOM record topology with different coordinates.  With 8 models,
    # LZ77 processing model 8 can match back to model 7 → full-model-length
    # exact match (76 residues × ~80 bytes = ~6 kbp per model) → L_p90 >> 1000.
    # Same H as other mmCIF (H≈3.5, 20-char atom label alphabet) → cell (4,5,2).
    StreamingGzLineSource(
        key="pdb_nmr",
        license="CC0 (RCSB PDB / wwPDB, rcsb.org/pages/policies)",
        note="H≈3.5-4.0, M0.99, L_p90>>1000 — 2K2E NMR ensemble (8 models, ubiquitin); verbatim per-model repeats",
        url="https://files.rcsb.org/download/2K2E.cif.gz",
        partial_name="2k2e.partial.cif.gz",
        partial_bytes=5 * 1024 * 1024,
        line_fn=_mmcif_atom_line,
        slices=[
            ("pdb-nmr-2k2e-4M.bin",   0, _4M),
            ("pdb-nmr-2k2e-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 9: DBLP bibliography XML for H3.0-4.5/M0.80+/L-long ────────────
    # DBLP computer-science bibliography in XML.  Record structure:
    # <article>, <author>, <title>, <year>, <journal> tags repeat verbatim for
    # every publication entry (>7M entries total).  The fixed tag names provide
    # long exact matches (e.g. "<inproceedings mdate=" = 22 chars) within every
    # record → L_p90 >> 60 → L-long.  High-diversity author/title text keeps
    # H ≈ 4.3-4.5 (ASCII, 26-char alphabet with numbers and punctuation).
    # Expected: H≈4.3, M≈0.90-0.95, L_p90≈80-200 → cell (4,5,2).
    StreamingGzLineSource(
        key="dblp_xml",
        license="ODC-BY (DBLP Computer Science Bibliography, dblp.org)",
        note="H≈4.3-4.5, M≈0.90-0.95, L_p90≈80-200 — DBLP bibliography XML; repeated tag structure + diverse author/title text",
        url="https://dblp.org/xml/dblp.xml.gz",
        partial_name="dblp.partial.xml.gz",
        partial_bytes=50 * 1024 * 1024,
        line_fn=_xml_keep_line,
        slices=[
            ("dblp-4M.bin",   0, _4M),
            ("dblp-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 9: NCBI Taxonomy names.dmp for H3.0-4.5/M0.60-0.80/L-short ────
    # NCBI Taxonomy dump names.dmp: tab-delimited lines with fields
    # tax_id | name | unique_name | name_class.
    # The name_class field has ~15 distinct long values ("scientific name",
    # "common name", "authority", "blast name", etc.) that repeat for millions
    # of taxa → very high match density.  Diverse taxon names keep H ≈ 3.8-4.2.
    # L_p90 ≈ 6-14 (length of name_class strings) → L-short.
    # Expected: H≈3.8-4.2, M≈0.65-0.75, L_p90≈6-14 → cell (4,4,0).
    TarConcatSource(
        key="ncbi_taxonomy",
        license="Public domain (NCBI Taxonomy, ncbi.nlm.nih.gov/taxonomy)",
        note="H≈3.8-4.2, M≈0.65-0.75, L_p90≈6-14 — NCBI Taxonomy names.dmp; repeated name_class field + diverse taxon names",
        url="https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz",
        archive_mode="gz",
        # names.dmp is the 7th file in the archive; earlier files total ~33 MB
        # uncompressed (citations.dmp alone is 19 MB).  Use full download (74.5 MB)
        # to guarantee reaching names.dmp regardless of per-file compression ratio.
        partial_bytes=0,
        file_suffix="names.dmp",
        transform_fn=None,
        slices=[
            ("ncbi-taxonomy-4M.bin",   0, _4M),
            ("ncbi-taxonomy-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 9: OpenSubtitles English for H4.5-6.0/M0.20-0.60 ──────────────
    # OPUS OpenSubtitles 2018: plain English dialogue, one sentence per line,
    # from thousands of films across many decades and genres.  Cross-film diversity
    # suppresses phrase repetition compared to single-author text → lower M than
    # Gutenberg or GHArchive.  Short declarative sentences keep L_p90 small.
    # Expected: H≈4.7-5.1, M≈0.35-0.55, L_p90≈5-9 → cell (5,3,0) or (5,2,0).
    StreamingGzLineSource(
        key="opensubtitles",
        license="CC-BY-SA (OpenSubtitles.org via OPUS, opus.nlpl.eu/OpenSubtitles)",
        note="H≈4.7-5.1, M≈0.35-0.55, L_p90≈5-9 — English movie dialogue; cross-film diversity suppresses repetition",
        url="https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2018/mono/en.txt.gz",
        partial_name="opensubtitles-en.partial.txt.gz",
        partial_bytes=30 * 1024 * 1024,
        line_fn=_xml_keep_line,  # passes all non-empty lines (plain text, no tags)
        slices=[
            ("opensubtitles-4M.bin",   0, _4M),
            ("opensubtitles-256K.bin",  0, _256K),
        ],
    ),
    # ── Round 9: Pfam-A domain FASTA for H3.0-4.5/M0.60-0.80 ────────────────
    # Pfam-A organises all known protein domains into families, with sequences
    # grouped by family → many near-identical homologous sequences concatenated.
    # The conserved domain core (typically 50-200 residues) in each family drives
    # high M_norm at H≈4.1 (20-letter amino-acid alphabet).  Compared to UniRef50
    # (M_norm=0.23), the family clustering produces denser exact-match coverage.
    # Expected: H≈4.0-4.3, M≈0.60-0.75, L_p90≈5-10 → cell (4,4,0) or (4,4,1).
    StreamingGzLineSource(
        key="pfam_a",
        license="CC0 (Pfam, ebi.ac.uk/interpro/entry/pfam/)",
        note="H≈4.0-4.3, M≈0.60-0.75, L_p90≈5-10 — Pfam-A domain FASTA; family-grouped sequences → conserved domain motifs",
        url="https://ftp.ebi.ac.uk/pub/databases/Pfam/current_release/Pfam-A.fasta.gz",
        partial_name="pfam-a.partial.fasta.gz",
        partial_bytes=100 * 1024 * 1024,  # ~100 MB → many protein family clusters
        line_fn=_fasta_seq_line,
        add_newline=False,
        slices=[
            ("pfam-a-4M.bin",   0, _4M),
            ("pfam-a-256K.bin",  0, _256K),
        ],
    ),
]

SOURCES_BY_KEY: dict[str, Source] = {s.key: s for s in SOURCES}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="List sources and exit")
    parser.add_argument("--only", nargs="+", metavar="KEY",
                        help="Download only these source keys")
    parser.add_argument("--force", action="store_true",
                        help="Re-download and re-extract even if files exist")
    args = parser.parse_args()

    if args.list:
        print(f"{'Key':12s}  {'License':50s}  Note")
        print("-" * 120)
        for s in SOURCES:
            print(f"{s.key:12s}  {s.license:50s}  {s.note}")
        return

    keys = args.only or [s.key for s in SOURCES]
    unknown = [k for k in keys if k not in SOURCES_BY_KEY]
    if unknown:
        print(f"Unknown keys: {unknown}", file=sys.stderr)
        sys.exit(1)

    ok_count = fail_count = 0
    for key in keys:
        src = SOURCES_BY_KEY[key]
        dest_dir = DEST_BASE / key
        print(f"\n[{key}]  {src.note}")
        if src.run(dest_dir, force=args.force):
            ok_count += 1
        else:
            print(f"  FAILED: {key}", file=sys.stderr)
            fail_count += 1

    print(f"\n{'='*60}")
    print(f"Done: {ok_count} succeeded, {fail_count} failed")
    print(f"\nNext: measure extracted files:")
    print(f"  uv run scripts/measure-corpus.py \\")
    print(f"      --dirs build/raw/natural \\")
    print(f"      --out build/bench/natural-measurements.csv")
    print(f"\nThen rebuild curated selection:")
    print(f"  uv run scripts/select-curated.py")


if __name__ == "__main__":
    main()
