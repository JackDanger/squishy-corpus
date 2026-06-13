#!/usr/bin/env python3
"""Mint the Squishy-2026 Zenodo DOI. OWNER-RUN at freeze.

Reads ZENODO_TOKEN from the environment (NEVER hard-code it; never commit it).
Creates a deposition, uploads the citable artifacts (meta files + ALL distributed
data files), sets metadata (including the scored-set fingerprint), and RESERVES a
DOI.  It does NOT auto-publish — review on zenodo.org, then click Publish (or pass
--publish) to mint the final DOI.

  ZENODO_TOKEN=xxxx uv run python scripts/zenodo-deposit.py [--sandbox] [--publish]

ARTIFACTS policy:
  META FILES  — hard-fail if any are missing (small, always present at freeze).
  DATA FILES  — derived dynamically from build/meta/edition.json (key → build/raw/<key>).
                Large files (multi-GB) are only present on the owner's machine at
                freeze time.  Missing data files print a clear warning + count and
                allow the upload to proceed (so the script can run in --sandbox /
                dry-run mode without the full corpus checked out).  Change the guard
                below if you want to hard-fail on missing data too.
"""
from __future__ import annotations
import argparse, json, os, re, sys, urllib.error, urllib.request
from pathlib import Path


def _redact(s: str) -> str:
    """Strip any access_token=… from a string so the secret never reaches a log/stderr."""
    return re.sub(r"access_token=[^&\s'\"]+", "access_token=REDACTED", s or "")


def _http_fail(action: str, e: Exception) -> SystemExit:
    """Turn a urllib error into a clear, token-free message + a SystemExit(1).

    HTTPError.url / .filename carry the request URL (which contains the token), and
    the server body can echo it too — both are redacted before printing.
    """
    detail = ""
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        msg = f"HTTP {e.code} {e.reason}"
        if detail:
            msg += f"\n  server said: {_redact(detail)[:500]}"
    else:
        msg = _redact(str(e))
    print(f"ERROR: {action} failed: {msg}", file=sys.stderr)
    return SystemExit(1)

REPO = Path(__file__).resolve().parent.parent

# ── META FILES ───────────────────────────────────────────────────────────────
# These are small, committed or always-generated before freeze.  Hard-fail if any
# are absent so the DOI record is never missing its manifest/license/checksum.
META_ARTIFACTS = [
    REPO / "build" / "meta" / "edition.json",
    REPO / "build" / "meta" / "schema.json",
    REPO / "build" / "meta" / "baseline.json",
    REPO / "build" / "meta" / "CHECKSUMS.sha256",
    REPO / "build" / "meta" / "LICENSE-MANIFEST.csv",
    REPO / "build" / "meta" / "NOTICE",
    REPO / "build" / "meta" / "squishy-scores.json",
]

# ── DATA FILES ───────────────────────────────────────────────────────────────
# Derived at runtime from edition.json: every distributed corpus+scale file maps to
# build/raw/<key> on disk (same layout as squishy.py's raw_path()).  Missing files
# warn + continue (see ARTIFACTS policy in module docstring).

def _data_artifacts() -> list[Path]:
    """Return a list of local paths for every file in edition.json."""
    ed_path = REPO / "build" / "meta" / "edition.json"
    if not ed_path.exists():
        return []  # META check will catch this first
    ed = json.loads(ed_path.read_text())
    raw_root = REPO / "build" / "raw"
    return [raw_root / f["key"] for f in ed.get("files", []) if f.get("key")]


def _scored_set_fingerprint() -> str | None:
    """Read the stable edition fingerprint from build/meta/baseline.json."""
    p = REPO / "build" / "meta" / "baseline.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("scored_set_fingerprint")
    except Exception:
        return None


META = {
    "metadata": {
        "title": "Squishy-2026: a compression benchmark corpus",
        "upload_type": "dataset",
        "description": ("The Squishy-2026 named core: a small, curated set of real, "
                        "redistributable modern files that compress differently, plus "
                        "the Squishy Score (geometric mean of per-file compression "
                        "ratio). Successor to the Silesia corpus. See NOTICE and "
                        "LICENSE-MANIFEST.csv for per-file provenance and licenses."),
        "creators": [{"name": "Danger, Jack"}],
        "keywords": ["compression", "benchmark", "corpus", "lossless"],
        "version": "Squishy-2026",
        "access_right": "open",
    }
}


def api(base, path, token, method="GET", data=None):
    req = urllib.request.Request(f"{base}/api/{path}?access_token={token}",
                                 method=method,
                                 headers={"Content-Type": "application/json"})
    body = json.dumps(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(req, data=body, timeout=60) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        raise _http_fail(f"{method} api/{path}", e) from None


def upload_file(bucket: str, token: str, path: Path) -> None:
    """Stream a file to the Zenodo bucket (no size limit, no OOM).

    Uses an open file handle as the request body so even multi-GB files are
    never loaded into RAM.  No upload timeout is set so that very large files
    (e.g. a 4 GB CSV) do not time out mid-transfer.
    """
    url = f"{bucket}/{path.name}?access_token={token}"
    size = path.stat().st_size
    try:
        with path.open("rb") as fh:
            req = urllib.request.Request(url, method="PUT", data=fh,
                                         headers={"Content-Length": str(size)})
            with urllib.request.urlopen(req) as r:
                pass  # response body not needed for PUT
    except urllib.error.URLError as e:
        raise _http_fail(f"upload {path.name}", e) from None
    print(f"  uploaded {path.name} ({size:,} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", action="store_true", help="use sandbox.zenodo.org")
    ap.add_argument("--publish", action="store_true", help="publish (mint final DOI) — irreversible")
    args = ap.parse_args()
    token = os.environ.get("ZENODO_TOKEN")
    if not token:
        print("ERROR: set ZENODO_TOKEN in the environment (do not commit it).", file=sys.stderr)
        return 1
    base = "https://sandbox.zenodo.org" if args.sandbox else "https://zenodo.org"

    # ── guard: hard-fail on missing META files ────────────────────────────────
    missing_meta = [str(a) for a in META_ARTIFACTS if not a.exists()]
    if missing_meta:
        print(f"ERROR: missing required meta artifacts (fix before depositing):\n"
              f"  {missing_meta}", file=sys.stderr)
        return 1

    # ── guard: warn-but-continue on missing DATA files ────────────────────────
    data_artifacts = _data_artifacts()
    missing_data = [str(a) for a in data_artifacts if not a.exists()]
    if missing_data:
        print(f"WARNING: {len(missing_data)}/{len(data_artifacts)} data files not found locally "
              f"(normal in sandbox/dry-run; they must be present for the final public deposit):")
        for p in missing_data:
            print(f"  missing: {p}")
    present_data = [a for a in data_artifacts if a.exists()]

    # ── stamp the edition fingerprint in the metadata ─────────────────────────
    # The scored_set_fingerprint is a stable, timestamp-independent hash of the
    # edition's scored roster (names/shas/kinds/categories).  Including it in the
    # deposit description and version notes lets the DOI name the exact edition
    # by a content-derived identifier — independent of git tags or wall time.
    fingerprint = _scored_set_fingerprint()
    if fingerprint:
        META["metadata"]["description"] += (
            f" Edition fingerprint (scored-set sha256, timestamp-independent): {fingerprint}.")
        META["metadata"]["notes"] = f"scored_set_fingerprint: {fingerprint}"
        print(f"stamped edition fingerprint: {fingerprint[:16]}…")
    else:
        print("WARNING: baseline.json not found — edition fingerprint not stamped.", file=sys.stderr)

    # ── pin the source revision ───────────────────────────────────────────────
    # The seeded generators + PRNG that reproduce the large/pathological files
    # live only in the repo, not in the uploaded artifacts.
    import subprocess
    def _git(*a):
        try:
            return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout.strip()
        except Exception:
            return ""
    commit = _git("rev-parse", "HEAD")
    tag = _git("describe", "--tags", "--always")
    if commit:
        repo_url = "https://github.com/JackDanger/squishy-corpus"
        META["metadata"].setdefault("related_identifiers", []).append(
            {"identifier": f"{repo_url}/tree/{commit}", "relation": "isSupplementedBy",
             "resource_type": "software", "scheme": "url"})
        META["metadata"]["description"] += (
            f" Source revision (corpus generators + PRNG that reproduce the "
            f"regeneratable tier bit-for-bit): {repo_url} @ {tag or commit}.")
        print(f"pinned source revision: {tag or commit}")

    dep = api(base, "deposit/depositions", token, "POST", {})
    dep_id = dep["id"]
    bucket = dep["links"]["bucket"]
    print(f"deposition {dep_id} created; reserved DOI: {dep['metadata'].get('prereserve_doi',{}).get('doi','(on publish)')}")
    api(base, f"deposit/depositions/{dep_id}", token, "PUT", META)

    # Upload meta files first (small, always present), then data files (streamed).
    all_to_upload = META_ARTIFACTS + present_data
    print(f"uploading {len(all_to_upload)} files "
          f"({len(META_ARTIFACTS)} meta + {len(present_data)} data)…")
    for a in all_to_upload:
        upload_file(bucket, token, a)

    if args.publish:
        pub = api(base, f"deposit/depositions/{dep_id}/actions/publish", token, "POST")
        print(f"PUBLISHED. DOI: {pub.get('doi')}")
    else:
        print(f"\nDraft ready. Review at {base}/deposit/{dep_id} then publish there "
              f"(or re-run with --publish) to mint the final DOI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
