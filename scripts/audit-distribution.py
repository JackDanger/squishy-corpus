#!/usr/bin/env python3
"""Distribution audit for the Squishy corpus in S3.

For every corpus file, the published tarball, and the checksums file:
  - confirm the object exists in the bucket with size + x-amz-meta-sha256 matching
    the local bytes (integrity), and
  - confirm it is publicly fetchable over HTTPS (200 + sane content).

Exits non-zero on any mismatch. Run before any freeze (verification pass 1).

Usage: uv run python scripts/audit-distribution.py [--prefix draft]
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, subprocess, sys, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUCKET = "squishy-corpus"
HOST = f"https://{BUCKET}.s3.us-west-2.amazonaws.com"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def head(key: str) -> dict | None:
    r = subprocess.run(["aws", "s3api", "head-object", "--bucket", BUCKET, "--key", key],
                       capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def https_ok(key: str) -> tuple[bool, int]:
    try:
        req = urllib.request.Request(f"{HOST}/{key}", method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read(1024)  # touch the body
            return resp.status == 200, resp.status
    except Exception as e:
        return False, getattr(e, "code", 0)


def corpus_entries():
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    for files in sq.CORE.values():
        for _d, st, n in files:
            yield n, REPO / "build" / "raw" / st / n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="draft")
    args = ap.parse_args()
    px = args.prefix
    bad = 0
    checks = [(f"{px}/corpus/{n}", p) for n, p in corpus_entries()]
    checks.append((f"{px}/squishy-2026.tar", REPO / "build" / "meta" / "squishy-2026.tar"))
    checks.append((f"{px}/CHECKSUMS.sha256", REPO / "build" / "meta" / "CHECKSUMS.sha256"))
    for key, local in checks:
        if not local.exists():
            print(f"  [skip] {key}: no local copy to compare"); continue
        h = head(key)
        lsize, lsha = local.stat().st_size, sha256(local)
        ok_size = h is not None and h["ContentLength"] == lsize
        ok_sha = h is not None and h.get("Metadata", {}).get("sha256") == lsha
        pub, code = https_ok(key)
        status = "ok" if (ok_size and ok_sha and pub) else "FAIL"
        if status == "FAIL":
            bad += 1
        print(f"  [{status}] {key}  size={'✓' if ok_size else '✗'} "
              f"sha256={'✓' if ok_sha else '✗'} public={'✓' if pub else f'✗({code})'}")
    print(f"\n{len(checks)} objects audited; {bad} failures.")
    print("\nDownloader verify one-liner:")
    print(f"  curl -s {HOST}/{px}/CHECKSUMS.sha256 | while read h f; do "
          f'curl -s {HOST}/{px}/corpus/$(basename $f) | shasum -a256 | grep -q $h '
          f'&& echo "ok $f" || echo "MISMATCH $f"; done')
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
