# Personal OS — Teardown (M1-D)
#
# Removes ALL deployed infrastructure for one environment. Run this when
# finished with a disposable dev environment. DO NOT run against production
# unless you have confirmed a recent RDS snapshot exists.
#
# Usage:
#   ./deploy/scripts/teardown.sh dev
#   ./deploy/scripts/teardown.sh production --i-have-a-snapshot
set -euo pipefail

ENV="${1:?usage: teardown.sh <dev|staging|production> [--i-have-a-snapshot]}"
CONFIRM="${2:-}"
cd "$(dirname "$0")/../.."

if [ "$ENV" = "production" ] && [ "$CONFIRM" != "--i-have-a-snapshot" ]; then
  echo "Refusing to tear down production without --i-have-a-snapshot."
  echo "First: aws rds create-db-snapshot --db-instance-identifier personal-os-production-db \\"
  echo "         --db-snapshot-identifier personal-os-production-pre-teardown"
  exit 1
fi

echo "Tearing down ${ENV}..."

# 1. Empty buckets (S3 must be empty before terraform destroys them).
for bucket in "personal-os-${ENV}-frontend" "personal-os-${ENV}-artifacts"; do
  aws s3 rm "s3://${bucket}" --recursive || true
done

# 2. Terraform destroy.
terraform -chdir=deploy/terraform init \
  -backend-config="deploy/terraform/envs/${ENV}.backend.hcl" \
  -reconfigure
terraform -chdir=deploy/terraform destroy \
  -var-file="deploy/terraform/envs/${ENV}.tfvars" \
  -auto-approve

# 3. Remove artifact bucket objects.
aws s3 rm "s3://personal-os-${ENV}-artifacts" --recursive || true

# 4. Delete the DB secret's recovery window immediately (dev and staging).
if [ "$ENV" = "dev" ] || [ "$ENV" = "staging" ]; then
  aws secretsmanager delete-secret \
    --secret-id "personal-os/${ENV}/database" \
    --force-delete-without-recovery || true
fi

echo "Teardown of ${ENV} complete."
echo "Remaining manual cleanup (if any):"
echo "  - CloudFront invalidation references (expire on their own)"
echo "  - SNS email subscription confirmations (expire on their own)"
echo "  - ACM certificates (deleted by terraform)"
