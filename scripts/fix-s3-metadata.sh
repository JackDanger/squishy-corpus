#!/usr/bin/env bash
# fix-s3-metadata.sh — walk s3://<bucket>/<prefix>/ and rewrite each object
# in place with correct content-type, cache-control, ONEZONE_IA, ACL, and
# preserved x-amz-meta-sha256.
#
# Why: `aws s3 cp --metadata-directive COPY` does NOT preserve Content-Type
# during in-place storage-class changes — the new object gets a re-detected
# (usually wrong) content type. This script is idempotent (skips objects
# already in the target state).
#
# Usage:
#   aws-vault exec personal -- bash fix-s3-metadata.sh <bucket> <prefix>

set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "usage: $0 <bucket> <prefix>" >&2; exit 2
fi
bucket="$1"
prefix="${2%/}"

CC_IMMUTABLE="public, max-age=31536000, immutable"
CC_INDEX="public, max-age=300, must-revalidate"

# basename → content-type. Strips trailing .l<digits> level annotation
# (e.g., dickens.gz.l9 is treated the same as dickens.gz).
content_type_for() {
  local name="${1##*/}"
  name="${name%.l[0-9]}"
  name="${name%.l[0-9][0-9]}"
  case "$name" in
    *.tar.gz)  echo "application/gzip" ;;
    *.tar.bz2) echo "application/x-bzip2" ;;
    *.tar.xz)  echo "application/x-xz" ;;
    *.tar.zst) echo "application/zstd" ;;
    *.tar.lz4) echo "application/x-lz4" ;;
    *.tar.br)  echo "application/x-brotli" ;;
    *.tar.lzma)echo "application/x-lzma" ;;
    *.tar.lz)  echo "application/x-lzip" ;;
    *.tar)     echo "application/x-tar" ;;
    *.gz)      echo "application/gzip" ;;
    *.bz2)     echo "application/x-bzip2" ;;
    *.xz)      echo "application/x-xz" ;;
    *.zst)     echo "application/zstd" ;;
    *.lz4)     echo "application/x-lz4" ;;
    *.br)      echo "application/x-brotli" ;;
    *.lzma)    echo "application/x-lzma" ;;
    *.lz)      echo "application/x-lzip" ;;
    *.lzo)     echo "application/x-lzop" ;;
    *.zpaq)    echo "application/x-zpaq" ;;
    *.7z|*.7z.*)   echo "application/x-7z-compressed" ;;
    *.zip|*.zip.*) echo "application/zip" ;;
    *.cpio)    echo "application/x-cpio" ;;
    *.squashfs|*.squashfs.*) echo "application/x-squashfs" ;;
    *.pax)     echo "application/x-tar" ;;
    *.ar)      echo "application/x-archive" ;;
    *.html)    echo "text/html; charset=utf-8" ;;
    *.css)     echo "text/css; charset=utf-8" ;;
    *.js)      echo "application/javascript" ;;
    *.json)    echo "application/json" ;;
    *.ndjson)  echo "application/x-ndjson" ;;
    *.log|*.txt|*.sha256) echo "text/plain; charset=utf-8" ;;
    *.sqlite)  echo "application/vnd.sqlite3" ;;
    *.parquet) echo "application/vnd.apache.parquet" ;;
    *.wasm)    echo "application/wasm" ;;
    *.zdict|*.bin) echo "application/octet-stream" ;;
    *.concat-gz)  echo "application/gzip" ;;
    *.concat-xz)  echo "application/x-xz" ;;
    *.concat-zst|*.concat-zst-skipframes|*.concat-zst-trained-dict) echo "application/zstd" ;;
    *)         echo "application/octet-stream" ;;
  esac
}

cache_control_for() {
  local name="${1##*/}"
  case "$name" in
    index.txt|manifest.json|CHECKSUMS.sha256|versions.txt|expected-ratio.json|README.txt|index.html|listing.html|plan.tsv)
      echo "$CC_INDEX" ;;
    *) echo "$CC_IMMUTABLE" ;;
  esac
}

echo "scanning s3://$bucket/$prefix/ ..."
keys_file=$(mktemp); trap 'rm -f "$keys_file"' EXIT
aws s3api list-objects-v2 --bucket "$bucket" --prefix "$prefix/" \
  --query 'Contents[].Key' --output text 2>/dev/null \
  | tr '\t' '\n' | grep -v '^$' > "$keys_file"
total=$(wc -l < "$keys_file" | tr -d ' ')
echo "found $total objects"

fixed=0; skipped=0; failed=0; n=0
while IFS= read -r key; do
  n=$((n + 1))
  # HEAD to read current metadata
  head=$(aws s3api head-object --bucket "$bucket" --key "$key" --output json 2>/dev/null) || {
    echo "HEAD failed: $key" >&2; failed=$((failed+1)); continue
  }
  current_ct=$(echo "$head" | awk -F'"' '/"ContentType"/   {print $4; exit}')
  current_cc=$(echo "$head" | awk -F'"' '/"CacheControl"/  {print $4; exit}')
  current_sc=$(echo "$head" | awk -F'"' '/"StorageClass"/  {print $4; exit}')
  sha=$(echo "$head" | awk -F'"' '/"sha256"/ {print $4; exit}')

  want_ct=$(content_type_for "$key")
  want_cc=$(cache_control_for "$key")

  # Conservative: only override Content-Type when it's clearly missing/wrong
  # (empty, binary/octet-stream, application/octet-stream). If the origin
  # has a specific content-type already (set at upload time by stream-publish
  # via plan-publish.py), trust it — that source-of-truth is more reliable
  # than our shell-level pattern matching.
  if [[ -n "$current_ct" \
        && "$current_ct" != "binary/octet-stream" \
        && "$current_ct" != "application/octet-stream" ]]; then
    want_ct="$current_ct"
  fi

  if [[ "$current_ct" == "$want_ct" && "$current_cc" == "$want_cc" && "$current_sc" == "ONEZONE_IA" ]]; then
    skipped=$((skipped + 1))
    continue
  fi

  meta_arg=()
  if [[ -n "$sha" ]]; then
    meta_arg=(--metadata "sha256=$sha")
  else
    meta_arg=(--metadata "{}")
  fi

  if aws s3api copy-object --bucket "$bucket" --key "$key" \
       --copy-source "$bucket/$key" \
       --metadata-directive REPLACE \
       "${meta_arg[@]}" \
       --content-type "$want_ct" \
       --cache-control "$want_cc" \
       --storage-class ONEZONE_IA \
       --acl public-read \
       --output json >/dev/null 2>&1; then
    fixed=$((fixed + 1))
    printf "[%4d/%d] fixed  %s  ct=%s  storage=ONEZONE_IA\n" "$n" "$total" "$key" "$want_ct"
  else
    failed=$((failed + 1))
    echo "COPY failed: $key" >&2
  fi
done < "$keys_file"

echo "---"
echo "total=$total fixed=$fixed skipped=$skipped failed=$failed"
[[ $failed -gt 0 ]] && exit 1 || exit 0
