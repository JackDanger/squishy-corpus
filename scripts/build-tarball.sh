#!/usr/bin/env bash
# Build the citable one-shot tarball DETERMINISTICALLY — identical bytes (and sha256)
# on any machine, so the Zenodo DOI cites a fixed artifact. Normalises name order,
# ownership, permissions, and mtime (the things that otherwise leak into tar bytes).
#
#   scripts/build-tarball.sh <src-dir> <out.tar>
#
# Requires GNU tar (gtar on macOS) for --sort/--numeric-owner/--mtime. Intended to run
# at FREEZE over the assembled corpus directory; not part of routine dev.
set -euo pipefail
SRC="${1:?usage: build-tarball.sh <src-dir> <out.tar>}"
OUT="${2:?usage: build-tarball.sh <src-dir> <out.tar>}"
TAR="$(command -v gtar || command -v tar)"
if ! "$TAR" --version | grep -qi 'GNU tar'; then
  echo "FATAL: need GNU tar (brew install gnu-tar) for deterministic output" >&2; exit 1
fi
"$TAR" --sort=name --owner=0 --group=0 --numeric-owner --mtime='@0' \
       --mode='go-w' --format=ustar -C "$SRC" -cf "$OUT" .
sha=$(shasum -a 256 "$OUT" | awk '{print $1}')
echo "wrote $OUT"
echo "sha256 $sha  ($(du -h "$OUT" | awk '{print $1}'))"
echo "(deterministic: re-running on the same inputs reproduces this sha256)"
