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
#   aws-vault exec personal -- bash scripts/deploy-site.sh   # if you use aws-vault
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

# 1) Large/static assets: sync, only changed files upload. Long cache (the CDN
#    invalidation below covers any that did change this run).
aws s3 sync "$site" "$dest" \
  --no-progress \
  --cache-control "public, max-age=86400" \
  --exclude '*.html' --exclude '*.js' --exclude '*.json'

# 2) Text files (html/js/json): always re-put
#    with an explicit charset content-type and a short cache. They're tiny.
find "$site" -type f \( -name '*.html' -o -name '*.js' -o -name '*.json' \) -print0 \
  | while IFS= read -r -d '' f; do
      rel="${f#"$site"/}"
      case "$f" in
        *.html) ct="text/html; charset=utf-8" ;;
        *.js)   ct="application/javascript; charset=utf-8" ;;
        *.json) ct="application/json; charset=utf-8" ;;
      esac
      aws s3 cp "$f" "$dest/$rel" \
        --content-type "$ct" \
        --cache-control "public, max-age=120, must-revalidate" \
        --metadata-directive REPLACE \
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
