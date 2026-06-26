#!/usr/bin/env bash
# deploy-site.sh — push build/site to the live site (S3 origin + CloudFront).
#
# The S3 bucket is private (OAC only); we never expose direct S3. The site is
# served at https://squishy.jackdanger.com from origin path /draft.
#
# Idempotent: large assets go through `aws s3 sync` (only changed files upload);
# the small text files are always re-put with explicit charset content-types and
# a short cache, then we invalidate the CDN so the change is live immediately.
#
# Usage:
#   make deploy                      # builds the site first, then runs this
#   bash scripts/deploy-site.sh      # deploy whatever is already in build/site
#
# Env overrides: S3_BUCKET, S3_PREFIX, CF_DISTRIBUTION_ID, SITE_URL.
set -euo pipefail

BUCKET="${S3_BUCKET:-squishy-corpus}"
PREFIX="${S3_PREFIX:-draft}"
DIST_ID="${CF_DISTRIBUTION_ID:-E2UVD5LCNEUNSU}"
SITE_URL="${SITE_URL:-https://squishy.jackdanger.com}"

site="build/site"
dest="s3://$BUCKET/$PREFIX"

if [[ ! -f "$site/index.html" ]]; then
  echo "no $site/index.html — run 'make site' first" >&2
  exit 1
fi

# Fail early + clearly on expired creds (the usual culprit) instead of mid-upload.
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "AWS credentials are missing or expired — refresh them (e.g. 'aws sso login') and re-run." >&2
  exit 1
fi

echo "→ deploying $site → $dest  (cdn: $DIST_ID)"

# 1+2) Upload every file with an explicit content-type, its bytes' SHA-256 as
#      x-amz-meta-sha256, AND an S3-native SHA256 checksum (--checksum-algorithm SHA256)
#      so every object is self-describing and integrity-verifiable straight from the S3
#      HEAD/GET API — the data tier already carries x-amz-meta-sha256 (set at publish);
#      this gives the metadata + license tier the same. Per-file (not `s3 sync`) because
#      the sha metadata is per-file; build/site is small so re-putting each deploy is cheap.
find "$site" -type f -print0 \
  | while IFS= read -r -d '' f; do
      rel="${f#"$site"/}"
      sha=$(shasum -a 256 "$f" | cut -d' ' -f1)
      case "$rel" in
        *.html)               ct="text/html; charset=utf-8";            cache="public, max-age=120, must-revalidate" ;;
        *.js)                 ct="application/javascript; charset=utf-8"; cache="public, max-age=120, must-revalidate" ;;
        *.json)               ct="application/json; charset=utf-8";      cache="public, max-age=120, must-revalidate" ;;
        *.csv)                ct="text/csv; charset=utf-8";              cache="public, max-age=86400" ;;
        *.jpg|*.jpeg)         ct="image/jpeg";                           cache="public, max-age=86400" ;;
        *.svg)                ct="image/svg+xml";                        cache="public, max-age=86400" ;;
        *)                    ct="text/plain; charset=utf-8";            cache="public, max-age=86400" ;;
      esac
      aws s3 cp "$f" "$dest/$rel" \
        --content-type "$ct" \
        --cache-control "$cache" \
        --metadata "sha256=$sha" \
        --checksum-algorithm SHA256 \
        --no-progress
    done

# 3) Invalidate the CDN so the change is live now.
inv=$(aws cloudfront create-invalidation \
        --distribution-id "$DIST_ID" \
        --paths '/*' \
        --query 'Invalidation.Id' --output text)

echo "---"
echo "deployed. cloudfront invalidation: $inv"
echo "live in ~30–60s: $SITE_URL"
