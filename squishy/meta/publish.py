"""Publish corpus artifacts to S3 and invalidate CloudFront.

Migrated from publish.sh (upload logic) and publish-plan.py (plan generation).

Public interfaces:
  run_publish(cfg: BuildConfig, dry_run: bool = False) -> int
  run_invalidate(cfg: BuildConfig, dist_id: str) -> int
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import sha256_file

# ── CloudFront invalidation paths ────────────────────────────────────────────

_INVALIDATION_PATHS = [
    "index.txt",
    "manifest.json",
    "manifest.safe.json",
    "manifest.safe.txt",
    "CHECKSUMS.sha256",
    "decode-expectations.json",
    "expected-ratio.json",
    "index.html",
    "listing.html",
    "AGENTS.md",
    "agent.json",
    "robots.txt",
    "llms.txt",
]

# ── helpers ───────────────────────────────────────────────────────────────────


def _human_size(n: int) -> str:
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TiB"


def _s3_head_sha256(bucket: str, s3_key: str) -> str | None:
    """Return the x-amz-meta-sha256 value stored on the S3 object, or None."""
    try:
        result = subprocess.run(
            [
                "aws", "s3api", "head-object",
                "--bucket", bucket,
                "--key", s3_key,
                "--query", "Metadata.sha256",
                "--output", "text",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        val = result.stdout.strip()
        if result.returncode == 0 and val and val != "None":
            return val
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _s3_upload(
    local_path: Path,
    bucket: str,
    s3_key: str,
    content_type: str,
    cache_control: str,
    sha256: str,
) -> bool:
    """Upload a file to S3 with metadata. Returns True on success."""
    try:
        result = subprocess.run(
            [
                "aws", "s3", "cp",
                str(local_path),
                f"s3://{bucket}/{s3_key}",
                "--acl", "public-read",
                "--storage-class", "ONEZONE_IA",
                "--metadata", f"sha256={sha256}",
                "--content-type", content_type,
                "--cache-control", cache_control,
                "--checksum-algorithm", "SHA256",
                "--no-progress",
            ],
            capture_output=True,
            timeout=600,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _purge_local(local_path: Path, keep_local: bool, dry_run: bool) -> int:
    """Delete the local file after upload and prune empty parent dirs.

    Returns the number of bytes freed (0 if not deleted).
    """
    if keep_local or dry_run:
        return 0
    sz = local_path.stat().st_size if local_path.exists() else 0
    try:
        local_path.unlink(missing_ok=True)
        parent = local_path.parent
        while True:
            try:
                parent.rmdir()
                parent = parent.parent
            except OSError:
                break
    except OSError:
        pass
    return sz


# ── run_publish ───────────────────────────────────────────────────────────────


def run_publish(cfg: BuildConfig, dry_run: bool = False) -> int:
    """Read publish.tsv and upload each entry to S3."""
    import os

    tsv_path = cfg.meta_dir / "publish.tsv"
    if not tsv_path.exists():
        print(f"publish: {tsv_path} not found — run 'build manifest' first", file=sys.stderr)
        return 1

    keep_local = os.environ.get("CORPUS_KEEP_LOCAL", "0") == "1"
    bucket = cfg.bucket

    uploaded = 0
    skipped = 0
    failed = 0
    total = 0
    freed_bytes = 0

    with tsv_path.open() as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            local_path_str, s3_key, content_type, cache_control = parts[:4]
            local_path = Path(local_path_str)
            total += 1

            if not local_path.exists():
                print(f"MISSING: {local_path}", file=sys.stderr)
                failed += 1
                continue

            local_sha = sha256_file(local_path)
            remote_sha = _s3_head_sha256(bucket, s3_key)

            if remote_sha == local_sha:
                skipped += 1
                if dry_run:
                    print(f"skip  {s3_key}")
                freed_bytes += _purge_local(local_path, keep_local, dry_run)
                continue

            if dry_run:
                status = "PLAN UPLOAD (new)" if remote_sha is None else f"PLAN UPLOAD (drift: remote={remote_sha[:12]})"
                print(f"{status}:  {s3_key}")
                uploaded += 1
                continue

            if not _s3_upload(local_path, bucket, s3_key, content_type, cache_control, local_sha):
                print(f"UPLOAD FAILED: {s3_key}", file=sys.stderr)
                failed += 1
                continue

            # Verify the upload landed correctly
            new_sha = _s3_head_sha256(bucket, s3_key)
            if new_sha != local_sha:
                print(
                    f"VERIFY FAILED: {s3_key}  expected={local_sha}  got={new_sha}",
                    file=sys.stderr,
                )
                failed += 1
                continue

            freed_bytes += _purge_local(local_path, keep_local, dry_run)
            print(f"ok    {s3_key}")
            uploaded += 1

    print("---", file=sys.stderr)
    print(
        f"total={total} uploaded={uploaded} skipped={skipped} failed={failed} "
        f"dry_run={dry_run} freed={_human_size(freed_bytes)}",
        file=sys.stderr,
    )
    return 0 if failed == 0 else 1


# ── run_invalidate ────────────────────────────────────────────────────────────


def run_invalidate(cfg: BuildConfig, dist_id: str) -> int:
    """Send a CloudFront invalidation for the standard set of index files."""
    paths = [f"/{cfg.prefix}/{name}" for name in _INVALIDATION_PATHS]

    import time
    caller_ref = f"squishy-{int(time.time())}"

    invalidation_batch = {
        "Paths": {"Quantity": len(paths), "Items": paths},
        "CallerReference": caller_ref,
    }

    try:
        result = subprocess.run(
            [
                "aws", "cloudfront", "create-invalidation",
                "--distribution-id", dist_id,
                "--invalidation-batch", json.dumps(invalidation_batch),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print(f"invalidation submitted: {dist_id}  {len(paths)} paths", file=sys.stderr)
            return 0
        print(f"invalidation failed: {result.stderr.strip()}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("invalidate: 'aws' CLI not found on PATH", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("invalidate: aws cloudfront command timed out", file=sys.stderr)
        return 1
