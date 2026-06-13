#!/usr/bin/env python3
"""Emit build/meta/baseline.json — the committed golden record the whole pipeline is
verified against. It pins, for the current edition, ONLY edition-immutable facts:
  • the sha256 of every corpus file (from edition.json),
  • the scored-set fingerprint (names/shas/kinds/categories),
  • the toolchain versions that make derived files reproduce byte-for-byte.

It deliberately does NOT pin any codec's Squishy Score: a score is a measurement of
(codec, version, argv) and changes when a codec ships a new release, so freezing one
here would make the immutable golden record go stale the day zstd updates. Codec
scores live with the boards (build/meta/squishy-score-complete.json / squishy-scores.json),
each fully version-stamped and re-run when codecs move.

`scripts/run-all.sh` regenerates everything from scratch and diffs against this file,
so "end-to-end verification" is an equality check, not an eyeball. Re-run this only
when the edition deliberately changes (and review the diff).

  uv run python scripts/build-baseline.py
"""
from __future__ import annotations
import hashlib, importlib.util, json, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _pyarrow_version() -> str | None:
    try:
        out = subprocess.run([sys.executable, "-c", "import pyarrow,sys;sys.stdout.write(pyarrow.__version__)"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None
    except Exception:
        return None


def main() -> int:
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    ed_path = REPO / "build/meta/edition.json"
    ed = json.loads(ed_path.read_text())
    files = {f["name"]: f["sha256"] for f in ed["files"]}
    # deterministic fingerprint of the edition's scored set (independent of the
    # generated_utc timestamp in edition.json), so re-generation diffs cleanly.
    fp = [(f["name"], f["sha256"], f["kind"], f["category"])
          for f in sorted(ed["files"], key=lambda x: x["name"])]
    scored_set_fingerprint = hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()

    baseline = {
        "edition": ed.get("edition"),
        "schema_version": ed.get("schema_version"),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_files": len(files),
        "n_scored_size_points": sum(len(p) for ks in sq.scored_corpus().values() for p in ks.values()),
        "scored_set_fingerprint": scored_set_fingerprint,
        "files_sha256": files,
        "reproducibility_toolchain": {
            "note": "derived files (clang archive concat, BTS all-string parquet, NOAA csv concat) "
                    "reproduce byte-identical with these pins; verified by run-all.sh.",
            "pyarrow": _pyarrow_version(),
            "python": sq.host_provenance()["python"],
            "host": sq.host_provenance(),
        },
    }
    dst = REPO / "build/meta/baseline.json"
    dst.write_text(json.dumps(baseline, indent=2) + "\n")
    print(f"wrote {dst}: {len(files)} files pinned, scored-set {scored_set_fingerprint[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
