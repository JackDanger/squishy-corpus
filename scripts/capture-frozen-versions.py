#!/usr/bin/env python3
"""capture-frozen-versions.py — AFTER the freeze, record the exact immutable identity of
every object in the frozen prefix: key, size, x-amz-meta-sha256, and S3 VersionId. The
bucket has versioning enabled, so a VersionId addresses an exact, immutable object even
if the key is later overwritten. Writes build/meta/frozen-manifest.json, which
zenodo-deposit.py deposits as provenance (--frozen-manifest) so the DOI pins each
artifact to a precise versioned S3 location — belt-and-suspenders over the immutable
cache-control.

This is deposit-only PROVENANCE: it is generated AFTER the freeze (the 2026/ objects
get their own VersionIds when copied) and is therefore NOT itself part of the frozen
2026/ data set.

  uv run python scripts/capture-frozen-versions.py BUCKET [--prefix 2026]
"""
from __future__ import annotations
import argparse, importlib.util, json, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    s = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def _head(bucket: str, key: str) -> dict | None:
    r = subprocess.run(
        ["aws", "s3api", "head-object", "--bucket", bucket, "--key", key,
         "--query", "{size:ContentLength,sha:Metadata.sha256,version:VersionId,modified:LastModified}",
         "--output", "json"],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def _expected_rel_keys() -> list[str]:
    """The full frozen set, prefix-relative: 30 data + root meta + LICENSES/* — derived
    from the same sources as the freeze allowlist / zenodo META_ARTIFACTS."""
    ed = json.loads((REPO / "build/meta/edition.json").read_text())
    data = [f["key"] for f in ed["files"]]
    zen = _load("zenodo_deposit", "scripts/zenodo-deposit.py")
    meta = [p.name for p in zen.META_ARTIFACTS if p.parent.name == "meta"]
    lics = [f"LICENSES/{p.name}" for p in zen.META_ARTIFACTS if p.parent.name == "LICENSES"]
    return data + meta + lics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bucket")
    ap.add_argument("--prefix", default="2026", help="frozen prefix to capture")
    ap.add_argument("--out", type=Path, default=REPO / "build/meta/frozen-manifest.json")
    a = ap.parse_args()

    rel_keys = _expected_rel_keys()
    objects, missing, unversioned = {}, [], []
    for rk in rel_keys:
        h = _head(a.bucket, f"{a.prefix}/{rk}")
        if h is None:
            missing.append(rk); continue
        if not h.get("version") or h.get("version") == "null":
            unversioned.append(rk)
        objects[rk] = {"size": h.get("size"), "sha256": h.get("sha"),
                       "version_id": h.get("version"), "last_modified": h.get("modified")}
    if missing or unversioned:
        print(f"ERROR: cannot capture a complete versioned manifest — "
              f"{len(missing)} missing {missing}; {len(unversioned)} unversioned {unversioned}. "
              f"Freeze must be complete and the bucket versioned before capture.", file=sys.stderr)
        return 1

    ed = json.loads((REPO / "build/meta/edition.json").read_text())
    # Cross-check the FROZEN data bytes against edition.json (sha256 metadata + size): the
    # freeze is a server-side copy of draft/, and a concurrent draft overwrite mid-copy could
    # land wrong bytes in the permanent 2026/. Catch it here, BEFORE the DOI is minted.
    sha_bad, size_bad = [], []
    for f in ed["files"]:
        o = objects.get(f["key"])
        if not o:
            continue  # already caught as missing above
        if f.get("sha256") and o["sha256"] != f["sha256"]:
            sha_bad.append(f["key"])
        if f.get("size_bytes") is not None and o["size"] != f["size_bytes"]:
            size_bad.append(f["key"])
    if sha_bad or size_bad:
        print(f"ERROR: frozen {a.prefix}/ does not match edition.json — "
              f"{len(sha_bad)} sha-mismatch {sha_bad}; {len(size_bad)} size-mismatch {size_bad}. "
              f"The freeze copied wrong/changed bytes; do NOT mint.", file=sys.stderr)
        return 1
    base = json.loads((REPO / "build/meta/baseline.json").read_text())
    manifest = {
        "note": ("Exact immutable identity of every object in the frozen prefix "
                 "(key, size, sha256, S3 VersionId). Provenance for the DOI; not part of "
                 "the frozen data set itself."),
        "bucket": a.bucket,
        "prefix": a.prefix,
        "edition": ed.get("edition"),
        "scored_set_fingerprint": base.get("scored_set_fingerprint"),
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_objects": len(objects),
        "objects": dict(sorted(objects.items())),
    }
    a.out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {a.out}: {len(objects)} objects pinned to exact VersionIds "
          f"in s3://{a.bucket}/{a.prefix}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
