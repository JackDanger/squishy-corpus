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
import argparse, hashlib, importlib.util, json, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    s = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def _head(bucket: str, key: str) -> dict | None:
    """HEAD with checksum mode → {size, sha (x-amz-meta-sha256, owner-set),
    crc64 (S3-computed CRC64NVME), version, modified}. None if absent."""
    r = subprocess.run(
        ["aws", "s3api", "head-object", "--bucket", bucket, "--key", key,
         "--checksum-mode", "ENABLED",
         "--query", "{size:ContentLength,sha:Metadata.sha256,crc64:ChecksumCRC64NVME,"
                    "version:VersionId,modified:LastModified}",
         "--output", "json"],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def _sha256_of_version(bucket: str, key: str, version_id: str) -> str | None:
    """Download an EXACT object version and SHA-256 its actual bytes. S3 verifies its own
    CRC64NVME on GET, so a clean download is provably the real object. None on error."""
    with tempfile.TemporaryDirectory(prefix="squishy-sha-") as td:
        dest = Path(td) / "obj"
        r = subprocess.run(["aws", "s3api", "get-object", "--bucket", bucket, "--key", key,
                            "--version-id", version_id, str(dest)], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        h = hashlib.sha256()
        with dest.open("rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""):
                h.update(c)
        return h.hexdigest()


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
    objects, missing, unversioned, computed = {}, [], [], 0
    for rk in rel_keys:
        full = f"{a.prefix}/{rk}"
        h = _head(a.bucket, full)
        if h is None:
            missing.append(rk); continue
        ver = h.get("version")
        if not ver or ver == "null":
            unversioned.append(rk)
        # Data files carry owner-set x-amz-meta-sha256; meta/license objects do NOT (S3
        # only stored a CRC64NVME), so compute SHA-256 from the actual frozen bytes.
        sha = h.get("sha")
        if not sha and ver and ver != "null":
            sha = _sha256_of_version(a.bucket, full, ver)
            if sha:
                computed += 1
        objects[rk] = {"size": h.get("size"), "sha256": sha,
                       "crc64nvme": h.get("crc64"),          # S3-computed integrity checksum
                       "version_id": ver, "last_modified": h.get("modified")}
    no_sha = [rk for rk, o in objects.items() if not o["sha256"]]
    if no_sha:
        print(f"WARNING: could not obtain sha256 for {len(no_sha)} objects: {no_sha}", file=sys.stderr)
    print(f"  computed sha256 for {computed} objects S3 had none for (meta/licenses)")
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
        "note": ("Exact immutable identity of every object in the frozen prefix: size, "
                 "sha256 (from x-amz-meta-sha256 for data; computed from the actual bytes for "
                 "meta/license objects, which S3 stored only a CRC64NVME for), the "
                 "S3-computed CRC64NVME, and the S3 VersionId. Provenance for the DOI; not "
                 "part of the frozen data set itself."),
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
