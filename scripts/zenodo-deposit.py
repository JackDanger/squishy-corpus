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
    arcname = path.name
    url = f"{bucket}/{arcname}?access_token={token}"
    size = path.stat().st_size
    try:
        with path.open("rb") as fh:
            req = urllib.request.Request(url, method="PUT", data=fh,
                                         headers={"Content-Length": str(size)})
            with urllib.request.urlopen(req) as r:
                pass  # response body not needed for PUT
    except urllib.error.URLError as e:
        raise _http_fail(f"upload {arcname}", e) from None
    print(f"  uploaded {arcname} ({size:,} bytes)")


def _s3_head(bucket: str, key: str) -> dict | None:
    """HEAD an S3 object → {size, sha (x-amz-meta-sha256), version}. None if absent."""
    import subprocess
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


def _fetch_s3(bucket: str, key: str, dest: Path) -> None:
    """Download s3://bucket/key (current version) → dest. One file; bounded footprint."""
    import subprocess
    subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/{key}", str(dest), "--no-progress"],
                   check=True)


def _fetch_s3_version(bucket: str, key: str, version_id: str, dest: Path) -> None:
    """Download an EXACT object version → dest (pins the byte set even if the key is
    later overwritten)."""
    import subprocess
    subprocess.run(["aws", "s3api", "get-object", "--bucket", bucket, "--key", key,
                    "--version-id", version_id, str(dest)],
                   check=True, capture_output=True)


def _validate_frozen_manifest(mpath: Path, bucket: str, work: str, ed: dict,
                              fingerprint: str | None) -> tuple[dict, list[str]]:
    """Validate a frozen-manifest.json against THIS edition; return (version_by_key, errors).
    Ensures the manifest names this bucket/prefix and this edition, and pins every
    distributed file with a size+sha that matches edition.json."""
    errs: list[str] = []
    m = json.loads(mpath.read_text())
    if m.get("bucket") != bucket:
        errs.append(f"manifest bucket {m.get('bucket')!r} != {bucket!r}")
    if m.get("prefix") != work:
        errs.append(f"manifest prefix {m.get('prefix')!r} != --work {work!r} "
                    f"(deposit must source the same prefix the manifest pins)")
    if m.get("edition") != ed.get("edition"):
        errs.append(f"manifest edition {m.get('edition')!r} != {ed.get('edition')!r}")
    if fingerprint and m.get("scored_set_fingerprint") != fingerprint:
        errs.append("manifest scored_set_fingerprint != baseline")
    objs = m.get("objects", {})
    ver: dict[str, str] = {}
    for f in ed["files"]:
        o = objs.get(f["key"])
        if not o:
            errs.append(f"manifest missing {f['key']}"); continue
        if f.get("sha256") and o.get("sha256") != f["sha256"]:
            errs.append(f"{f['key']}: manifest sha != edition")
        if f.get("size_bytes") is not None and o.get("size") != f["size_bytes"]:
            errs.append(f"{f['key']}: manifest size != edition")
        if not o.get("version_id"):
            errs.append(f"{f['key']}: manifest has no version_id")
        else:
            ver[f["key"]] = o["version_id"]
    return ver, errs


def _free_bytes(path: Path) -> int:
    import shutil as _sh
    return _sh.disk_usage(path).free


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", action="store_true", help="use sandbox.zenodo.org")
    ap.add_argument("--publish", action="store_true", help="publish (mint final DOI) — irreversible")
    ap.add_argument("--allow-partial", action="store_true",
                    help="allow missing/mismatched data files (dry-run only; NEVER for the real mint)")
    ap.add_argument("--bucket",
                    help="S3 bucket to source the data bytes from (e.g. squishy-corpus). When set, "
                         "data files are streamed from s3://<bucket>/<work>/<key> — the same "
                         "authoritative, versioned bytes the freeze copies into 2026/ — instead of a "
                         "local checkout. Meta files are always taken from build/meta.")
    ap.add_argument("--work", default="draft", help="S3 working prefix the live bytes are served from")
    ap.add_argument("--frozen-manifest", type=Path,
                    help="build/meta/frozen-manifest.json (from capture-frozen-versions.py): deposited "
                         "as provenance AND used to fetch each data file by its exact VersionId. "
                         "Required for --publish.")
    ap.add_argument("--tmpdir", help="directory for the one-file-at-a-time S3 download buffer "
                                     "(default: system temp; needs ≥ 2× the largest file free)")
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
    # edition publishes. A missing file or a byte that does not match edition.json
    # would silently mint an incomplete/wrong record. Hard-fail unless explicitly
    # running a dry-run (--sandbox/--allow-partial).
    #
    # Source of truth: with --bucket, the AUTHORITATIVE versioned bytes in
    # s3://<bucket>/<work>/ (the same set the freeze copies into 2026/), verified by
    # x-amz-meta-sha256 — no local corpus needed. Otherwise, a local checkout.
    data_expected = _data_expected()
    missing_data, mismatched, ready = [], [], []   # ready: list of (key, want_sha)
    if args.bucket:
        print(f"verifying {len(data_expected)} data files in "
              f"s3://{args.bucket}/{args.work}/ against edition.json sha256…")
        for _path, key, want_sha in data_expected:
            h = _s3_head(args.bucket, f"{args.work}/{key}")
            if h is None:
                missing_data.append(key)
            elif want_sha and h.get("sha") != want_sha:
                mismatched.append(key)
            else:
                ready.append((key, want_sha))
    else:
        print(f"verifying {len(data_expected)} local data files against edition.json sha256…")
        for path, key, want_sha in data_expected:
            if not path.exists():
                missing_data.append(key)
            elif want_sha and _sha256(path) != want_sha:
                mismatched.append(key)
            else:
                ready.append((key, want_sha))
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
    print(f"  ✓ {len(ready)}/{len(data_expected)} data files verified "
          f"({'S3 ' + args.work + '/' if args.bucket else 'local'})")

    # The minted DOI must pin the exact frozen object versions. --publish REQUIRES a valid
    # post-freeze VersionId manifest; when provided, data is fetched BY VersionId so the
    # deposit anchors precisely the frozen bytes (immune to a later key overwrite).
    fingerprint = _scored_set_fingerprint()
    version_by_key: dict[str, str] = {}
    if args.frozen_manifest and args.frozen_manifest.exists():
        ed_full = json.loads((REPO / "build/meta/edition.json").read_text())
        if not args.bucket:
            print("ERROR: --frozen-manifest requires --bucket (it pins S3 VersionIds to fetch by).",
                  file=sys.stderr)
            return 1
        version_by_key, merrs = _validate_frozen_manifest(
            args.frozen_manifest, args.bucket, args.work, ed_full, fingerprint)
        if merrs:
            print("ERROR: frozen-manifest validation failed:\n  " + "\n  ".join(merrs), file=sys.stderr)
            return 1
        print(f"  ✓ frozen-manifest validated: {len(version_by_key)} objects pinned to VersionIds")
    elif args.publish:
        print("ERROR: --publish requires --frozen-manifest (run capture-frozen-versions.py after the "
              "freeze) so the DOI pins the exact frozen VersionIds and re-validates the bytes.",
              file=sys.stderr)
        return 1

    # ── stamp the edition fingerprint in the metadata ─────────────────────────
    # The scored_set_fingerprint is a stable, timestamp-independent hash of the
    # edition's scored roster (names/shas/kinds/categories).  Including it in the
    # deposit description and version notes lets the DOI name the exact edition
    # by a content-derived identifier — independent of git tags or wall time.
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
    zbucket = dep["links"]["bucket"]          # Zenodo's upload bucket (NOT the S3 source)
    print(f"deposition {dep_id} created; reserved DOI: {dep['metadata'].get('prereserve_doi',{}).get('doi','(on publish)')}")
    api(base, f"deposit/depositions/{dep_id}", token, "PUT", META)

    # Meta files (always local), then the optional VersionId provenance manifest.
    to_upload_meta = list(META_ARTIFACTS)
    if args.frozen_manifest and args.frozen_manifest.exists():
        to_upload_meta.append(args.frozen_manifest)
    print(f"uploading {len(to_upload_meta)} meta files…")
    for a in to_upload_meta:
        upload_file(zbucket, token, a)

    # Data files. With --bucket: stream each from S3 to a temp file — BY VersionId when a
    # manifest pins one — RE-HASH the actual transferred bytes against edition.json
    # (defence in depth over the HEAD metadata), upload, then delete: one file's footprint
    # at a time. Otherwise upload local bytes. On any failure the deposition is a DRAFT
    # (never auto-published) and is safe to delete on Zenodo.
    print(f"uploading {len(ready)} data files ({'streamed from S3' if args.bucket else 'local'})…")
    if args.bucket:
        import tempfile
        tmp_root = Path(args.tmpdir) if args.tmpdir else None
        biggest = max((f["size_bytes"] or 0 for f in json.loads(
            (REPO / "build/meta/edition.json").read_text())["files"]), default=0)
        with tempfile.TemporaryDirectory(prefix="squishy-deposit-", dir=tmp_root) as td:
            tdp = Path(td)
            if _free_bytes(tdp) < biggest * 2:
                print(f"ERROR: only {_free_bytes(tdp)//(1<<30)} GiB free in {tdp}; need ≥ "
                      f"{biggest*2//(1<<30)} GiB headroom for the largest file. Use --tmpdir.",
                      file=sys.stderr)
                return 1
            for key, want_sha in ready:
                dest = tdp / Path(key).name
                try:
                    if version_by_key.get(key):
                        _fetch_s3_version(args.bucket, f"{args.work}/{key}", version_by_key[key], dest)
                    else:
                        _fetch_s3(args.bucket, f"{args.work}/{key}", dest)
                except Exception as e:
                    print(f"ERROR: fetch {key} failed: {e}. Deposition {dep_id} is an unpublished "
                          f"DRAFT — delete it at {base}/deposit/{dep_id} and retry.", file=sys.stderr)
                    return 1
                got = _sha256(dest)
                if want_sha and got != want_sha:
                    print(f"ERROR: {key}: transferred bytes sha {got[:12]} != edition "
                          f"{want_sha[:12]}. Deposition {dep_id} is an unpublished DRAFT — delete it "
                          f"and retry.", file=sys.stderr)
                    return 1
                upload_file(zbucket, token, dest)
                dest.unlink()
    else:
        raw_root = REPO / "build" / "raw"
        for key, _want in ready:
            upload_file(zbucket, token, raw_root / key)

    if args.publish:
        pub = api(base, f"deposit/depositions/{dep_id}/actions/publish", token, "POST")
        print(f"PUBLISHED. DOI: {pub.get('doi')}")
    else:
        print(f"\nDraft ready. Review at {base}/deposit/{dep_id} then publish there "
              f"(or re-run with --publish) to mint the final DOI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
