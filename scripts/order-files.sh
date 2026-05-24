#!/usr/bin/env bash
# order-files.sh — print a tar -T file list with a chosen ordering.
# Usage: order-files.sh <alpha|size-desc|size-asc|random> <root-dir>
#
# The path printed is relative to the parent of <root-dir>, so callers can do
# `tar -C $(parent_of_root) -T list -cf out.tar`.
set -euo pipefail
order="$1"; root="$2"
parent=$(dirname "$root")
base=$(basename "$root")

cd "$parent"

case "$order" in
  alpha)
    find "$base" -type f | LC_ALL=C sort
    ;;
  size-desc)
    if stat --version >/dev/null 2>&1; then
      find "$base" -type f -printf "%s\t%p\n" | sort -k1,1nr | cut -f2
    else
      find "$base" -type f -exec stat -f "%z%t%N" {} + | sort -k1,1nr | cut -f2
    fi
    ;;
  size-asc)
    if stat --version >/dev/null 2>&1; then
      find "$base" -type f -printf "%s\t%p\n" | sort -k1,1n | cut -f2
    else
      find "$base" -type f -exec stat -f "%z%t%N" {} + | sort -k1,1n  | cut -f2
    fi
    ;;
  random)
    # deterministic random: sort by sha256(path) hex
    sha=$(command -v sha256sum || echo "shasum -a 256")
    find "$base" -type f | while read -r p; do
      h=$(printf "%s" "$p" | $sha | awk '{print $1}')
      printf "%s\t%s\n" "$h" "$p"
    done | LC_ALL=C sort | cut -f2
    ;;
  *)
    echo "unknown ordering: $order" >&2; exit 2
    ;;
esac
