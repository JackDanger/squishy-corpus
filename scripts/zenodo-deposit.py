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
import argparse, hashlib, json, os, re, sys, urllib.error, urllib.request
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
# This set MUST equal the metadata that freeze.sh copies into the immutable 2026/
# prefix (scripts/preflight-freeze.py asserts that equality) — otherwise the DOI
# record and the frozen S3 prefix would disagree.
_META = REPO / "build" / "meta"
META_ARTIFACTS = [
    _META / "edition.json",
    _META / "schema.json",
    _META / "baseline.json",
    _META / "CHECKSUMS.sha256",
    _META / "LICENSE-MANIFEST.csv",
    _META / "NOTICE",
    _META / "squishy-board-complete.json",   # the whole-corpus board (every codec)
    _META / "squishy-score-complete.json",   # the round-trip-verified reference score
    _META / "file-properties.json",          # intrinsic byte axes (named core)
    _META / "scale-properties.json",          # intrinsic byte axes (scale tier)
    _META / "size-convergence.json",          # ratio-vs-size convergence evidence
    _META / "verification-pass4.json",        # independent stdlib cross-check of the board
] + sorted((_META / "LICENSES").glob("*"))   # full license texts (one per license)

# ── DATA FILES ───────────────────────────────────────────────────────────────
# Derived at runtime from edition.json: every distributed corpus+scale file maps to
# build/raw/<key> on disk (same layout as squishy.py's raw_path()).  Missing files
# warn + continue (see ARTIFACTS policy in module docstring).

def _data_expected() -> list[tuple[Path, str, str]]:
    """Return (local_path, key, sha256) for every distributed file in edition.json.
    The sha256 is the authoritative published hash the local bytes must match."""
    ed_path = REPO / "build" / "meta" / "edition.json"
    if not ed_path.exists():
        return []  # META check will catch this first
    ed = json.loads(ed_path.read_text())
    raw_root = REPO / "build" / "raw"
    return [(raw_root / f["key"], f["key"], f.get("sha256", ""))
            for f in ed.get("files", []) if f.get("key")]


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


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
        "title": "Squishy-2026: a citable compression benchmark corpus and score",
        "upload_type": "dataset",
        "description": ("Squishy-2026 — a curated, citable compression benchmark corpus "
                        "(successor to the Silesia corpus) and its headline metric, the "
                        "Squishy Score: the geometric mean of per-file compression ratio "
                        "over the whole corpus (one vote per file), reported with a "
                        "byte-weighted corpus bits-per-byte companion. 30 real, "
                        "redistributable files — 26 scored cells spanning kinds and sizes "
                        "(tens of MB to multi-GB) plus 4 non-scored diagnostics. See "
                        "NOTICE and LICENSE-MANIFEST.csv for per-file provenance and "
                        "licenses; CHECKSUMS.sha256 verifies every file."),
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
    ap.add_argument("--allow-partial", action="store_true",
                    help="allow missing/mismatched data files (dry-run only; NEVER for the real mint)")
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

    # ── guard: every DATA file must be present AND match its published sha256 ──
    # The DOI is permanent: it must anchor the EXACT, COMPLETE set of bytes the
    # edition publishes. A missing file (e.g. the scale tier not checked out) or a
    # byte that does not match edition.json would silently mint an incomplete/wrong
    # record. Hard-fail unless explicitly running a dry-run (--sandbox/--allow-partial).
    data_expected = _data_expected()
    present_data, missing_data, mismatched = [], [], []
    print(f"verifying {len(data_expected)} data files against edition.json sha256…")
    for path, key, want_sha in data_expected:
        if not path.exists():
            missing_data.append(key); continue
        if want_sha and _sha256(path) != want_sha:
            mismatched.append(key); continue
        present_data.append(path)
    if missing_data:
        print(f"  {len(missing_data)} missing: {missing_data}")
    if mismatched:
        print(f"  {len(mismatched)} sha MISMATCH: {mismatched}")
    incomplete = bool(missing_data or mismatched)
    lenient = args.sandbox or args.allow_partial
    if incomplete and args.publish:
        # --publish mints the permanent DOI; it can NEVER anchor an incomplete set,
        # regardless of --allow-partial (which only loosens DRAFT creation).
        print(f"ERROR: refusing to PUBLISH — {len(missing_data)} missing + {len(mismatched)} "
              f"mismatched of {len(data_expected)} data files. The minted DOI must carry the "
              f"complete, byte-correct edition.", file=sys.stderr)
        return 1
    if incomplete and not lenient:
        print(f"ERROR: {len(missing_data)} missing + {len(mismatched)} mismatched data files. "
              f"The deposit must carry all {len(data_expected)} files, byte-correct. "
              f"Check out the full corpus (incl. the scale tier) and retry, or pass "
              f"--sandbox/--allow-partial for a dry-run.", file=sys.stderr)
        return 1
    if incomplete and lenient:
        print(f"WARNING: proceeding WITHOUT {len(missing_data)+len(mismatched)} data files "
              f"(dry-run mode) — this deposit must NOT be published.")
    print(f"  ✓ {len(present_data)}/{len(data_expected)} data files present and sha-verified")

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
