#!/usr/bin/env bash
# publish.sh — idempotent S3 uploader for the corpus.
#
# Reads publish.tsv on stdin, one record per line:
#   <local_path>\t<s3_key>\t<content_type>\t<cache_control>
#
# For each record:
#   1) compute local sha256
#   2) HEAD origin (s3, NOT cloudfront — CDN strips user metadata)
#   3) if remote x-amz-meta-sha256 matches → skip (already safe in S3)
#   4) else PUT with native S3 SHA256 checksum + metadata + content-type + cache-control
#   5) verify by HEAD again
#   6) delete the local file (and parent dir if now empty) to keep disk lean.
#      Set CORPUS_KEEP_LOCAL=1 in the environment to disable local cleanup.
#
# Usage:
#   aws-vault exec personal -- bash publish.sh <bucket> [--dry-run] < publish.tsv

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <bucket> [--dry-run]" >&2
  exit 1
fi
bucket="$1"; shift
dry_run="false"
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run="true"; shift
fi

sha_cmd="shasum -a 256"
command -v sha256sum >/dev/null 2>&1 && sha_cmd="sha256sum"

# Local cleanup: after a successful upload (or skip-because-already-uploaded),
# delete the local file and prune the parent directory if empty. Lets the
# build run on tight disk. Disable with CORPUS_KEEP_LOCAL=1.
purge_local() {
  local p="$1"
  [[ "${CORPUS_KEEP_LOCAL:-0}" == "1" ]] && return 0
  [[ "$dry_run" == "true" ]] && return 0
  rm -f "$p"
  # walk up and rmdir empty parents, but don't escape build/
  local d="$(dirname "$p")"
  while [[ "$d" == */build/* || "$d" == */build ]] && rmdir "$d" 2>/dev/null; do
    d="$(dirname "$d")"
  done
}

uploaded=0; skipped=0; failed=0; total=0; freed_bytes=0
bytes_of() {
  if stat --version >/dev/null 2>&1; then stat -c %s "$1" 2>/dev/null || echo 0
  else stat -f %z "$1" 2>/dev/null || echo 0; fi
}
while IFS=$'\t' read -r local_path s3_key content_type cache_control; do
  total=$((total + 1))
  if [[ ! -f "$local_path" ]]; then
    echo "MISSING: $local_path" >&2
    failed=$((failed + 1)); continue
  fi
  local_sha=$($sha_cmd "$local_path" | awk '{print $1}')

  # HEAD origin to read x-amz-meta-sha256
  remote_sha=$(aws s3api head-object \
                 --bucket "$bucket" --key "$s3_key" \
                 --query 'Metadata.sha256' --output text 2>/dev/null || echo "MISSING")

  if [[ "$remote_sha" == "$local_sha" ]]; then
    skipped=$((skipped + 1))
    [[ "$dry_run" == "true" ]] && echo "skip  $s3_key"
    sz=$(bytes_of "$local_path")
    purge_local "$local_path"
    freed_bytes=$((freed_bytes + sz))
    continue
  fi

  if [[ "$dry_run" == "true" ]]; then
    if [[ "$remote_sha" == "MISSING" ]]; then
      echo "PLAN UPLOAD (new):  $s3_key"
    else
      echo "PLAN UPLOAD (drift): $s3_key  (remote=$remote_sha local=$local_sha)"
    fi
    uploaded=$((uploaded + 1)); continue
  fi

  # Native S3 SHA256 checksum is base64; AWS CLI computes it for us with
  # --checksum-algorithm SHA256 on `aws s3api put-object`. We use `aws s3 cp`
  # for transparent multipart on large files; --checksum-algorithm is
  # supported there since AWS CLI v2.
  if ! aws s3 cp "$local_path" "s3://$bucket/$s3_key" \
        ${S3_ACL:+--acl $S3_ACL} \
        --storage-class "${S3_STORAGE_CLASS:-STANDARD}" \
        --metadata "sha256=$local_sha" \
        --content-type "$content_type" \
        --cache-control "$cache_control" \
        --checksum-algorithm SHA256 \
        --no-progress >/dev/null; then
    echo "UPLOAD FAILED: $s3_key" >&2
    failed=$((failed + 1)); continue
  fi

  # Verify by re-reading the metadata
  new_sha=$(aws s3api head-object --bucket "$bucket" --key "$s3_key" \
              --query 'Metadata.sha256' --output text 2>/dev/null || echo "")
  if [[ "$new_sha" != "$local_sha" ]]; then
    echo "VERIFY FAILED: $s3_key  (expected $local_sha, got $new_sha)" >&2
    failed=$((failed + 1)); continue
  fi
  sz=$(bytes_of "$local_path")
  purge_local "$local_path"
  freed_bytes=$((freed_bytes + sz))
  echo "ok    $s3_key"
  uploaded=$((uploaded + 1))
done

echo "---"
freed_human=$(awk -v b="$freed_bytes" 'BEGIN{
  u="B"; if(b>1024){b/=1024;u="KiB"}; if(b>1024){b/=1024;u="MiB"}; if(b>1024){b/=1024;u="GiB"};
  printf "%.2f %s", b, u
}')
echo "total=$total uploaded=$uploaded skipped=$skipped failed=$failed dry_run=$dry_run freed=$freed_human"
if [[ "$failed" -gt 0 ]]; then exit 1; fi
