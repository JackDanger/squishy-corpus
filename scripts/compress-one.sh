#!/usr/bin/env bash
# compress-one.sh — compress a single file with one codec, deterministically.
# Usage: compress-one.sh <codec> <input> <output>
set -euo pipefail
codec="$1"; src="$2"; dst="$3"
tmp="$dst.tmp"

case "$codec" in
  gz)    gzip  -n -k -9        -c "$src" > "$tmp" ;;
  bz2)   bzip2 -k -9           -c "$src" > "$tmp" ;;
  xz)    xz    -k -T1 -9e      -c "$src" > "$tmp" ;;
  zst)   zstd  -T1 -19 -q -f --no-progress "$src" -o "$tmp" ;;
  lz4)   lz4   -9 -q -c        "$src"    > "$tmp" ;;
  br)    brotli -k -q 11       -c "$src" > "$tmp" ;;
  lzma)  lzma  -k -9           -c "$src" > "$tmp" ;;
  lz)    lzip  -k -9           -c "$src" > "$tmp" ;;
  lzo)   lzop  -n -9           -c "$src" > "$tmp" ;;
  zpaq)  rm -f "$tmp"; zpaq add "$tmp" "$src" -m5 > /dev/null ;;
  *)     echo "unknown codec: $codec" >&2; exit 2 ;;
esac
mv "$tmp" "$dst"
