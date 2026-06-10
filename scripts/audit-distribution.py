#!/usr/bin/env python3
"""Distribution audit for the published Squishy corpus.

Audits the corpus where downloaders actually fetch it: the public base URL
(CloudFront in front of S3 — direct S3 access is intentionally blocked). For
every file in build/meta/edition.json (the pinned manifest), HEAD its published
url and confirm:
  - it is publicly fetchable (200),
  - Content-Length matches the manifest size_bytes,
  - the x-amz-meta-sha256 header (set at publish, passed through the CDN)
    matches the manifest sha256.
Plus CHECKSUMS.sha256 itself: fetched and byte-compared to the local copy.

Exits non-zero on any mismatch. Run before any freeze (verification pass 1).

Usage: uv run python scripts/audit-distribution.py [--base https://…]
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def head(url: str) -> tuple[int, int | None, str | None]:
    """(status, content_length, sha256_meta) for a published object."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return (r.status, int(r.headers.get("Content-Length", -1)),
                    r.headers.get("x-amz-meta-sha256"))
    except Exception as e:
        return getattr(e, "code", 0), None, None


def main() -> int:
    ed = json.loads((REPO / "build/meta/edition.json").read_text())
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("SQUISHY_BASE") or ed.get("base_url"),
                    help="public base URL (default: edition.json base_url)")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    bad = 0

    for f in ed["files"]:
        url = f.get("url") or f"{base}/{f['key']}"
        status, clen, meta_sha = head(url)
        ok_pub = status == 200
        ok_size = clen == f["size_bytes"]
        ok_sha = meta_sha == f["sha256"]
        ok = ok_pub and ok_size and ok_sha
        bad += 0 if ok else 1
        print(f"  [{'ok' if ok else 'FAIL'}] {f['key']}  public={'✓' if ok_pub else f'✗({status})'} "
              f"size={'✓' if ok_size else '✗'} sha256={'✓' if ok_sha else '✗'}")

    # CHECKSUMS.sha256 — the fail-closed root of trust for squishy-calculate
    url = f"{base}/CHECKSUMS.sha256"
    local = REPO / "build/meta/CHECKSUMS.sha256"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            remote = r.read()
        if local.exists():
            ok = remote == local.read_bytes()
            note = "matches local copy" if ok else "DIFFERS from local build/meta copy"
        else:
            ok = bool(remote.strip())
            note = "no local copy; fetched + non-empty"
        bad += 0 if ok else 1
        print(f"  [{'ok' if ok else 'FAIL'}] CHECKSUMS.sha256  {note}")
    except Exception as e:
        bad += 1
        print(f"  [FAIL] CHECKSUMS.sha256  unfetchable: {e}")

    print(f"\n{len(ed['files']) + 1} objects audited at {base}; {bad} failures.")
    print("\nDownloader verify one-liner:")
    print(f"  curl -s {base}/CHECKSUMS.sha256 | while read h f; do "
          f"curl -s {base}/$f | shasum -a256 | grep -q $h "
          f'&& echo "ok $f" || echo "MISMATCH $f"; done')
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
