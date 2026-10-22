#!/usr/bin/env bash
# Personal OS — one-time bootstrap of Terraform remote state (M1-D).
# Creates the S3 state bucket and DynamoDB lock table. Idempotent.
# Usage: ./deploy/scripts/bootstrap-remote-state.sh
set -euo pipefail

REGION="us-east-1"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="personal-os-tfstate-${ACCOUNT_ID}"
TABLE="personal-os-tfstate-lock"

echo "Account: ${ACCOUNT_ID}"
echo "State bucket: ${BUCKET}"

if bucket_err="$(aws s3api head-bucket --bucket "${BUCKET}" 2>&1)"; then
  echo "State bucket ${BUCKET} already exists; verifying security configuration..."
  aws s3api put-bucket-versioning --bucket "${BUCKET}" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "${BUCKET}" \
    --server-side-encryption-configuration '{"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}'
  aws s3api put-public-access-block --bucket "${BUCKET}" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  echo "State bucket ${BUCKET} verified (versioning, AES256 encryption, public access block)."
elif echo "${bucket_err}" | grep -q -E "404|NoSuchBucket|Not Found"; then
  echo "State bucket ${BUCKET} does not exist. Creating in ${REGION}..."
  aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}"
  aws s3api put-bucket-versioning --bucket "${BUCKET}" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "${BUCKET}" \
    --server-side-encryption-configuration '{"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}'
  aws s3api put-public-access-block --bucket "${BUCKET}" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  echo "Created and secured ${BUCKET} (versioning, explicit AES256 encryption, public access block)."
elif echo "${bucket_err}" | grep -q -E "403|Forbidden|AccessDenied"; then
  echo "ERROR: Authorization failed inspecting bucket ${BUCKET}:" >&2
  echo "${bucket_err}" >&2
  exit 1
else
  echo "ERROR: Unexpected failure inspecting bucket ${BUCKET}:" >&2
  echo "${bucket_err}" >&2
  exit 1
fi

if aws dynamodb describe-table --table-name "${TABLE}" --region "${REGION}" >/dev/null 2>&1; then
  echo "DynamoDB lock table ${TABLE} already exists."
else
  table_err="$(aws dynamodb describe-table --table-name "${TABLE}" --region "${REGION}" 2>&1 || true)"
  if echo "${table_err}" | grep -q "ResourceNotFoundException"; then
    echo "DynamoDB lock table ${TABLE} does not exist. Creating..."
    aws dynamodb create-table \
      --table-name "${TABLE}" \
      --attribute-definitions AttributeName=LockID,AttributeType=S \
      --key-schema AttributeName=LockID,KeyType=HASH \
      --billing-mode PAY_PER_REQUEST \
      --region "${REGION}"
    echo "Waiting for DynamoDB lock table ${TABLE} to become active..."
    aws dynamodb wait table-exists --table-name "${TABLE}" --region "${REGION}"
    echo "Created ${TABLE}"
  elif echo "${table_err}" | grep -q -E "AccessDenied|AccessDeniedException"; then
    echo "ERROR: Authorization failed inspecting DynamoDB table ${TABLE}:" >&2
    echo "${table_err}" >&2
    exit 1
  else
    echo "ERROR: Unexpected failure inspecting DynamoDB table ${TABLE}:" >&2
    echo "${table_err}" >&2
    exit 1
  fi
fi

echo "Remote state backend ready."
echo "Backend configurations in deploy/terraform/envs/*.backend.hcl reference bucket = ${BUCKET}"
echo "For default CloudFront access, no domain or route53_zone_id is required."
echo "(Optional) Set root_domain and route53_zone_id in *.tfvars only if enabling a custom domain."
