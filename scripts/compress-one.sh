#!/usr/bin/env bash
# compress-one.sh — compress a single file with one codec, deterministically.
# Usage: compress-one.sh <codec> <input> <output>
set -euo pipefail
codec="$1"; src="$2"; dst="$3"
tmp="$dst.tmp"

# Files over 200 MiB are typically pathological (random/incompressible bundles).
# High quality levels would take many hours without meaningful size benefit.
LARGE_THRESHOLD=$((200 * 1024 * 1024))
sz=$(stat -f %z "$src" 2>/dev/null || stat -c %s "$src")

case "$codec" in
  gz)    q=9;  [[ $sz -gt $LARGE_THRESHOLD ]] && q=1
               gzip  -n -k -"$q" -c "$src" > "$tmp" ;;
  bz2)   q=9;  [[ $sz -gt $LARGE_THRESHOLD ]] && q=1
               bzip2 -k -"$q"    -c "$src" > "$tmp" ;;
  xz)    xf=-9e; [[ $sz -gt $LARGE_THRESHOLD ]] && xf=-1
               xz    -k -T1 "$xf" -c "$src" > "$tmp" ;;
  zst)   q=19; [[ $sz -gt $LARGE_THRESHOLD ]] && q=3
               zstd  -T1 -"$q" -q -f --no-progress "$src" -o "$tmp" ;;
  lz4)   lz4   -9 -q -c        "$src"    > "$tmp" ;;
  br)    q=11; [[ $sz -gt $((16 * 1024 * 1024)) ]] && q=1
               brotli -k -q "$q" -c "$src" > "$tmp" ;;
  lzma)  q=9;  [[ $sz -gt $LARGE_THRESHOLD ]] && q=1
               lzma  -k -"$q"    -c "$src" > "$tmp" ;;
  lz)    q=9;  [[ $sz -gt $LARGE_THRESHOLD ]] && q=1
               lzip  -k -"$q"    -c "$src" > "$tmp" ;;
  lzo)   lzop  -n -9           -c "$src" > "$tmp" ;;
  zpaq)  m=5;  [[ $sz -gt $LARGE_THRESHOLD ]] && m=1
               rm -f "$tmp"; zpaq add "$tmp" "$src" -m"$m" > /dev/null ;;
  *)     echo "unknown codec: $codec" >&2; exit 2 ;;
esac
mv "$tmp" "$dst"
