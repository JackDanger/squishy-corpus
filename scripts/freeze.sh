#!/usr/bin/env bash
# freeze.sh — the Squishy-2026 freeze. OWNER-RUN, IRREVERSIBLE. Do not run until the
# #17 sign-offs (representativeness, legal/counsel, PII, verification pass-4) are
# green. Refuses to run without --confirm.
#
#   aws-vault exec personal -- bash scripts/freeze.sh squishy-corpus --confirm
#
# The edition IS the version: the frozen prefix is the edition year (2026/), the git
# tag is the edition name (Squishy-2026), and the DOI is its permanence anchor. There
# is no "v1.0" — see GOVERNANCE.md "Versioning: the edition is the version".
#
# Steps: (1) re-audit draft/ integrity+public, (2) verify draft/ vs published
# CHECKSUMS, (3) server-side copy draft/ -> the pristine 2026/, (4) print the
# remaining manual steps (git tag, Zenodo DOI, backup, announce).
set -euo pipefail
B="${1:?usage: freeze.sh <bucket> --confirm}"; shift || true
[[ "${1:-}" == "--confirm" ]] || { echo "refusing: pass --confirm (this is irreversible)"; exit 1; }
PREFIX="2026"   # the edition year — the immutable frozen prefix

echo "== 1/4 audit draft/ (the live base_url serves the draft prefix) =="
uv run python scripts/audit-distribution.py

echo "== 2/4 verify $PREFIX/ is empty (must be pristine) =="
n=$(aws s3 ls "s3://$B/$PREFIX/" --recursive 2>/dev/null | wc -l | tr -d ' ')
[[ "$n" == "0" ]] || { echo "ABORT: s3://$B/$PREFIX/ is not empty ($n objects). Freeze must be the first write."; exit 1; }

echo "== 3/4 copy draft/ -> $PREFIX/ (server-side, immutable cache) =="
# Allowlist ONLY the frozen product. draft/ also holds retired byte-property-cube
# build artifacts (individual/, bundle/, bundles/, negative/, bench/) — ~57 GB
# that must NOT be immortalized in the permanent DOI. Copy the curated set only.
INCLUDES=(
  --exclude "*"
  --include "corpus/*"                      # the named core files
  --include "scale/*"                     # scale-tier (weights ladder, large files)
  --include "LICENSES/*"                  # full license texts
  --include "index.html"                  # the primary page (hero + 3D cube + datasets)
  --include "squishy-cube.js"             # the 3D-cube renderer
  --include "cube-data.json"              # the 3D-cube data (live metrics)
  --include "photo.jpg" --include "movie.jpg"   # rendered preview assets
  --include "provenance/*"                # legacy explorer path (redirect to primary)
  --include "provenance.html" --include "review.html"   # legacy redirects
  --include "LICENSE-MANIFEST.csv"
  --include "CHECKSUMS.sha256"
  --include "NOTICE"
  --include "squishy-board-complete.json"   # the whole-corpus Squishy Score board (every codec)
  --include "squishy-score-complete.json"   # the round-trip-verified reference score
  --include "verification-pass4.json"
  --include "size-convergence.json"
  --include "file-properties.json"        # intrinsic byte properties (the 3D-cube axes)
  --include "scale-properties.json"       # intrinsic properties of the scale-tier files
  --include "edition.json"                # per-file URL+sha edition manifest
  --include "schema.json"                 # the constitution (roster shape) the DOI also carries
  --include "baseline.json"              # the golden verification anchor (fingerprint + toolchain)
)
echo "   dry run — objects that WILL enter $PREFIX/:"
aws s3 cp "s3://$B/draft/" "s3://$B/$PREFIX/" --recursive --dryrun "${INCLUDES[@]}" \
  --metadata-directive COPY --cache-control "public, max-age=31536000, immutable"
read -r -p "   proceed with the above (and ONLY the above) into the permanent $PREFIX/? [y/N] " ok
[[ "$ok" == "y" ]] || { echo "ABORT: not confirmed."; exit 1; }
aws s3 cp "s3://$B/draft/" "s3://$B/$PREFIX/" --recursive "${INCLUDES[@]}" \
  --metadata-directive COPY --cache-control "public, max-age=31536000, immutable"

echo "== 4/4 done. Remaining MANUAL steps =="
cat <<EOF
  - git tag Squishy-2026 && git push --tags
  - mint the DOI:   ZENODO_TOKEN=<fresh-token> uv run python scripts/zenodo-deposit.py
  - backup:         aws s3 sync s3://$B/$PREFIX/ s3://<dr-bucket>/$PREFIX/   (cross-region)
  - update CITATION.cff + the runner's DOI fetch with the minted DOI
  - announce
$PREFIX/ is now populated and immutable. Squishy-2026 is frozen.
EOF
