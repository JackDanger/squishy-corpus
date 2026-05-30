#!/usr/bin/env python3
"""squishy — the Squishy Score runner (shared scoring core).

Squishy Score (of a codec) = the nested geometric mean (size → kind → category) of
per-file compression ratio (uncompressed / compressed) over the compressibility-
scored files. A file is scored iff it sits on the compressible side of the K plane
(see `is_scored`); entropy-coded media (photo/movie/weights) are measured but kept
out of the headline as diagnostics. Reported as a dimensionless "×" beside a
byte-weighted `corpus_bpb` (never derive bpb from the score).

This module is the scoring + provenance library; the canonical whole-edition number
is produced by `scripts/squishy-calculate.py` (streams core + large rungs). Local:

  # Score one codec live over the LOCAL core members (a partial, dev-time board):
  uv run python scripts/squishy.py bench --cmd "gzip -9 -c"
  # Reference panel over the local core (writes build/meta/squishy-scores.json):
  uv run python scripts/board-live.py

Canonical run rule: one codec, one setting, all files. The complete-edition score is
the periodic computation; a run over a subset prints per-file ratios, not a headline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "build" / "raw"
IND = REPO / "build" / "individual"

# The Squishy corpus (small members present; large rungs pending): real,
# provenanced files across 5 categories.
# Entries: (display, set, filename). Raw bytes at build/raw/<set>/<filename>;
# reference-panel compressed variants at build/individual/<set>/<filename>.<suffix>.
# Near-incompressible by design: photo, movie, weights (a realistic mix includes
# already-compressed bytes).
CORE: dict[str, list[tuple[str, str, str]]] = {
    "Prose": [
        ("dickens", "corpus", "dickens"),         # English prose (PD), pure text
        ("aozora",  "corpus", "aozora.txt"),      # Japanese prose (PD), ruby-stripped
    ],
    "Code & Web": [
        ("monorepo", "corpus", "monorepo.tar"),   # modern source subtree (LLVM/Apache)
        ("minjs",    "corpus", "minjs.min.js"),   # real minified bundle
        ("markup",   "corpus", "markup.xml"),      # XML — Bosak Shakespeare (freely distributable)
    ],
    "Structured": [
        ("json",   "corpus", "data.json"),        # real API/NDJSON dump
        ("log",    "corpus", "access.log"),       # real anonymized access log
        ("genome", "corpus", "ecoli.fastq"),      # E. coli FASTQ reads (PD)
    ],
    "Tabular / DB": [
        ("csv",     "corpus", "data.csv"),        # NOAA GHCN-Daily weather (PD-USGov)
        ("parquet", "corpus", "data.parquet"),    # BTS airline on-time, uncompressed columnar (PD-USGov)
        ("sqlite",  "corpus", "data.sqlite"),     # USDA FoodData Central nutrition DB (PD-USGov)
    ],
    "Binary & Media": [
        ("exe",     "corpus", "tool.bin"),        # native binary (MIT/Apache)
        ("photo",   "corpus", "photo.jpg"),       # pre-compressed image (PD-USGov-NASA) [incompressible]
        ("movie",   "corpus", "movie.mp4"),       # pre-compressed video (CC-BY)  [incompressible]
        ("weights", "corpus", "weights.safetensors"),  # model-weight shard (Apache) [incompressible]
    ],
}
BOUNDS = [("modern", "random-1M")]  # synthetic/incompressible — never in headline

# What enters the Squishy Score is decided by intrinsic COMPRESSIBILITY, not by
# category. From the codec-free byte axes alone:
#     K = coverage + (8 − entropy)/8        (repetition gain + entropy headroom)
# a file is scored iff K ≥ COMPRESSIBILITY_MIN. Because K ignores match-distance, the
# boundary is a single flat PLANE in the (entropy × coverage × distance) space — a
# vertical curtain — that cleanly separates the entropy-coded media (photo/movie/
# weights, top K ≈ 0.081) from everything compressible (lowest scored point: parquet
# K ≈ 0.143): the plane at 0.11 sits inside that ≈0.062-wide gap. Non-scored files
# stay in the corpus as behaviour/throughput diagnostics; `exe` (K ≈ 0.47) is scored.
# (Owner decision 2026-05-29: a compressibility plane, not a category, gates scoring.)
CATEGORY_ORDER = ["Prose", "Code & Web", "Structured", "Tabular / DB", "Binary & Media"]
COMPRESSIBILITY_MIN = 0.11


def compressibility(entropy: float, coverage: float) -> float:
    """Intrinsic, codec-free compressibility proxy: repetition gain + entropy headroom."""
    return coverage + (8.0 - entropy) / 8.0


def is_scored(props: dict) -> bool:
    """A file is in the Squishy Score iff it sits on the compressible side of the plane.
    A file we have not measured cannot be certified compressible, so it is NOT scored
    (the build should measure every distributed file; an un-measured one is excluded
    from the headline rather than scored on a guess)."""
    if not props or "entropy" not in props or "coverage" not in props:
        return False
    return compressibility(props["entropy"], props["coverage"]) >= COMPRESSIBILITY_MIN

# Reference panel: canonical "best practical" level per codec → individual/ suffix.
PANEL = {
    "gzip -9":    "gz.l9",
    "bzip2 -9":   "bz2",
    "zstd -19":   "zst.l19",
    "zstd -22":   "zst.l22",
    "xz -9":      "xz.l9",
    "brotli -11": "br.l11",
    "zpaq":       "zpaq",
}

# Map a panel codec label to the tool name used in build/tools.lock.
PANEL_TOOL = {
    "gzip -9": "gzip", "bzip2 -9": "bzip2", "zstd -19": "zstd",
    "zstd -22": "zstd", "xz -9": "xz", "brotli -11": "brotli", "zpaq": "zpaq",
}

# Exact canonical command line per reference codec — the OTHER half of
# reproducibility (versions alone aren't enough; flags decide the bytes).
# A reference-board number is a property of (corpus, codec, version, argv).
PANEL_ARGV = {
    "gzip -9":   "gzip -9 -c",
    "bzip2 -9":  "bzip2 -9 -c",
    "zstd -19":  "zstd -19 -c",
    "zstd -22":  "zstd --ultra -22 -c",
    "xz -9":     "xz -9 -c",
    "brotli -11": "brotli -q 11 -c",
    "zpaq":      "zpaq add {out} {in} -method 5",  # archiver, not a filter; {out}=.zpaq
}


def _validate_core() -> None:
    """Guard against duplicate core entries (a silent way to double-weight a file)."""
    seen_display, seen_file = set(), set()
    for files in CORE.values():
        for display, s, name in files:
            assert display not in seen_display, f"duplicate core display name: {display}"
            assert (s, name) not in seen_file, f"duplicate core file: {s}/{name}"
            seen_display.add(display); seen_file.add((s, name))


_validate_core()


def tool_versions() -> dict[str, str]:
    """Parse build/tools.lock → {tool: version string}. Numbers are only
    reproducible for these exact codec builds; record them with the scores."""
    out: dict[str, str] = {}
    lock = REPO / "build" / "tools.lock"
    if not lock.exists():
        return out
    for line in lock.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split(None, 2)
        if len(parts) >= 3:
            out[parts[0]] = parts[2].strip()
    return out


def _query_version(tool: str) -> str | None:
    for flag in ("--version", "-V", "version"):
        try:
            r = subprocess.run([tool, flag], capture_output=True, text=True, timeout=10)
            line = (r.stdout or r.stderr).splitlines()
            if line:
                return line[0].strip()[:120]
        except Exception:
            continue
    return None


def host_provenance() -> dict:
    """The machine a score was produced on. Compression ratios are byte-deterministic
    for a given (codec version, argv), so cross-system scores SHOULD match — this is
    recorded so that, if they ever don't, the variance has perfect provenance (same
    discipline as the per-file dataset sha256s)."""
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),                       # cpu architecture, e.g. arm64 / x86_64
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _git_sha(version: str | None) -> str | None:
    """A short git sha IF the version names a non-release/dev build (git-describe
    `-g<sha>` or an explicit `commit <sha>`); None for a clean release version. The
    install path is deliberately NOT recorded — it's host-specific and says nothing
    about which code ran."""
    if not version:
        return None
    m = re.search(r"\bg([0-9a-f]{7,40})\b", version) or re.search(r"commit[:\s]+([0-9a-f]{7,40})", version, re.I)
    return m.group(1)[:12] if m else None


def tool_provenance(cmd: str) -> dict:
    """Portable identity of the codec behind a score: release **version** (or a short
    **git sha** for a non-release build) and the target **architecture**. Ratios are
    byte-deterministic for a given (version, argv), so this is what lets any cross-
    system variance be traced — the tool-side analogue of the dataset sha256s."""
    tok = cmd.replace("{in}", "").replace("{out}", "").split()
    tool = tok[0] if tok else cmd
    version = tool_versions().get(tool) or _query_version(tool)
    sha = _git_sha(version)
    prov: dict = {"tool": tool, "argv": cmd, "version": version,
                  "git_sha": sha, "release": version is not None and sha is None,
                  "arch": None}
    path = shutil.which(tool)
    if path and os.path.exists(path):
        try:                                             # binary's TARGET arch (not its path)
            arch = subprocess.run(["file", "-b", path], capture_output=True,
                                  text=True, timeout=5).stdout.strip()
            prov["arch"] = " ".join(arch.split())[:200]   # collapse multi-line (universal binaries)
        except Exception:
            pass
    return prov


def raw_path(s: str, name: str) -> Path:
    return RAW / s / name


def raw_size(s: str, name: str) -> int | None:
    p = raw_path(s, name)
    return p.stat().st_size if p.exists() else None


def comp_size_individual(s: str, name: str, suffix: str) -> int | None:
    p = IND / s / f"{name}.{suffix}"
    return p.stat().st_size if p.exists() else None


def run_codec_live(cmd: str, data: bytes) -> tuple[int, float]:
    """Run a codec command, return (compressed_size, seconds).

    Two calling conventions:
      - stdin→stdout filter (default): e.g. "gzip -9 -c"
      - file-arg codecs: include `{in}` (and optionally `{out}`) in the command;
        the input is written to a temp file and {in}/{out} are substituted. The
        compressed size is len(stdout) unless {out} is given, then the {out} file.
    """
    import tempfile, os as _os
    t0 = time.perf_counter()
    if "{in}" in cmd:
        with tempfile.TemporaryDirectory() as d:
            ip = _os.path.join(d, "in"); op = _os.path.join(d, "out")
            with open(ip, "wb") as f:
                f.write(data)
            run = cmd.replace("{in}", ip).replace("{out}", op)
            proc = subprocess.run(run, shell=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, check=True)
            if "{out}" in cmd:
                # the codec may add an extension (e.g. zpaq → out.zpaq); take the
                # largest file in the temp dir that isn't the input.
                outs = [(_os.path.getsize(_os.path.join(d, f)), f)
                        for f in _os.listdir(d) if f != "in"]
                size = max(outs)[0] if outs else len(proc.stdout)
            else:
                size = len(proc.stdout)
            return size, time.perf_counter() - t0
    proc = subprocess.run(cmd, shell=True, input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          check=True)
    return len(proc.stdout), time.perf_counter() - t0


def verify_core_checksums() -> list[str]:
    """If a core CHECKSUMS file is present, verify each core raw file's sha256.
    Returns list of mismatched/missing display names. No-op (empty) until the
    corpus is published with checksums (wired fully at freeze)."""
    import hashlib
    ck = REPO / "build" / "meta" / "CHECKSUMS.sha256"
    if not ck.exists():
        return []
    want = {}
    for line in ck.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            want[parts[1]] = parts[0]
    bad = []
    for files in CORE.values():
        for display, s, name in files:
            p = raw_path(s, name)
            key = f"{s}/{name}"
            if key in want:
                if not p.exists():
                    bad.append(display); continue
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                if h != want[key]:
                    bad.append(display)
    return bad


def geomean(xs: list[float]) -> float:
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def scored_corpus(edition_path: Path | None = None) -> dict[str, dict[str, list[dict]]]:
    """The scored set, edition-driven (single source of truth) and grouped for the
    nested geomean: {category: {kind: [size-point, ...]}}, size-points sorted small→
    large. Reads build/meta/edition.json (category/kind/tier/key/url/sha256/props per
    file). ALL five categories are kept; the per-file compressibility plane (is_scored)
    decides membership — so entropy-coded media (photo/movie/weights) fall out one by
    one while `exe` stays, and Binary & Media is NOT special-cased. Each kind's list is
    its size axis (one entry for single-size kinds, several for kinds with large rungs)."""
    path = edition_path or (REPO / "build" / "meta" / "edition.json")
    data = json.loads(path.read_text())
    out: dict[str, dict[str, list[dict]]] = {c: {} for c in CATEGORY_ORDER}
    for f in data.get("files", []):
        cat = f.get("category")
        if cat not in out or not is_scored(f):     # below the compressibility plane → not scored
            continue
        out[cat].setdefault(f.get("kind"), []).append(f)
    for cat in out:
        for kind in out[cat]:
            out[cat][kind].sort(key=lambda x: x.get("size_bytes") or 0)
    return {c: ks for c, ks in out.items() if ks}


def nested_score(ratio_of, edition_path: Path | None = None) -> dict:
    """Compute the Squishy Score as the true nested geomean size→kind→category over
    the scored corpus. `ratio_of(size_point)->ratio|None` supplies each file's ratio
    (the caller decides how to obtain it: local bytes, streamed bytes, cached). Equal
    weight at every level; non-compressible files (below the K plane) are already
    excluded per-file by scored_corpus()."""
    sc = scored_corpus(edition_path)
    cat_scores: dict[str, float] = {}
    kind_scores: dict[str, float] = {}
    per_file: dict[str, float] = {}
    missing: list[str] = []
    for cat, kinds in sc.items():
        ks = []
        for kind, points in kinds.items():
            rs = []
            for pt in points:
                r = ratio_of(pt)
                if r is None:
                    missing.append(pt.get("name")); continue
                rs.append(r); per_file[pt.get("name")] = round(r, 4)
            if rs:
                g = geomean(rs); kind_scores[f"{cat}/{kind}"] = round(g, 4); ks.append(g)
        if ks:
            cat_scores[cat] = geomean(ks)
    n_total = sum(len(p) for ks in sc.values() for p in ks.values())
    headline = geomean(list(cat_scores.values())) if cat_scores else float("nan")
    return {
        "squishy_score": round(headline, 4) if cat_scores else float("nan"),
        "score_aggregation": "nested geomean size→kind→category over compressibility-scored "
                             "files (below-plane media excluded; equal weight per level)",
        "categories": {c: round(v, 4) for c, v in cat_scores.items()},
        "kinds": kind_scores,
        "per_file": per_file,
        "n_scored": n_total,
        "n_done": len(per_file),
        "missing": missing,
        "complete": len(missing) == 0 and len(per_file) == n_total,
    }


def _core_props() -> dict[str, dict]:
    """Per-core-file intrinsic byte properties (entropy/coverage/…), keyed by display
    name, from build/meta/file-properties.json — used to apply the compressibility gate."""
    p = REPO / "build" / "meta" / "file-properties.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text()).get("files", {})


def _collect(ratio_fn) -> dict:
    """Apply ratio_fn(set, name) -> ratio|None over the core; return structured result.

    A file enters the headline only if it is intrinsically compressible (the
    compressibility plane, see is_scored); non-scored files (entropy-coded media) are
    still measured and reported as diagnostics, never in the Squishy Score. The
    headline is the nested geomean size→kind→category — for the local core each kind
    is single-size, so it reduces to category-balanced geomean over scored files."""
    props = _core_props()
    cats: dict[str, list[float]] = {}
    all_r: list[float] = []
    per_file: dict[str, float] = {}
    diagnostic: dict[str, float] = {}     # measured but non-scored (below the plane)
    missing: list[str] = []
    expansions: list[str] = []
    for cat, files in CORE.items():
        rs = []
        for display, s, name in files:
            r = ratio_fn(s, name)
            if r is None:
                missing.append(display)
                continue
            per_file[display] = round(r, 3)
            if not is_scored(props.get(display)):
                diagnostic[display] = round(r, 3)      # incompressible → out of the score
                continue
            if r < 1.0:
                expansions.append(display)
            rs.append(r); all_r.append(r)
        cats[cat] = rs
    bounds = {}
    for s, name in BOUNDS:
        r = ratio_fn(s, name)
        if r is not None:
            bounds[f"{s}/{name}"] = round(r, 3)
    # Headline = equal-weight nested geomean (size→kind→category) over scored files.
    # Each category counts 1/N regardless of how many files sit under it; categories
    # with no scored file drop out rather than poison the mean.
    cat_scores = {c: (geomean(rs) if rs else float("nan")) for c, rs in cats.items()}
    scored = [v for v in cat_scores.values() if not math.isnan(v)]
    headline = geomean(scored) if scored else float("nan")
    return {
        "squishy_score": round(headline, 3) if scored else float("nan"),
        # The Squishy Score is a dimensionless quality index (a balanced geomean of
        # ratios), NOT a bit rate — do not derive bpb from it. The operational bit
        # rate is `corpus_bpb` (byte-weighted), added by _add_byte_weighted().
        "score_aggregation": "equal-weight geomean of per-category geomeans over "
                             "compressibility-scored files (nested size→kind→category); "
                             "files below the compressibility plane are diagnostic-only",
        "n_files": len(all_r),
        "categories": {c: (round(v, 3) if not math.isnan(v) else None) for c, v in cat_scores.items()},
        "diagnostic_non_scored": diagnostic,
        "per_file": per_file,
        "bounds": bounds,
        "missing": missing,
        "expansions": expansions,
    }


def score_panel() -> dict[str, dict]:
    results = {}
    for codec, suffix in PANEL.items():
        tot_in = 0
        tot_out = 0.0
        def rf(s, name, suffix=suffix):
            nonlocal tot_in, tot_out
            r = raw_size(s, name)
            c = comp_size_individual(s, name, suffix)
            if r and c:
                tot_in += r; tot_out += c
                return r / c
            return None
        results[codec] = _collect(rf)
        _add_byte_weighted(results[codec], tot_in, tot_out)
    return results


def _live_ratio(cmd: str, p: Path):
    """Compress p with cmd; return (ratio, nbytes, secs) or None for a
    missing OR empty file. Empty→None keeps `bench` consistent with `board`
    (an empty core file is invalid, never scored as ratio 0)."""
    if not p.exists():
        return None
    data = p.read_bytes()
    if not data:               # 0-byte file → invalid, treat as missing
        return None
    csize, secs = run_codec_live(cmd, data)
    if not csize:
        return None
    return len(data) / csize, len(data), secs


def score_cmd(cmd: str) -> dict:
    times: list[tuple[int, float]] = []
    tot_in = 0
    tot_out = 0.0
    def rf(s, name):
        nonlocal tot_in, tot_out
        out = _live_ratio(cmd, raw_path(s, name))
        if out is None:
            return None
        ratio, nbytes, secs = out
        times.append((nbytes, secs))
        tot_in += nbytes
        tot_out += nbytes / ratio                 # = compressed bytes for this file
        return ratio
    res = _collect(rf)
    _add_byte_weighted(res, tot_in, tot_out)
    tot_bytes = sum(b for b, _ in times)
    tot_secs = sum(s for _, s in times)
    res["compress_MBps"] = round(tot_bytes / 1e6 / tot_secs, 1) if tot_secs else None
    res["note_speed"] = "informational only — NOT part of the canonical score (not cross-machine reproducible)"
    return res


def _add_byte_weighted(res: dict, tot_in: int, tot_out: float) -> None:
    """Attach the *true* byte-weighted corpus numbers — total bytes in/out, the
    byte-weighted ratio, and the real corpus bits-per-byte (8·out/in). This is the
    operational bpb the literature uses (total compressed ÷ total input); it is
    distinct from the Squishy Score, which is a category-balanced geomean of
    per-file ratios. Both are reported so neither is mistaken for the other."""
    res["total_in_bytes"] = int(tot_in)
    res["total_out_bytes"] = int(round(tot_out))
    res["byte_weighted_ratio"] = round(tot_in / tot_out, 3) if tot_out else None
    res["corpus_bpb"] = round(8.0 * tot_out / tot_in, 3) if tot_in else None


def round_trip_ok(comp_cmd: str, decomp_cmd: str, data: bytes) -> bool:
    """Compress then decompress `data`; True iff the bytes survive exactly.
    Supports stdin→stdout codecs and {in}/{out} file-arg codecs. Used by
    `bench --verify` and by squishy-calculate to prove losslessness."""
    with tempfile.TemporaryDirectory() as d:
        cp = os.path.join(d, "c"); dp = os.path.join(d, "d")
        if "{in}" in comp_cmd:
            ip = os.path.join(d, "in"); open(ip, "wb").write(data)
            run = comp_cmd.replace("{in}", ip).replace("{out}", cp)
            r = subprocess.run(run, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            comp = open(cp, "rb").read() if "{out}" in comp_cmd and os.path.exists(cp) else r.stdout
        else:
            comp = subprocess.run(comp_cmd, shell=True, input=data, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL).stdout
        if "{in}" in decomp_cmd:
            open(cp, "wb").write(comp)
            run = decomp_cmd.replace("{in}", cp).replace("{out}", dp)
            r = subprocess.run(run, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            back = open(dp, "rb").read() if "{out}" in decomp_cmd and os.path.exists(dp) else r.stdout
        else:
            back = subprocess.run(decomp_cmd, shell=True, input=comp, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL).stdout
    return back == data


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not math.isnan(x)


def print_board(results: dict[str, dict]) -> None:
    cats = list(CORE.keys())
    any_r = next(iter(results.values()))
    n_core = sum(len(v) for v in CORE.values())
    valid = {c: r for c, r in results.items() if _num(r["squishy_score"])}
    if valid:
        print(f"\n{'codec':<13} {'SQUISHY×':>8} {'corpus bpb':>11}   " +
              "  ".join(f"{c[:10]:>10}" for c in cats))
        print("-" * (13 + 21 + 12 * len(cats)))
        for codec, r in sorted(valid.items(), key=lambda kv: -kv[1]["squishy_score"]):
            cells = "  ".join((f"{r['categories'][c]:>8.2f}×" if _num(r['categories'][c])
                               else f"{'—':>9}") for c in cats)
            cbpb = r.get("corpus_bpb")
            print(f"{codec:<13} {r['squishy_score']:>7.2f}× {(f'{cbpb:.3f}' if cbpb else '—'):>11}   {cells}")
        print(f"\nSquishy Score = category-balanced geomean of per-file ratios (dimensionless, not a bit rate).")
        print(f"corpus bpb = byte-weighted total compressed÷input bits/byte (the operational rate).")
        best = max(valid.items(), key=lambda kv: kv[1]["squishy_score"])
        print(f"Squishiest in panel: {best[0]} at {best[1]['squishy_score']:.2f}x")
        print("\nBounds (synthetic/incompressible — NOT in score):")
        for f, v in any_r["bounds"].items():
            print(f"  {f} = {v:.3f}x ({'expansion' if v < 1 else 'passthrough'})")
    if any_r["missing"]:
        print(f"\n⚠ CORE INCOMPLETE: {len(set(any_r['missing']))}/{n_core} core files "
              f"missing — score is NOT canonical. Missing: {sorted(set(any_r['missing']))}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Squishy Score runner")
    sub = ap.add_subparsers(dest="cmd_name", required=True)
    pb = sub.add_parser("board", help="score the built-in reference panel")
    pb.add_argument("--json", type=Path, help="also write machine-readable scores here")
    pc = sub.add_parser("bench", help="score one codec command live")
    pc.add_argument("--cmd", required=True, help='e.g. "gzip -9 -c" or "./gzippy -c"')
    pc.add_argument("--verify", action="store_true",
                    help="round-trip each file to prove the codec is lossless (needs --decompress)")
    pc.add_argument("--decompress", help='decompressor for --verify, e.g. "gzip -dc"')
    pc.add_argument("--json", type=Path)
    args = ap.parse_args()

    n_core = sum(len(v) for v in CORE.values())
    # Fail closed: never score bytes we can't verify against the published hashes.
    ck = REPO / "build" / "meta" / "CHECKSUMS.sha256"
    if not ck.exists() and not os.environ.get("SQUISHY_ALLOW_UNVERIFIED"):
        print("⚠ build/meta/CHECKSUMS.sha256 not found — refusing to score "
              "unverified bytes (fail closed). Fetch it with the corpus, or set "
              "SQUISHY_ALLOW_UNVERIFIED=1 for local development.", file=sys.stderr)
        return 2
    altered = verify_core_checksums()
    if altered:
        print(f"⚠ CORE ALTERED: {altered} fail sha256 vs CHECKSUMS — refusing to score.",
              file=sys.stderr)
        return 2
    if getattr(args, "verify", False) and not args.decompress:
        print("⚠ --verify requires --decompress \"<cmd>\".", file=sys.stderr)
        return 2
    if args.cmd_name == "board":
        results = score_panel()
        print_board(results)
        print("\nRules: RULES.md — canonical run = one codec, one setting, all files.")
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            versions = tool_versions()
            for codec, r in results.items():
                r["codec_version"] = versions.get(PANEL_TOOL.get(codec, ""), "UNKNOWN")
                r["codec_command"] = PANEL_ARGV.get(codec, codec)
            missing = sorted(set(next(iter(results.values()))["missing"]))
            args.json.write_text(json.dumps({
                "score_definition": "equal-weight geomean of per-category geomeans (nested size→kind→category)",
                "edition": "Squishy-2026-DRAFT",
                "corpus_files": n_core,
                "missing": missing,
                "status": ("DRAFT — NOT CITABLE. Partial board: small members only, "
                           "large rungs pending — not yet a Squishy Score. "
                           "Numbers are properties of (corpus, codec, codec_version) — "
                           "reproducible only for the pinned builds recorded per row."),
                "panel": results,
            }, indent=2) + "\n")
            print(f"\nwrote {args.json}")
        missing = next(iter(results.values()))["missing"]
        return 1 if missing else 0
    else:  # bench
        if args.verify:
            for files in CORE.values():
                for display, s, name in files:
                    p = raw_path(s, name)
                    if not p.exists():
                        continue
                    if not round_trip_ok(args.cmd, args.decompress, p.read_bytes()):
                        print(f"⚠ ROUND-TRIP FAILED for {display} — codec is not lossless; "
                              f"no valid Squishy Score.", file=sys.stderr)
                        return 3
            print("round-trip: ✓ lossless on all present core files")
        res = score_cmd(args.cmd)
        res["round_trip_verified"] = bool(args.verify)
        # One corpus, one number: only a run over the complete corpus prints a
        # Squishy Score; a partial run prints per-file ratios for regression use.
        if res["missing"]:
            print(f"\npartial run ({res['n_files']}/{n_core} files) — per-file ratios for "
                  f"your own regression use; NOT a Squishy Score.")
            for d, r in res["per_file"].items():
                print(f"  {d:<16} {r:>6.2f}x")
        else:
            print(f"\nSquishy Score: {res['squishy_score']:.2f}×   [{res['n_files']}/{n_core} files]"
                  f"   (category-balanced geomean of per-file ratios)")
            if res.get("corpus_bpb") is not None:
                print(f"  corpus bpb (byte-weighted, total out÷in): {res['corpus_bpb']:.3f}  "
                      f"[{res['total_in_bytes']/1e6:.0f}→{res['total_out_bytes']/1e6:.0f} MB]")
            for c, v in res["categories"].items():
                if v is not None:
                    print(f"  {c:<16} {v:>6.2f}×")
        if res.get("compress_MBps"):
            print(f"  compress: {res['compress_MBps']} MB/s ({res['note_speed']})")
        print("\nRules: RULES.md — one codec, one setting, the whole corpus.")
        if args.json:
            args.json.write_text(json.dumps(res, indent=2) + "\n")
        return 1 if res["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
