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
import argparse, hashlib, importlib.util, re, subprocess, sys
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


def _freeze_includes() -> list[str]:
    txt = (REPO / "scripts" / "freeze.sh").read_text()
    return re.findall(r'--include\s+"([^"]+)"', txt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bucket", nargs="?", help="S3 bucket (omit to run LOCAL checks only)")
    ap.add_argument("--prefix", default="2026", help="immutable frozen prefix")
    ap.add_argument("--work", default="draft", help="live working prefix the bytes are served from")
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

    # 4. all 30 data files present + sha-correct
    data = zen._data_expected()
    miss = [k for p, k, _ in data if not p.exists()]
    mism = [k for p, k, s in data if p.exists() and s and _sha256_file(p) != s]
    if not miss and not mism:
        ok(f"all {len(data)} data files present and sha-match edition.json")
    else:
        bad(f"data not freeze-ready: {len(miss)} missing {miss}; {len(mism)} sha-mismatch {mism}")

    # ── S3 checks ────────────────────────────────────────────────────────────────
    if a.bucket:
        print(f"S3 checks (s3://{a.bucket}):")
        # 5. frozen prefix empty
        r = subprocess.run(["aws", "s3", "ls", f"s3://{a.bucket}/{a.prefix}/", "--recursive"],
                           capture_output=True, text=True)
        n = len([ln for ln in r.stdout.splitlines() if ln.strip()])
        (ok if n == 0 else bad)(f"frozen prefix {a.prefix}/ is empty"
                                if n == 0 else f"{a.prefix}/ is NOT empty ({n} objects)")
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
    print("PREFLIGHT PASS — frozen set and Zenodo deposit are identical and ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
