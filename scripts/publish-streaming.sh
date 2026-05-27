#!/usr/bin/env bash
# stream-publish.sh — build → upload → delete, ONE artifact at a time.
#
# Reads a plan TSV on stdin:
#   <local_path>\t<s3_key>\t<content_type>\t<cache_control>
#
# For each line:
#   1. If local_path is missing, build it via `make <local_path>`
#   2. Compute sha256, HEAD origin; skip if already uploaded
#   3. Upload with metadata + native S3 checksum
#   4. Verify by HEAD
#   5. Delete the local artifact + prune empty parent dirs
#
# Peak local disk = raw inputs + one in-flight artifact. Designed for tight
# disk environments. Set CORPUS_KEEP_LOCAL=1 to disable post-upload purge.
#
# Usage:
#   aws-vault exec personal -- bash stream-publish.sh <bucket> < plan.tsv

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <bucket> [--dry-run]" >&2
  exit 1
fi
bucket="$1"; shift
dry_run="false"
if [[ "${1:-}" == "--dry-run" ]]; then dry_run="true"; shift; fi

sha_cmd="shasum -a 256"
command -v sha256sum >/dev/null 2>&1 && sha_cmd="sha256sum"

bytes_of() {
  if stat --version >/dev/null 2>&1; then stat -c %s "$1" 2>/dev/null || echo 0
  else stat -f %z "$1" 2>/dev/null || echo 0; fi
}

human() {
  awk -v b="$1" 'BEGIN{
    u="B";  if(b>=1024){b/=1024;u="KiB"};
            if(b>=1024){b/=1024;u="MiB"};
            if(b>=1024){b/=1024;u="GiB"};
            if(b>=1024){b/=1024;u="TiB"};
    printf "%.1f%s", b, u
  }'
}

hms() {
  local s=$1
  printf "%dm%02ds" $((s/60)) $((s%60))
}

purge_local() {
  local p="$1"
  [[ "${CORPUS_KEEP_LOCAL:-0}" == "1" ]] && return 0
  [[ "$dry_run" == "true" ]] && return 0
  rm -f "$p"
  local d="$(dirname "$p")"
  while [[ "$d" == */build/* || "$d" == */build ]] && rmdir "$d" 2>/dev/null; do
    d="$(dirname "$d")"
  done
}

# Buffer stdin so we can count total lines and pre-scan remote.
plan_file=$(mktemp)
remote_file=$(mktemp)
annot_file=$(mktemp)
trap 'rm -f "$plan_file" "$remote_file" "$annot_file"' EXIT
cat > "$plan_file"
total_files=$(grep -c . "$plan_file" 2>/dev/null || echo 0)

# ─── Pre-scan: one s3 ls instead of one HEAD per file ────────────────────
# Without this, a "skip" on an already-uploaded artifact would still
# rebuild + sha256 the local copy (~30 s for a 600 MB tar.bz2). With it,
# skip is just a set-membership check (microseconds).
if [[ "$dry_run" != "true" ]]; then
  echo "scanning s3://$bucket/ for existing artifacts ..." >&2
  scan_start=$(date +%s)
  aws s3 ls "s3://$bucket/" --recursive 2>/dev/null | awk '{print $4}' \
      > "$remote_file" || true
  remote_count=$(grep -c . "$remote_file" 2>/dev/null || echo 0)
  echo "  found $remote_count existing objects in $(( $(date +%s) - scan_start ))s" >&2
fi

# Annotate plan: mark each line SKIP (already in S3) or UPLOAD.
awk -F'\t' -v rf="$remote_file" '
  BEGIN { while ((getline k < rf) > 0) remote[k] = 1 }
  { if ($2 in remote) print "SKIP\t"   $0
    else              print "UPLOAD\t" $0 }
' "$plan_file" > "$annot_file"

start_time=$(date +%s)
built=0; uploaded=0; skipped=0; failed=0; total=0; freed_bytes=0; bytes_done=0

progress_line() {  # op key bytes_this_file
  local op="$1" key="$2" sz="$3"
  bytes_done=$((bytes_done + sz))
  local processed=$((uploaded + skipped))
  local elapsed=$(( $(date +%s) - start_time ))
  local rate=0; [[ $elapsed -gt 0 ]] && rate=$(( bytes_done / elapsed ))
  local eta=""
  if (( processed > 5 && elapsed > 0 )); then
    local per_file=$(( elapsed / processed ))
    local remaining=$(( total_files - processed ))
    eta="ETA $(hms $((remaining * per_file)))"
  fi
  printf "%-6s [%4d/%d] %-70s  %8s  @ %7s/s  total %8s  %s\n" \
    "$op" "$processed" "$total_files" "$key" "$(human $sz)" "$(human $rate)" "$(human $bytes_done)" "$eta"
}

while IFS=$'\t' read -r status local_path s3_key content_type cache_control; do
  total=$((total + 1))

  # ── Fast SKIP path: key already exists on S3 (from pre-scan) ──
  if [[ "$status" == "SKIP" ]]; then
    sz=0
    if [[ -f "$local_path" ]]; then
      sz=$(bytes_of "$local_path")
      purge_local "$local_path"
      freed_bytes=$((freed_bytes + sz))
    fi
    skipped=$((skipped + 1))
    if [[ "$dry_run" == "true" ]]; then echo "skip  $s3_key"
    else progress_line "skip" "$s3_key" "$sz"; fi
    continue
  fi

  # ── UPLOAD path: build if missing, then upload + verify + purge ──
  if [[ ! -f "$local_path" ]]; then
    if [[ "$dry_run" == "true" ]]; then
      echo "PLAN BUILD: $local_path"
    else
      if ! make -s "$local_path" 2>&1 | tail -3; then
        echo "BUILD FAILED: $local_path" >&2
        failed=$((failed + 1)); continue
      fi
      built=$((built + 1))
    fi
  fi
  [[ ! -f "$local_path" ]] && { echo "STILL MISSING: $local_path" >&2; failed=$((failed+1)); continue; }

  if [[ "$dry_run" == "true" ]]; then
    echo "PLAN UPLOAD: $s3_key  ($(bytes_of "$local_path") bytes)"
    uploaded=$((uploaded + 1)); continue
  fi

  local_sha=$($sha_cmd "$local_path" | awk '{print $1}')

  if ! aws s3 cp "$local_path" "s3://$bucket/$s3_key" \
        --acl public-read \
        --storage-class ONEZONE_IA \
        --metadata "sha256=$local_sha" \
        --content-type "$content_type" \
        --cache-control "$cache_control" \
        --checksum-algorithm SHA256 \
        --no-progress >/dev/null; then
    echo "UPLOAD FAILED: $s3_key" >&2
    failed=$((failed + 1)); continue
  fi

  new_sha=$(aws s3api head-object --bucket "$bucket" --key "$s3_key" \
              --query 'Metadata.sha256' --output text 2>/dev/null || echo "")
  if [[ "$new_sha" != "$local_sha" ]]; then
    echo "VERIFY FAILED: $s3_key  (expected $local_sha, got $new_sha)" >&2
    failed=$((failed + 1)); continue
  fi

  sz=$(bytes_of "$local_path")
  purge_local "$local_path"
  freed_bytes=$((freed_bytes + sz))
  uploaded=$((uploaded + 1))
  progress_line "ok" "$s3_key" "$sz"
done < "$annot_file"

echo "---"
elapsed=$(( $(date +%s) - start_time ))
avg_rate=0; [[ $elapsed -gt 0 ]] && avg_rate=$(( bytes_done / elapsed ))
echo "total=$total built=$built uploaded=$uploaded skipped=$skipped failed=$failed"
echo "freed=$(human $freed_bytes)  transferred=$(human $bytes_done)  elapsed=$(hms $elapsed)  avg=$(human $avg_rate)/s"
if [[ "$failed" -gt 0 ]]; then exit 1; fi
