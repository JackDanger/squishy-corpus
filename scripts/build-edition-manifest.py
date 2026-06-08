#!/usr/bin/env python3
"""Generate build/meta/edition.json — the authoritative, per-file, URL-addressable
manifest for the Squishy edition. One row per distributed file with everything a
consumer needs to fetch + verify a single artifact by name:

  name, display, kind, category, tier (core|scale), size_bytes, sha256,
  url (HTTPS), license, source_url

Derived entirely from the single sources of truth — scripts/squishy.py:CORE,
build/meta/CHECKSUMS.sha256, build/meta/LICENSE-MANIFEST.csv, and (for the scale
tier) build/meta/scale-properties.json — so it cannot drift from the product.

  uv run python scripts/build-edition-manifest.py
"""
from __future__ import annotations
import csv, importlib.util, json, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = "https://squishy.jackdanger.com"
EDITION = "Squishy-2026-DRAFT"
# which core category a scale kind belongs to (for the by-category roll-up)
SCALE_CATEGORY = {"csv": "Tabular / DB", "columnar": "Tabular / DB", "parquet": "Tabular / DB",
                  "sqlite": "Tabular / DB",
                  "genome": "Structured", "log": "Structured", "json": "Structured",
                  "text": "Prose", "prose": "Prose", "monorepo": "Code & Web",
                  "markup": "Code & Web", "archive": "Code & Web",
                  "media": "Binary & Media", "weights": "Binary & Media"}


def _props(measured: dict, sq) -> dict:
    """Carry the intrinsic byte axes (entropy/coverage/match-distance/size) into each
    edition entry, so the scorer and the webpage's 3-D map both read one file. There is
    no compressibility gate any more — every file is scored (one vote per file)."""
    if not measured:
        return {}
    return {k: measured[k] for k in ("entropy", "coverage", "match_distance",
                                     "match_distance_p90", "size") if k in measured}


def main() -> int:
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    man = {r["core_slot"]: r for r in csv.DictReader((REPO / "build/meta/LICENSE-MANIFEST.csv").open())}
    man_by_name = {r["name"]: r for r in csv.DictReader((REPO / "build/meta/LICENSE-MANIFEST.csv").open())}
    sums = {}
    for line in (REPO / "build/meta/CHECKSUMS.sha256").read_text().splitlines():
        p = line.split()
        if len(p) == 2:
            sums[p[1]] = p[0]                       # "corpus/<name>" -> sha256

    core_props = json.loads((REPO / "build/meta/file-properties.json").read_text()).get("files", {}) \
        if (REPO / "build/meta/file-properties.json").exists() else {}

    files = []
    # core tier — from CORE (the named small members)
    for cat, members in sq.CORE.items():
        for display, st, name in members:
            key = f"{st}/{name}"
            p = sq.raw_path(st, name)
            m = man.get(display, {})
            files.append({
                "name": name, "display": display, "kind": display, "category": cat,
                "tier": "core",
                "size_bytes": p.stat().st_size if p.exists() else None,
                "sha256": sums.get(key), "key": key, "url": f"{BASE}/{key}",
                "license": m.get("license"), "source_url": m.get("source_url"),
                **_props(core_props.get(display, {}), sq),
            })
    # scale tier — LICENSE-MANIFEST is the set-of-record (every distributed scale
    # file, incl. the weights ladder); merge measured byte props from scale-properties.
    measured = json.loads((REPO / "build/meta/scale-properties.json").read_text()).get("files", {}) \
        if (REPO / "build/meta/scale-properties.json").exists() else {}
    for row in csv.DictReader((REPO / "build/meta/LICENSE-MANIFEST.csv").open()):
        slot = row["core_slot"]
        if not slot.startswith("scale-"):
            continue
        name = row["name"]; kind = slot[len("scale-"):]
        v = measured.get(name, {})
        key = (v.get("key") or f"scale/{kind}/{name}")
        if key.startswith("draft/"):                # normalize to base-relative, like core keys
            key = key[len("draft/"):]
        files.append({
            "name": name, "display": name, "kind": kind,
            "category": SCALE_CATEGORY.get(kind, "Scale tier"), "tier": "scale",
            "size_bytes": int(row["size_bytes"]) if row.get("size_bytes") else v.get("size"),
            "sha256": row.get("sha256") or v.get("sha256"),
            "key": key, "url": f"{BASE}/{key}",
            "license": row.get("license"), "source_url": row.get("source_url"),
            **_props(v, sq),
        })

    out = {
        "edition": EDITION,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": BASE,
        "note": ("Per-file addressable inventory. tier=core are the scored named files; "
                 "tier=scale are large members (the scored size-rung set is finalized at freeze). "
                 "Fetch any file by its url and verify against sha256."),
        "n_files": len(files),
        "total_bytes": sum(f["size_bytes"] or 0 for f in files),
        "files": files,
    }
    dst = REPO / "build" / "meta" / "edition.json"
    dst.write_text(json.dumps(out, indent=2) + "\n")
    core = sum(1 for f in files if f["tier"] == "core")
    scale = sum(1 for f in files if f["tier"] == "scale")
    print(f"wrote {dst}: {core} core + {scale} scale = {len(files)} files, "
          f"{out['total_bytes']/1e9:.2f} GB")
    missing_sha = [f["name"] for f in files if not f["sha256"]]
    if missing_sha:
        print(f"  ⚠ no sha256 for: {missing_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
