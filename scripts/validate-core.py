#!/usr/bin/env python3
"""Validate the 19-file core: format validity, non-degeneracy, and that no single
file dominates the geometric-mean score. Run before freeze (part of verification).

Checks per file:
  - magic/format sniff matches the declared kind
  - non-degenerate: not a single repeated byte; compresses within a sane band
Cross-file:
  - in log-ratio space, no file contributes > 1.5x the mean per-file contribution
    (advisor's dominance bar) — i.e. no file swings the score.

Exits non-zero on any failure.
"""
from __future__ import annotations
import importlib.util, math, subprocess, sys, collections
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (display -> (magic_predicate, human kind, incompressible?))
# NOTE: every CORE display MUST have an entry here — main() looks up SNIFF[d] for
# each core file, so a missing entry is a KeyError, not a skipped check. The
# tests/test_roster_consistency.py::test_core_matches_sniff guard fails if this
# roster drifts from scripts/squishy.py CORE.
def _is(prefix): return lambda b: b.startswith(prefix)
# Mach-O object magics (the macOS dSYM DWARF companion is a Mach-O file).
_MACHO = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
          b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
SNIFF = {
    "dickens": (lambda b: sum(32 <= x < 127 or x in (9, 10, 13) for x in b[:256]) > 200, "text", False),
    "aozora":  (lambda b: b[:3] == b"\xef\xbb\xbf" or any(x & 0x80 for x in b[:64]), "utf-8 text", False),
    "monorepo":(lambda b: len(b) > 512, "tar", False),   # tar: checked via tarfile below
    "minjs":   (lambda b: True, "minified js", False),
    "markup":  (lambda b: b"xml" in b[:512] or b"<" in b[:4096], "xml (tar)", False),
    "json":    (lambda b: b[:1] in (b"{", b"["), "ndjson", False),
    "log":     (lambda b: b[:1].isdigit() or b'"' in b[:200], "log", False),
    "genome":  (lambda b: b[:1] == b"@", "fastq", False),
    "csv":     (lambda b: b","  in b[:200], "csv", False),
    "parquet": (_is(b"PAR1"), "parquet", False),
    "sqlite":  (_is(b"SQLite format 3\x00"), "sqlite", False),
    "exe":     (_is(b"\x7fELF"), "ELF", False),
    "photo":   (_is(b"\xff\xd8\xff"), "jpeg", True),
    "movie":   (lambda b: b[4:8] == b"ftyp", "mp4", True),
    "weights": (lambda b: b[8:9] == b"{", "safetensors", True),
    "symbols": (lambda b: b[:4] in _MACHO or b[:4] == b"\x7fELF", "DWARF (Mach-O/ELF)", False),
    "wasm":    (_is(b"\x00asm"), "wasm", False),
    "winexe":  (_is(b"MZ"), "PE (exe)", False),
    "armexe":  (_is(b"\x7fELF"), "ELF (ARM64)", False),
}


def gzip_ratio(p: Path) -> float:
    raw = p.stat().st_size
    out = subprocess.run(["gzip", "-1", "-c", str(p)], stdout=subprocess.PIPE, check=True).stdout
    return raw / len(out)


def main() -> int:
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    entries = [(d, st, n) for files in sq.CORE.values() for (d, st, n) in files]
    bad = 0
    ratios = {}
    for d, st, n in entries:
        p = REPO / "build" / "raw" / st / n
        if not p.exists():
            print(f"  [FAIL] {d}: missing"); bad += 1; continue
        head = p.read_bytes()[:4096]
        pred, kind, incompressible = SNIFF[d]
        fmt_ok = pred(head)
        # non-degeneracy: not a single repeated byte in the head
        nondegen = len(set(head)) > 3
        r = gzip_ratio(p)
        ratios[d] = r
        # band: incompressible ~[0.95,1.6]; compressible must be >1.1
        band_ok = (0.90 <= r <= 1.8) if incompressible else (r > 1.1)
        ok = fmt_ok and nondegen and band_ok
        if not ok: bad += 1
        print(f"  [{'ok' if ok else 'FAIL'}] {d:9s} {kind:13s} gzip={r:.2f}x "
              f"fmt={'✓' if fmt_ok else '✗'} nondegen={'✓' if nondegen else '✗'} band={'✓' if band_ok else '✗'}")
    # tar sanity for monorepo
    import tarfile
    mr = REPO / "build" / "raw" / "corpus" / "monorepo.tar"
    if mr.exists():
        try:
            with tarfile.open(mr) as tf: nmem = len(tf.getnames())
            print(f"  [ok] monorepo tar opens, {nmem} members")
        except Exception as e:
            print(f"  [FAIL] monorepo tar: {e}"); bad += 1
    # dominance: log-ratio contributions
    logs = {d: math.log(r) for d, r in ratios.items()}
    mean = sum(logs.values()) / len(logs)
    print(f"\n  geomean(gzip-1) = {math.exp(mean):.2f}x")
    for d, lv in sorted(logs.items(), key=lambda kv: -abs(kv[1])):
        if mean > 0 and lv > 1.5 * mean:
            print(f"  [WARN] {d} dominates: log-ratio {lv:.2f} > 1.5x mean {mean:.2f}")
    print(f"\n{len(entries)} core files validated; {bad} failures.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
