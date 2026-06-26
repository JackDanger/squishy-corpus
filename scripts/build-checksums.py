#!/usr/bin/env python3
"""Emit build/meta/CHECKSUMS.sha256 — the published trust-root for EVERY distributed
object, so the one-line downloader can `sha256sum -c` the WHOLE distribution: the 30
data files (named core + scale tier) AND the metadata + license tier (edition.json,
NOTICE, the boards, LICENSES/*, …). Keys mirror the served CDN paths (corpus/<name>,
scale/<kind>/<name>, <meta>, LICENSES/<name>).

Data shas come from build/meta/edition.json (authoritative, sourced from
LICENSE-MANIFEST.csv); meta/license shas are hashed from the local bytes (identical to
what deploy-site.sh pushes). CHECKSUMS.sha256 cannot list itself, so it is excluded.
`edition.json` does NOT depend on this file (it reads sha from the manifest), so
regenerating here is downstream and never circular.

  uv run python scripts/build-checksums.py
"""
from __future__ import annotations
import hashlib, importlib.util, json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    ed = json.loads((REPO / "build/meta/edition.json").read_text())
    rows = [(f["sha256"], f["key"]) for f in ed["files"]]          # 30 data files
    missing = [k for sha, k in rows if not sha]
    if missing:
        raise SystemExit(f"ERROR: edition.json has no sha256 for: {missing}")

    # The metadata + license tier — the same set freeze.sh / zenodo-deposit.py treat as
    # the frozen meta. CHECKSUMS.sha256 itself is excluded (it cannot contain its own hash).
    s = importlib.util.spec_from_file_location("zen", REPO / "scripts/zenodo-deposit.py")
    zen = importlib.util.module_from_spec(s); s.loader.exec_module(zen)
    for p in zen.META_ARTIFACTS:
        if p.name == "CHECKSUMS.sha256":
            continue
        key = p.name if p.parent.name == "meta" else f"LICENSES/{p.name}"
        rows.append((_sha256(p), key))

    rows.sort(key=lambda r: r[1])
    text = "".join(f"{sha}  {key}\n" for sha, key in rows)
    dst = REPO / "build/meta/CHECKSUMS.sha256"
    dst.write_text(text)
    n_data = sum(1 for _, k in rows if k.startswith(("corpus/", "scale/")))
    n_lic = sum(1 for _, k in rows if k.startswith("LICENSES/"))
    print(f"wrote {dst}: {len(rows)} objects "
          f"({n_data} data + {len(rows) - n_data - n_lic} meta + {n_lic} licenses)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
