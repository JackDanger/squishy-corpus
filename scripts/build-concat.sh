#!/usr/bin/env bash
# build-concat.sh — multi-member stream (no tar): each file compressed,
# then the compressed bytes concatenated. gzip/xz/zstd all support this.
# Usage: build-concat.sh <codec> <src-dir> <out>
set -euo pipefail
codec="$1"; src="$2"; out="$3"
mkdir -p "$(dirname "$out")"
tmp="$(cd "$(dirname "$out")" && pwd)/$(basename "$out").tmp"
rm -f "$tmp"

cmd=""
case "$codec" in
  gz)  cmd="gzip  -n -9 -c" ;;
  xz)  cmd="xz    -T1 -9e -c" ;;
  zst) cmd="zstd  -T1 -19 -q -f --no-progress -c" ;;
  *)   echo "unknown concat codec: $codec" >&2; exit 2 ;;
esac

(cd "$(dirname "$src")" && find "$(basename "$src")" -type f | LC_ALL=C sort | \
   while read -r f; do $cmd "$f"; done) > "$tmp"
mv "$tmp" "$out"
