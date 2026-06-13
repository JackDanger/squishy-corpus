#!/usr/bin/env python3
"""Generate build/meta/edition.json — the authoritative, per-file, URL-addressable
manifest for the Squishy edition. One row per distributed file with everything a
consumer needs to fetch + verify a single artifact by name:

  name, display, kind, category, role, cell, lineage, scored, size_bytes, sha256,
  url (HTTPS), license, source_url, origin (upstream|minted)

The SCORED ROSTER is defined by build/meta/schema.json (the constitution): each
`cell` there is exactly one scored file (one vote); its `diagnostics` are distributed
but never scored. This builder joins schema cells + diagnostics against the byte
sources of truth and stamps every row with `scored` so the scorer never has to infer
the roster:

  - schema.json                      → which files are cells (scored) vs diagnostics
  - scripts/publish-corpus.py RECIPES → per-member origin (upstream|minted)
  - build/meta/CHECKSUMS.sha256       → sha256
  - build/meta/LICENSE-MANIFEST.csv   → size_bytes, license, source_url (set-of-record)
  - build/meta/file-properties.json /
    build/meta/scale-properties.json  → intrinsic byte axes for the coverage map

Sizes come from LICENSE-MANIFEST (not from stat()) so the manifest regenerates
identically whether or not the raw bytes are present locally.

  uv run python scripts/build-edition-manifest.py
"""
from __future__ import annotations
import csv, importlib.util, json, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = "https://squishy.jackdanger.com"


def _squishy():
    """Load the scoring core for the canonical EDITION string (single source of truth)."""
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m


def _props(measured: dict) -> dict:
    """Carry the intrinsic byte axes (entropy/coverage/match-distance) into each entry so
    the webpage's 3-D map can read one file. (Descriptive only — never a weight in the
    score; the scored set is the schema's cells. `size` is omitted on purpose: the
    authoritative byte count is `size_bytes`, and carrying both invites drift.)"""
    if not measured:
        return {}
    return {k: measured[k] for k in ("entropy", "coverage", "match_distance",
                                     "match_distance_p90") if k in measured}


def main() -> int:
    schema = json.loads((REPO / "build/meta/schema.json").read_text())
    cells = {c["file"]: c for c in schema["cells"]}
    diagnostics = {d["file"]: d for d in schema["diagnostics"]}

    # Provenance class (upstream|minted) is owned by publish-corpus.py RECIPES — the
    # single source of truth for how each member is produced. Derive it here so the
    # manifest's `origin` field can never drift from the mint/publish contract.
    p = importlib.util.spec_from_file_location("pc", REPO / "scripts" / "publish-corpus.py")
    pc = importlib.util.module_from_spec(p); p.loader.exec_module(pc)

    rows = list(csv.DictReader((REPO / "build/meta/LICENSE-MANIFEST.csv").open()))
    by_name = {r["name"]: r for r in rows}

    sums = {}
    for line in (REPO / "build/meta/CHECKSUMS.sha256").read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1]] = parts[0]                # "corpus/<name>" -> sha256

    core_props = json.loads((REPO / "build/meta/file-properties.json").read_text()).get("files", {}) \
        if (REPO / "build/meta/file-properties.json").exists() else {}
    scale_props = json.loads((REPO / "build/meta/scale-properties.json").read_text()).get("files", {}) \
        if (REPO / "build/meta/scale-properties.json").exists() else {}

    def key_of(name: str, row: dict, kind: str) -> str:
        """S3/base-relative key. Small named members live under corpus/<name>; large
        rungs under scale/<kind>/<name> (mirrors the source-of-record layout)."""
        slot = row.get("core_slot", "")
        if slot.startswith("scale-"):
            v = scale_props.get(name, {})
            k = v.get("key") or f"scale/{kind}/{name}"
            return k[len("draft/"):] if k.startswith("draft/") else k
        return f"corpus/{name}"

    def props_for(name: str, display: str) -> dict:
        return _props(scale_props.get(name) or core_props.get(display) or {})

    files = []
    for name, row in by_name.items():
        cell = cells.get(name)
        diag = diagnostics.get(name)
        if not cell and not diag:
            # A distributed file that is neither a scored cell nor a declared
            # diagnostic is a roster error — surface it rather than silently ship it.
            print(f"  ⚠ {name} is in LICENSE-MANIFEST but not in schema.json (cell or diagnostic)")
            continue
        kind = (cell or diag).get("kind")
        category = cell["category"] if cell else None
        key = key_of(name, row, kind)
        display = cell["id"] if cell else name
        entry = {
            "name": name,
            "display": display,
            "kind": kind,
            "category": category,
            "scored": bool(cell),
            "role": cell["role"] if cell else "diagnostic",
            "cell": cell["id"] if cell else None,
            "lineage": cell.get("lineage") if cell else None,
            "size_bytes": int(row["size_bytes"]) if row.get("size_bytes") else None,
            "sha256": row.get("sha256") or sums.get(key),
            "key": key,
            "url": f"{BASE}/{key}",
            "license": row.get("license"),
            "source_url": row.get("source_url"),
            "origin": pc.rec_of(key)["origin"],
            **props_for(name, display),
        }
        if cell and "scales" in cell:
            entry["scales"] = cell["scales"]
        if diag:
            entry["diagnostic_reason"] = diag["reason"]
        files.append(entry)

    # Stable order: scored cells in schema order, then diagnostics.
    cell_order = {c["file"]: i for i, c in enumerate(schema["cells"])}
    files.sort(key=lambda f: (not f["scored"], cell_order.get(f["name"], 1_000_000), f["name"]))

    scored = [f for f in files if f["scored"]]
    out = {
        "edition": _squishy().EDITION,
        "schema_version": schema.get("schema_version"),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": BASE,
        "note": ("Per-file addressable inventory. `scored:true` files are the corpus "
                 "cells (one vote each in the Squishy Score; roster defined by "
                 "build/meta/schema.json); `scored:false` files are distributed "
                 "diagnostics (throughput ladder, incompressible/redundant rungs) that "
                 "never enter the score. Fetch any file by its url and verify against sha256."),
        "n_files": len(files),
        "n_scored": len(scored),
        "scored_bytes": sum(f["size_bytes"] or 0 for f in scored),
        "total_bytes": sum(f["size_bytes"] or 0 for f in files),
        "files": files,
    }
    dst = REPO / "build" / "meta" / "edition.json"
    dst.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {dst}: {len(scored)} scored cells + {len(files) - len(scored)} diagnostics "
          f"= {len(files)} files, {out['total_bytes']/1e9:.2f} GB "
          f"({out['scored_bytes']/1e9:.2f} GB scored)")
    missing_sha = [f["name"] for f in files if not f["sha256"]]
    if missing_sha:
        print(f"  ⚠ no sha256 for: {missing_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
