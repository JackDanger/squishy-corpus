#!/usr/bin/env python3
"""Re-measure already-uploaded scale files WHOLE-FILE (exact), replacing any
384 MB-head numbers left in build/meta/scale-properties.json. Streams each object
back down from its published key, measures with file-properties.measure() (whole
file, every byte), rewrites its scale-properties entry (dropping scanned_prefix/
scanned_bytes), and deletes the local copy. One file's footprint at a time.

  uv run python scripts/scale-remeasure.py NAME [NAME ...]   # specific files
  uv run python scripts/scale-remeasure.py --stale           # only scanned_prefix ones
"""
from __future__ import annotations
import argparse, importlib.util, json, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUCKET = "squishy-corpus"
WORK_PREFIX = "draft"   # S3 working prefix the live bytes are served from (never frozen into metadata)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--stale", action="store_true", help="re-measure every entry still flagged scanned_prefix")
    a = ap.parse_args()
    sp = REPO / "build/meta/scale-properties.json"
    data = json.loads(sp.read_text())
    files = data["files"]
    targets = list(a.names)
    if a.stale:
        targets += [n for n, v in files.items() if v.get("scanned_prefix")]
    targets = [t for t in dict.fromkeys(targets) if t in files]
    if not targets:
        print("nothing to do"); return 0

    s = importlib.util.spec_from_file_location("fp", REPO / "scripts" / "file-properties.py")
    fp = importlib.util.module_from_spec(s); s.loader.exec_module(fp)
    tmp = REPO / "build" / "raw" / "scale" / "_remeasure"
    tmp.mkdir(parents=True, exist_ok=True)
    for name in targets:
        v = files[name]
        # Stored key is edition-relative (e.g. scale/text/enwik9.txt) — never the working
        # prefix; the live bytes are fetched from WORK_PREFIX/<key>. Tolerate a legacy
        # draft/-prefixed key still sitting in the file.
        key = v["key"]
        rel_key = key[len(WORK_PREFIX) + 1:] if key.startswith(WORK_PREFIX + "/") else key
        local = tmp / name
        print(f"  ↓ {WORK_PREFIX}/{rel_key} ...", flush=True)
        subprocess.run(["aws", "s3", "cp", f"s3://{BUCKET}/{WORK_PREFIX}/{rel_key}", str(local), "--no-progress"], check=True)
        props = fp.measure(local)
        local.unlink()
        merged = {**props, "category": v.get("category", "Scale tier"), "key": rel_key, "sha256": v.get("sha256")}
        files[name] = merged                 # whole-file props; scanned_prefix/_bytes dropped
        sp.write_text(json.dumps(data, indent=2) + "\n")
        print(f"  ✓ {name}: {props}", flush=True)
    try:
        tmp.rmdir()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
