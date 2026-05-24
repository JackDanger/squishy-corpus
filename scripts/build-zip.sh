#!/usr/bin/env bash
# build-zip.sh — build a zip archive with the chosen internal codec.
# Usage: build-zip.sh <internal-codec> <src-dir> <out.zip>
#   internal-codec: store | deflate | bzip2 | lzma | zstd
set -euo pipefail
codec="$1"; src="$2"; out="$3"
mkdir -p "$(dirname "$out")"
# Absolute tmp path: helpers cd into the source dir, so a relative tmp would
# resolve under the wrong cwd.
tmp="$(cd "$(dirname "$out")" && pwd)/$(basename "$out").tmp.zip"
rm -f "$tmp"
P7Z=$(command -v 7z 2>/dev/null || command -v 7zz 2>/dev/null || true)

# zip tool handles store/deflate/bzip2; 7z handles lzma/zstd while still
# writing a valid .zip container.
case "$codec" in
  store)
    (cd "$(dirname "$src")" && zip -X -q -0    "$tmp" -r "$(basename "$src")")
    ;;
  deflate)
    (cd "$(dirname "$src")" && zip -X -q -Z deflate -9 "$tmp" -r "$(basename "$src")")
    ;;
  bzip2)
    # macOS BSD zip doesn't support -Z bzip2; route through 7z (writes valid .zip)
    (cd "$(dirname "$src")" && "$P7Z" a -mtm=off -mtc=off -mta=off -tzip -mm=bzip2 -mx=9 -bd -bb0 -y "$tmp" "$(basename "$src")" > /dev/null)
    ;;
  lzma)
    (cd "$(dirname "$src")" && "$P7Z" a -mtm=off -mtc=off -mta=off -tzip -mm=LZMA -mx=9 -bd -bb0 -y "$tmp" "$(basename "$src")" > /dev/null)
    ;;
  zstd)
    (cd "$(dirname "$src")" && "$P7Z" a -mtm=off -mtc=off -mta=off -tzip -mm=zstd -mx=9 -bd -bb0 -y "$tmp" "$(basename "$src")" > /dev/null)
    ;;
  *) echo "unknown zip internal codec: $codec" >&2; exit 2 ;;
esac
mv "$tmp" "$out"
