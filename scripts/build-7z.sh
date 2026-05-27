#!/usr/bin/env bash
# build-7z.sh — build a 7z archive with chosen method and ordering.
# Usage: build-7z.sh <method> <ordering> <src-dir> <out.7z>
#   method:   lzma2 | ppmd | bzip2 | deflate
#   ordering: alpha | size-desc   (solid-archive ordering matters)
set -euo pipefail
method="$1"; order="$2"; src="$3"; out="$4"
mkdir -p "$(dirname "$out")"
tmp="$(cd "$(dirname "$out")" && pwd)/$(basename "$out").tmp.7z"
rm -f "$tmp"
P7Z=$(command -v 7z 2>/dev/null || command -v 7zz 2>/dev/null || true)

# Detect large input (>200 MiB) to avoid OOM on PPMD/LZMA2 with incompressible data.
LARGE_THRESHOLD=$((200 * 1024 * 1024))
total_bytes=$(du -sb "$src" 2>/dev/null | cut -f1 || \
              du -sk "$src" 2>/dev/null | awk '{print $1 * 1024}')
large=0
[[ "${total_bytes:-0}" -gt $LARGE_THRESHOLD ]] && large=1

method_arg=""
case "$method" in
  lzma2)   [[ $large -eq 1 ]] && method_arg="-m0=lzma2 -mx=1 -ms=on -md=32m" \
                               || method_arg="-m0=lzma2 -mx=9 -ms=on -md=128m" ;;
  ppmd)    [[ $large -eq 1 ]] && method_arg="-m0=ppmd  -mx=1 -ms=on" \
                               || method_arg="-m0=ppmd  -mx=9 -ms=on" ;;
  bzip2)   [[ $large -eq 1 ]] && method_arg="-m0=bzip2 -mx=1 -ms=on" \
                               || method_arg="-m0=bzip2 -mx=9 -ms=on" ;;
  deflate) [[ $large -eq 1 ]] && method_arg="-m0=deflate -mx=1 -ms=on" \
                               || method_arg="-m0=deflate -mx=9 -ms=on" ;;
  *) echo "unknown 7z method: $method" >&2; exit 2 ;;
esac

# Build an explicit file list with the chosen ordering, then feed it to 7z.
# 7z respects -i@listfile for "include these files".
listfile=$(mktemp)
trap 'rm -f "$listfile"' EXIT
"$(dirname "$0")/order-files.sh" "$order" "$src" > "$listfile"

# 7z paths must be relative to current working directory
parent=$(dirname "$src")
(cd "$parent" && "$P7Z" a -mtm=off -mtc=off -mta=off -bd -bb0 -y \
    $method_arg "$tmp" -i@"$listfile" > /dev/null)
mv "$tmp" "$out"
