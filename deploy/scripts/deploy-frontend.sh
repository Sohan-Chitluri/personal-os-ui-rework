#!/usr/bin/env bash
# Personal OS — deploy the frontend bundle to S3 and invalidate CloudFront.
# Does NOT provision anything; requires prior terraform apply.
# Usage: ./deploy/scripts/deploy-frontend.sh <environment>
set -euo pipefail

ENV="${1:?usage: deploy-frontend.sh <dev|staging|production>}"
cd "$(dirname "$0")/../.."

if command -v terraform >/dev/null 2>&1; then
  IAC_CLI=terraform
elif command -v tofu >/dev/null 2>&1; then
  IAC_CLI=tofu
else
  echo "Terraform/OpenTofu is required to resolve deployment outputs." >&2
  exit 1
fi

case "$ENV" in
  dev)        BUCKET="personal-os-dev-frontend" ;;
  staging)    BUCKET="personal-os-staging-frontend" ;;
  production) BUCKET="personal-os-production-frontend" ;;
  *) echo "Unknown environment: $ENV" >&2; exit 1 ;;
esac

test -f ui/dist/client/index.html || { echo "Run deploy/scripts/build-frontend.sh first" >&2; exit 1; }
test -d ui/dist/client/assets || { echo "ui/dist/client/assets is missing; refusing deployment" >&2; exit 1; }

BUCKET="$("$IAC_CLI" -chdir=deploy/terraform output -raw frontend_bucket 2>/dev/null)" || {
  echo "Terraform output frontend_bucket is unavailable; refusing deployment." >&2
  exit 1
}
DIST_ID="$("$IAC_CLI" -chdir=deploy/terraform output -raw cloudfront_distribution_id 2>/dev/null)" || {
  echo "Terraform output cloudfront_distribution_id is unavailable; refusing deployment." >&2
  exit 1
}
if [[ -z "$BUCKET" || "$BUCKET" == "null" || -z "$DIST_ID" || "$DIST_ID" == "null" ]]; then
  echo "Required Terraform outputs are empty; refusing deployment." >&2
  exit 1
fi
if [[ "$BUCKET" != *"-${ENV}-frontend" ]]; then
  echo "Terraform frontend_bucket '$BUCKET' does not match requested environment '${ENV}'; refusing deployment." >&2
  exit 1
fi

echo "Syncing ui/dist/client → s3://${BUCKET}"
aws s3 sync ui/dist/client "s3://${BUCKET}" --delete --cache-control "public,max-age=300"

# Long-cache immutable hashed assets.
aws s3 sync ui/dist/client "s3://${BUCKET}" \
  --exclude "*.html" --exclude "service-worker.js" \
  --cache-control "public,max-age=31536000,immutable"

echo "Invalidating CloudFront distribution ${DIST_ID}"
aws cloudfront create-invalidation \
  --distribution-id "${DIST_ID}" \
  --paths "/index.html" "/assets/*" "/api/*" > /dev/null

echo "Frontend deployed."
