#!/usr/bin/env python3
"""Preflight the Squishy-2026 freeze: prove the immutable S3 prefix (2026/) and the
immutable Zenodo deposit will carry the IDENTICAL object set, byte-for-byte, and that
nothing stale or extraneous sneaks in. Run by freeze.sh before the copy; safe to run
by hand any time.

Checks (all run; exits non-zero if ANY fails):
  LOCAL
   1. freeze.sh's metadata allowlist == zenodo-deposit.py META_ARTIFACTS (the two
      immutable anchors must immortalize the same metadata set).
   2. freeze.sh allowlist carries NO presentation assets (2026/ is data-only).
   3. every META_ARTIFACT exists locally.
   4. every one of the 30 distributed data files is present AND matches its
      edition.json sha256 (so the DOI/freeze anchor the complete, correct bytes).
  S3 (when a bucket is given)
   5. the frozen prefix (2026/) is empty (the freeze must be its first write).
   6. live draft/ metadata is not stale — each metadata object's bytes equal the
      local copy that will be deposited (catches "pinned edition.json not deployed").

  uv run python scripts/preflight-freeze.py [BUCKET] [--prefix 2026] [--work draft]
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, re, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRESENTATION = {"index.html", "squishy-cube.js", "cube-data.json", "photo.jpg",
                "movie.jpg", "provenance.html", "review.html", "provenance/*"}


def _load(name: str, rel: str):
    s = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _s3_head(bucket: str, key: str) -> dict | None:
    """HEAD an S3 object → {size, sha (x-amz-meta-sha256), version}. None if absent."""
    r = subprocess.run(
        ["aws", "s3api", "head-object", "--bucket", bucket, "--key", key,
         "--query", "{size:ContentLength,sha:Metadata.sha256,version:VersionId}",
         "--output", "json"],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def _s3_list(bucket: str, prefix: str) -> set[str]:
    """Every object key under prefix, returned RELATIVE to prefix (prefix stripped)."""
    r = subprocess.run(["aws", "s3", "ls", f"s3://{bucket}/{prefix}", "--recursive"],
                       capture_output=True, text=True)
    keys = set()
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if len(parts) >= 4:
            full = parts[3]
            keys.add(full[len(prefix):] if full.startswith(prefix) else full)
    return keys


def _sha256_stream_s3(bucket: str, key: str, tmpdir: Path) -> str | None:
    """Download s3://bucket/key to a temp file, hash its ACTUAL bytes, delete. None on error."""
    dest = tmpdir / Path(key).name
    try:
        subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/{key}", str(dest), "--no-progress"],
                       check=True, capture_output=True)
        return _sha256_file(dest)
    except Exception:
        return None
    finally:
        if dest.exists():
            dest.unlink()


def _freeze_includes() -> list[str]:
    txt = (REPO / "scripts" / "freeze.sh").read_text()
    return re.findall(r'--include\s+"([^"]+)"', txt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bucket", nargs="?", help="S3 bucket (omit to run LOCAL checks only)")
    ap.add_argument("--prefix", default="2026", help="immutable frozen prefix")
    ap.add_argument("--work", default="draft", help="live working prefix the bytes are served from")
    ap.add_argument("--deep", action="store_true",
                    help="also DOWNLOAD every data object and hash its actual bytes vs edition.json "
                         "(definitive content check; ~17 GB transfer, no persistent local copy). "
                         "The fast path trusts x-amz-meta-sha256 + size, which the deposit re-hashes "
                         "on stream before minting.")
    a = ap.parse_args()

    zen = _load("zenodo_deposit", "scripts/zenodo-deposit.py")
    fails: list[str] = []
    ok = lambda m: print(f"  ✓ {m}")
    bad = lambda m: (fails.append(m), print(f"  ✗ {m}"))

    # ── split the authoritative Zenodo set into root-meta vs license files ────────
    zen_root = {p.name for p in zen.META_ARTIFACTS if p.parent.name == "meta"}
    zen_lic = {p.name for p in zen.META_ARTIFACTS if p.parent.name == "LICENSES"}

    inc = _freeze_includes()
    globs = {x for x in inc if x.endswith("/*")}
    freeze_root = {x for x in inc if "/" not in x}          # root-level metadata files
    freeze_pres = set(inc) & PRESENTATION

    print("LOCAL checks:")
    # 1. metadata-set parity
    if freeze_root == zen_root:
        ok(f"freeze metadata set == zenodo META_ARTIFACTS ({len(zen_root)} files)")
    else:
        bad(f"freeze ⇄ zenodo metadata drift: only-in-freeze={freeze_root - zen_root}, "
            f"only-in-zenodo={zen_root - freeze_root}")
    # LICENSES handled via the LICENSES/* glob on both sides
    if "LICENSES/*" in globs and all((REPO / "build/meta/LICENSES" / n).exists() for n in zen_lic):
        ok(f"LICENSES/* covers all {len(zen_lic)} license files on both anchors")
    else:
        bad("LICENSES parity broken (freeze missing LICENSES/* or a license file absent)")
    for need in ("corpus/*", "scale/*"):
        (ok if need in globs else bad)(f"freeze carries data glob {need}")

    # 2. no presentation assets
    if not freeze_pres:
        ok("freeze allowlist is data-only (no presentation assets)")
    else:
        bad(f"freeze would immortalize presentation assets: {sorted(freeze_pres)}")

    # 3. META_ARTIFACTS exist locally
    missing_meta = [str(p.relative_to(REPO)) for p in zen.META_ARTIFACTS if not p.exists()]
    (ok if not missing_meta else bad)(
        "all metadata artifacts present locally" if not missing_meta
        else f"missing metadata artifacts: {missing_meta}")

    # 4. data integrity is verified against the AUTHORITATIVE S3 draft/ bytes (complete,
    #    hashed, versioned — exactly what the server-side freeze copies into 2026/), in
    #    the S3 section below. A local corpus checkout is NOT required. Without a bucket,
    #    fall back to a best-effort LOCAL check (dev convenience only).
    data = zen._data_expected()
    if not a.bucket:
        miss = [k for p, k, _ in data if not p.exists()]
        mism = [k for p, k, s in data if p.exists() and s and _sha256_file(p) != s]
        (ok if not miss and not mism else bad)(
            f"(local, no bucket) all {len(data)} data files present and sha-match edition.json"
            if not miss and not mism else
            f"(local) {len(miss)} missing + {len(mism)} sha-mismatch — pass a bucket to verify against S3")

    # ── S3 checks ────────────────────────────────────────────────────────────────
    if a.bucket:
        print(f"S3 checks (s3://{a.bucket}):")
        # 5. frozen prefix empty
        r = subprocess.run(["aws", "s3", "ls", f"s3://{a.bucket}/{a.prefix}/", "--recursive"],
                           capture_output=True, text=True)
        n = len([ln for ln in r.stdout.splitlines() if ln.strip()])
        (ok if n == 0 else bad)(f"frozen prefix {a.prefix}/ is empty"
                                if n == 0 else f"{a.prefix}/ is NOT empty ({n} objects)")

        # 5b. AUTHORITATIVE data check: every one of the 30 distributed files exists in
        #     the live working prefix, its x-amz-meta-sha256 AND size equal edition.json,
        #     and it is versioned. The exact byte set the freeze copies into 2026/ and the
        #     deposit streams to Zenodo — verified without a local corpus. x-amz-meta-sha256
        #     is owner-set metadata, not S3-computed; the deposit RE-HASHES the actual bytes
        #     on stream before minting, and --deep hashes them here too.
        ed = json.loads((REPO / "build/meta/edition.json").read_text())
        size_by_key = {f["key"]: f.get("size_bytes") for f in ed["files"]}
        d_miss, d_bad, no_ver, d_size = [], [], [], []
        import tempfile
        deep_td = Path(tempfile.mkdtemp(prefix="squishy-deep-")) if a.deep else None
        for _p, key, want_sha in data:
            h = _s3_head(a.bucket, f"{a.work}/{key}")
            if h is None:
                d_miss.append(key); continue
            if want_sha and h.get("sha") != want_sha:
                d_bad.append(key)
            if size_by_key.get(key) is not None and h.get("size") != size_by_key[key]:
                d_size.append(key)
            if not h.get("version") or h.get("version") == "null":
                no_ver.append(key)
            if a.deep and want_sha:
                got = _sha256_stream_s3(a.bucket, f"{a.work}/{key}", deep_td)
                if got != want_sha:
                    d_bad.append(f"{key}(deep:{(got or 'dl-fail')[:8]})")
        if deep_td and deep_td.exists():
            deep_td.rmdir()
        if not (d_miss or d_bad or no_ver or d_size):
            how = "bytes hashed" if a.deep else "sha256 metadata + size"
            ok(f"all {len(data)} data files in {a.work}/ match edition.json ({how}) and are versioned")
        else:
            bad(f"S3 data not freeze-ready: {len(d_miss)} missing {d_miss}; "
                f"{len(d_bad)} sha-mismatch {d_bad}; {len(d_size)} size-mismatch {d_size}; "
                f"{len(no_ver)} unversioned {no_ver}")

        # 5c. ORPHAN GUARD: freeze.sh copies draft/ via the broad globs corpus/* scale/*
        #     LICENSES/* — any STRAY object under those prefixes would be swept into the
        #     permanent 2026/ but is not in edition.json/META_ARTIFACTS, so the deposit
        #     would NOT carry it (freeze ⇄ deposit drift). Assert the glob-prefix object
        #     set is EXACTLY the expected 30 data + 6 licenses, nothing extra.
        want_glob = {f["key"] for f in ed["files"]} | {f"LICENSES/{n}" for n in zen_lic}
        live_glob = {k for k in _s3_list(a.bucket, f"{a.work}/")
                     if k.startswith(("corpus/", "scale/", "LICENSES/"))}
        extra = live_glob - want_glob
        if not extra:
            ok(f"no orphan objects under {a.work}/{{corpus,scale,LICENSES}} ({len(want_glob)} expected)")
        else:
            bad(f"ORPHAN objects would be swept into {a.prefix}/: {sorted(extra)} "
                f"(remove from {a.work}/ or add to the roster before freezing)")
        # 6. live draft/ metadata is not stale (bytes == what we will deposit)
        stale = []
        meta_objs = [(p.name, f"{a.work}/{p.name}") for p in zen.META_ARTIFACTS if p.parent.name == "meta"]
        meta_objs += [(p.name, f"{a.work}/LICENSES/{p.name}") for p in zen.META_ARTIFACTS if p.parent.name == "LICENSES"]
        local_by_name = {p.name: p for p in zen.META_ARTIFACTS}
        for name, key in meta_objs:
            got = subprocess.run(["aws", "s3", "cp", f"s3://{a.bucket}/{key}", "-"],
                                 capture_output=True)
            if got.returncode != 0:
                stale.append(f"{key} (absent on draft/)")
            elif _sha256_bytes(got.stdout) != _sha256_file(local_by_name[name]):
                stale.append(f"{key} (draft/ bytes ≠ local)")
        (ok if not stale else bad)(
            f"live {a.work}/ metadata matches local ({len(meta_objs)} files)" if not stale
            else f"STALE/absent {a.work}/ metadata (deploy before freezing): {stale}")

    print()
    if fails:
        print(f"PREFLIGHT FAIL — {len(fails)} problem(s); do NOT freeze until green.")
        return 1
    print("PREFLIGHT PASS — the frozen 2026/ set and the Zenodo deposit are identical "
          "(the deposit additionally carries the deposit-only frozen-manifest.json "
          "provenance) and ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
