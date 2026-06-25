#!/usr/bin/env python3
"""Emit build/meta/CHECKSUMS.sha256 — the published trust-root for EVERY distributed
file (all 30: the named core AND the scale tier), so the one-line downloader can
`sha256sum -c` the whole edition, not just the core.

Derived from build/meta/edition.json (which sources each file's authoritative sha256
from build/meta/LICENSE-MANIFEST.csv). One `"<sha256>  <key>"` line per file, sorted
by key for a stable diff. `edition.json` does NOT depend on this file (it reads sha
from the manifest), so regenerating here is downstream and never circular.

  uv run python scripts/build-checksums.py
"""
from __future__ import annotations
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    ed = json.loads((REPO / "build/meta/edition.json").read_text())
    rows = sorted(((f["sha256"], f["key"]) for f in ed["files"]), key=lambda r: r[1])
    missing = [k for sha, k in rows if not sha]
    if missing:
        raise SystemExit(f"ERROR: edition.json has no sha256 for: {missing}")
    text = "".join(f"{sha}  {key}\n" for sha, key in rows)
    dst = REPO / "build/meta/CHECKSUMS.sha256"
    dst.write_text(text)
    print(f"wrote {dst}: {len(rows)} files "
          f"({sum(1 for _, k in rows if k.startswith('corpus/'))} core + "
          f"{sum(1 for _, k in rows if k.startswith('scale/'))} scale)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
