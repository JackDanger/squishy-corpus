#!/usr/bin/env bash
# build-container.sh — build cpio / pax / ar (uncompressed containers).
# Usage: build-container.sh <kind> <src-dir> <out>
set -euo pipefail
kind="$1"; src="$2"; out="$3"
mkdir -p "$(dirname "$out")"
tmp="$(cd "$(dirname "$out")" && pwd)/$(basename "$out").tmp"
parent=$(dirname "$src")
base=$(basename "$src")

case "$kind" in
  cpio)
    (cd "$parent" && find "$base" -type f | LC_ALL=C sort | \
       cpio -o -H newc --quiet 2>/dev/null > "$tmp") || \
    (cd "$parent" && find "$base" -type f | LC_ALL=C sort | \
       cpio -o -H newc > "$tmp" 2>/dev/null)
    ;;
  pax)
    GTAR=$(command -v gtar 2>/dev/null || command -v tar)
    "$GTAR" --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
            --format=pax -C "$parent" -cf "$tmp" "$base"
    ;;
  ar)
    # ar doesn't have a recursive mode; concatenate all leaf files
    (cd "$parent" && find "$base" -type f | LC_ALL=C sort | \
       xargs ar -rcD "$tmp" 2>/dev/null) || \
    (cd "$parent" && find "$base" -type f | LC_ALL=C sort | \
       xargs ar -rc  "$tmp")
    ;;
  *) echo "unknown container kind: $kind" >&2; exit 2 ;;
esac
mv "$tmp" "$out"
