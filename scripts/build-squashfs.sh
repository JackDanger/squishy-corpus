#!/usr/bin/env bash
# build-squashfs.sh — build a squashfs image, reproducibly.
# Usage: build-squashfs.sh <comp> <ordering> <src-dir> <out.squashfs>
#   comp:     gzip | xz | lz4 | zstd | lzo
#   ordering: alpha | size-desc   (squashfs is solid; ordering affects ratio)
set -euo pipefail
comp="$1"; order="$2"; src="$3"; out="$4"
mkdir -p "$(dirname "$out")"
tmp="$(cd "$(dirname "$out")" && pwd)/$(basename "$out").tmp"
rm -f "$tmp"

# mksquashfs needs -comp <name> and reproducible flags.
# Ordering can be enforced by passing -sort <file> where each line is "<path> <prio>";
# higher prio == placed first.
sortfile=$(mktemp)
trap 'rm -f "$sortfile"' EXIT
# mksquashfs sort priority is signed-int16 (-32768..32767). Start near the
# top and count down — fine for our largest set (~60 files).
prio=32767
"$(dirname "$0")/order-files.sh" "$order" "$src" | while read -r p; do
  echo "${p#$(basename "$src")/} $prio"
  prio=$((prio - 1))
done > "$sortfile"

# SOURCE_DATE_EPOCH=0 (set in Makefile env) handles timestamp normalisation;
# mksquashfs 4.5+ refuses to combine that with explicit -all-time/-mkfs-time.
mksquashfs "$src" "$tmp" \
  -comp "$comp" \
  -no-exports -no-recovery -no-progress \
  -sort "$sortfile" \
  -noappend -quiet 2>/dev/null || {
    mksquashfs "$src" "$tmp" -comp "$comp" \
      -no-exports -no-recovery \
      -sort "$sortfile" -noappend
}
mv "$tmp" "$out"
