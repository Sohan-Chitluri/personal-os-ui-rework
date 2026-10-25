#!/usr/bin/env bash
# Personal OS — backend release + instance deployment (M1-D).
#
#   ./deploy/scripts/release.sh <env> <release|deploy> [version]
#
# Migrations are run once, before restarting services, with the instance role
# credentials. They run against the RDS master user (alembic env must accept
# DATABASE_URL — see the application prerequisites list in docs/DEPLOYMENT.md).
set -euo pipefail
cd "$(dirname "$0")/../.."

ENV="${1:?usage: release.sh <dev|staging|production> <release|deploy> [version]}"
COMMAND="${2:?usage: release.sh <dev|staging|production> <release|deploy> [version]}"
VERSION="${3:-$(date -u +%Y%m%d%H%M%S)}"
REGION="us-east-1"

case "$ENV" in
  dev)        ARTIFACT_BUCKET="personal-os-dev-artifacts" ;;
  staging)    ARTIFACT_BUCKET="personal-os-staging-artifacts" ;;
  production) ARTIFACT_BUCKET="personal-os-production-artifacts" ;;
  *) echo "Unknown environment: $ENV" >&2; exit 1 ;;
esac

case "$COMMAND" in
  release)
    ./deploy/scripts/build-backend.sh
    BUNDLE="dist/personal-os-backend-bundle.tar.gz"
    aws s3 cp "$BUNDLE" "s3://${ARTIFACT_BUCKET}/personal-os-${VERSION}-bundle.tar.gz"
    aws s3 cp "$BUNDLE" "s3://${ARTIFACT_BUCKET}/personal-os-latest-bundle.tar.gz"
    # Ship systemd unit files with each release so unit changes roll out
    # with code rather than with AMIs (user_data fetches them at boot).
    aws s3 cp deploy/systemd/pos-api.service       "s3://${ARTIFACT_BUCKET}/deploy/systemd/pos-api.service"
    aws s3 cp deploy/systemd/pos-worker.service       "s3://${ARTIFACT_BUCKET}/deploy/systemd/pos-worker.service"
    aws s3 cp deploy/systemd/pos-voice-worker.service "s3://${ARTIFACT_BUCKET}/deploy/systemd/pos-voice-worker.service"
    echo "Released ${VERSION} → s3://${ARTIFACT_BUCKET}/"
    ;;

  deploy)
    ./deploy/scripts/build-backend.sh
    BUNDLE="dist/personal-os-backend-bundle.tar.gz"
    aws s3 cp "$BUNDLE" "s3://${ARTIFACT_BUCKET}/personal-os-${VERSION}-bundle.tar.gz"
    aws s3 cp "$BUNDLE" "s3://${ARTIFACT_BUCKET}/personal-os-latest-bundle.tar.gz"

    # Migrations run on exactly one API instance before any service restart.
    # Worker instances only receive the new bundle and restart their worker.
    API_INSTANCE="$(aws ec2 describe-instances \
      --region "${REGION}" \
      --filters "Name=tag:Environment,Values=${ENV}" "Name=tag:Role,Values=api" "Name=instance-state-name,Values=running" \
      --query 'Reservations[].Instances[].InstanceId | [0]' --output text)"
    if [[ -z "$API_INSTANCE" || "$API_INSTANCE" == "None" ]]; then
      echo "No running API instance found for ${ENV}; refusing deployment." >&2
      exit 1
    fi

    SSM_COMMANDS="$(uv run python -c '
import json, sys
bucket, version = sys.argv[1], sys.argv[2]
commands = [
    "set -eu",
    f"aws s3 cp s3://{bucket}/personal-os-{version}-bundle.tar.gz /tmp/app-bundle.tar.gz",
    "mkdir -p /opt/personal-os && tar -xzf /tmp/app-bundle.tar.gz -C /opt/personal-os",
    "chown -R app:app /opt/personal-os",
    "if [ -f /opt/personal-os/app.whl ]; then mv /opt/personal-os/app.whl /opt/personal-os/personal_os-0.1.0-py3-none-any.whl; fi",
    "/opt/personal-os/.venv/bin/pip install --quiet --force-reinstall --no-deps /opt/personal-os/personal_os-0.1.0-py3-none-any.whl",
    "rm -f /tmp/app-bundle.tar.gz /opt/personal-os/*.whl",
    "cd /opt/personal-os && set -a && . /etc/personal-os/env && set +a && /opt/personal-os/.venv/bin/alembic -c /opt/personal-os/alembic.ini upgrade head",
]
print(json.dumps(commands))
' "$ARTIFACT_BUCKET" "$VERSION")"
    MIGRATION_PARAMS="$(uv run python -c 'import json, sys; print(json.dumps({"commands": json.loads(sys.argv[1])}))' "$SSM_COMMANDS")"
    MIGRATION_ID="$(aws ssm send-command \
      --region "${REGION}" \
      --instance-ids "${API_INSTANCE}" \
      --document-name "AWS-RunShellScript" \
      --comment "personal-os migrate ${VERSION}" \
      --timeout-seconds 600 \
      --parameters "$MIGRATION_PARAMS" \
      --query Command.CommandId --output text)"

    aws ssm wait command-executed --region "${REGION}" --command-id "${MIGRATION_ID}" --instance-id "${API_INSTANCE}"
    MIGRATION_STATUS="$(aws ssm get-command-invocation \
      --region "${REGION}" \
      --command-id "${MIGRATION_ID}" \
      --instance-id "${API_INSTANCE}" \
      --query "Status" --output text)"
    if [[ "$MIGRATION_STATUS" != "Success" ]]; then
      echo "Database migration failed on ${API_INSTANCE} with status: ${MIGRATION_STATUS}" >&2
      aws ssm get-command-invocation \
        --region "${REGION}" \
        --command-id "${MIGRATION_ID}" \
        --instance-id "${API_INSTANCE}" \
        --query "StandardErrorContent" --output text >&2
      exit 1
    fi

    for ROLE in api worker; do
      INSTANCES="$(aws ec2 describe-instances --region "${REGION}" \
        --filters "Name=tag:Environment,Values=${ENV}" "Name=tag:Role,Values=${ROLE}" "Name=instance-state-name,Values=running" \
        --query 'Reservations[].Instances[].InstanceId' --output text)"
      for INSTANCE in $INSTANCES; do
        SERVICE="pos-${ROLE}.service"
        DEPLOY_COMMANDS="$(uv run python -c '
import json, sys
bucket, version, service = sys.argv[1], sys.argv[2], sys.argv[3]
commands = [
    "set -eu",
    f"aws s3 cp s3://{bucket}/personal-os-{version}-bundle.tar.gz /tmp/app-bundle.tar.gz",
    "mkdir -p /opt/personal-os && tar -xzf /tmp/app-bundle.tar.gz -C /opt/personal-os",
    "chown -R app:app /opt/personal-os",
    "if [ -f /opt/personal-os/app.whl ]; then mv /opt/personal-os/app.whl /opt/personal-os/personal_os-0.1.0-py3-none-any.whl; fi",
    "/opt/personal-os/.venv/bin/pip install --quiet --force-reinstall --no-deps /opt/personal-os/personal_os-0.1.0-py3-none-any.whl",
    "rm -f /tmp/app-bundle.tar.gz /opt/personal-os/*.whl",
    f"systemctl restart {service}",
    f"sleep 5 && systemctl is-active {service}",
]
print(json.dumps(commands))
' "$ARTIFACT_BUCKET" "$VERSION" "$SERVICE")"
        DEPLOY_PARAMS="$(uv run python -c 'import json, sys; print(json.dumps({"commands": json.loads(sys.argv[1])}))' "$DEPLOY_COMMANDS")"
        COMMAND_ID="$(aws ssm send-command --region "${REGION}" --instance-ids "$INSTANCE" \
          --document-name AWS-RunShellScript --comment "personal-os deploy ${VERSION}" --timeout-seconds 600 \
          --parameters "$DEPLOY_PARAMS" \
          --query Command.CommandId --output text)"
        aws ssm wait command-executed --region "${REGION}" --command-id "$COMMAND_ID" --instance-id "$INSTANCE"
        DEPLOY_STATUS="$(aws ssm get-command-invocation \
          --region "${REGION}" \
          --command-id "$COMMAND_ID" \
          --instance-id "$INSTANCE" \
          --query "Status" --output text)"
        if [[ "$DEPLOY_STATUS" != "Success" ]]; then
          echo "Service deployment failed on ${INSTANCE} (${ROLE}) with status: ${DEPLOY_STATUS}" >&2
          aws ssm get-command-invocation \
            --region "${REGION}" \
            --command-id "$COMMAND_ID" \
            --instance-id "$INSTANCE" \
            --query "StandardErrorContent" --output text >&2
          exit 1
        fi
      done
    done
    echo "Deploy complete."
    ;;

  *)
    echo "Unknown command: $COMMAND (use release|deploy)" >&2
    exit 1
    ;;
esac
