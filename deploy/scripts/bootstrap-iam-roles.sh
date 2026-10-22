#!/usr/bin/env bash
# Personal OS — one-time bootstrap of application IAM role & instance profile.
# Run by an AWS Account Administrator prior to running OpenTofu as personal-os-deployer.
#
# Creates:
#   1. IAM Role: personal-os-staging-app-role (trusts ec2.amazonaws.com)
#   2. Managed Policy Attachments: AmazonSSMManagedInstanceCore, CloudWatchAgentServerPolicy
#   3. Inline Policy: personal-os-staging-app-policy (least-privilege runtime access)
#   4. IAM Instance Profile: personal-os-staging-app-profile
#
# Idempotent. Safe to run multiple times.
# Usage: ./deploy/scripts/bootstrap-iam-roles.sh

set -euo pipefail

REGION="us-east-1"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ROLE_NAME="personal-os-staging-app-role"
PROFILE_NAME="personal-os-staging-app-profile"
POLICY_NAME="personal-os-staging-app-policy"

echo "Account: ${ACCOUNT_ID}"
echo "Region:  ${REGION}"
echo "Role:    ${ROLE_NAME}"
echo "Profile: ${PROFILE_NAME}"

# 1. Create IAM Role with EC2 trust policy
TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}'

if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "IAM role ${ROLE_NAME} already exists."
else
  role_err="$(aws iam get-role --role-name "${ROLE_NAME}" 2>&1 || true)"
  if echo "${role_err}" | grep -q "NoSuchEntity"; then
    echo "Creating IAM role ${ROLE_NAME}..."
    aws iam create-role \
      --role-name "${ROLE_NAME}" \
      --assume-role-policy-document "${TRUST_POLICY}" \
      --tags "Key=Name,Value=${ROLE_NAME}"
  elif echo "${role_err}" | grep -q -E "AccessDenied|AccessDeniedException"; then
    echo "ERROR: Authorization failed inspecting IAM role ${ROLE_NAME}:" >&2
    echo "${role_err}" >&2
    exit 1
  else
    echo "ERROR: Unexpected failure inspecting IAM role ${ROLE_NAME}:" >&2
    echo "${role_err}" >&2
    exit 1
  fi
fi

# 2. Attach AWS-managed policies
echo "Attaching managed policies..."
aws iam attach-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

aws iam attach-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-arn "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"

# 3. Put inline application policy
echo "Configuring inline application policy ${POLICY_NAME}..."
APP_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SecretsRead",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": [
        "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:personal-os/staging/database*",
        "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:personal-os/staging/companion-jwt*",
        "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:personal-os/staging/openrouter-api-key*"
      ]
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/personal-os/staging/*"
    },
    {
      "Sid": "CloudWatchPutMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*"
    },
    {
      "Sid": "CaptureQueueAccess",
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:${REGION}:${ACCOUNT_ID}:personal-os-staging-capture"
    },
    {
      "Sid": "ArtifactWheelRead",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::personal-os-staging-artifacts/*"
    },
    {
      "Sid": "SSMSessionManager",
      "Effect": "Allow",
      "Action": [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel"
      ],
      "Resource": "*"
    }
  ]
}
EOF
)

aws iam put-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name "${POLICY_NAME}" \
  --policy-document "${APP_POLICY}"

# 4. Create IAM Instance Profile and bind role
if aws iam get-instance-profile --instance-profile-name "${PROFILE_NAME}" >/dev/null 2>&1; then
  echo "Instance profile ${PROFILE_NAME} already exists."
else
  profile_err="$(aws iam get-instance-profile --instance-profile-name "${PROFILE_NAME}" 2>&1 || true)"
  if echo "${profile_err}" | grep -q "NoSuchEntity"; then
    echo "Creating instance profile ${PROFILE_NAME}..."
    aws iam create-instance-profile \
      --instance-profile-name "${PROFILE_NAME}"
  elif echo "${profile_err}" | grep -q -E "AccessDenied|AccessDeniedException"; then
    echo "ERROR: Authorization failed inspecting instance profile ${PROFILE_NAME}:" >&2
    echo "${profile_err}" >&2
    exit 1
  else
    echo "ERROR: Unexpected failure inspecting instance profile ${PROFILE_NAME}:" >&2
    echo "${profile_err}" >&2
    exit 1
  fi
fi

# Bind role to profile if not already attached
if ! aws iam get-instance-profile --instance-profile-name "${PROFILE_NAME}" \
    --query "InstanceProfile.Roles[?RoleName=='${ROLE_NAME}'].RoleName" \
    --output text | grep -q "${ROLE_NAME}"; then
  echo "Adding role ${ROLE_NAME} to instance profile ${PROFILE_NAME}..."
  aws iam add-role-to-instance-profile \
    --instance-profile-name "${PROFILE_NAME}" \
    --role-name "${ROLE_NAME}"
else
  echo "Role ${ROLE_NAME} is already bound to profile ${PROFILE_NAME}."
fi

echo
echo "IAM Bootstrap complete."
echo "If importing into OpenTofu state, run from deploy/terraform:"
echo "  tofu import -var-file=envs/staging.tfvars aws_iam_role.app ${ROLE_NAME}"
echo "  tofu import -var-file=envs/staging.tfvars aws_iam_role_policy.app ${ROLE_NAME}:${POLICY_NAME}"
echo "  tofu import -var-file=envs/staging.tfvars aws_iam_role_policy_attachment.app_ssm_core ${ROLE_NAME}/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
echo "  tofu import -var-file=envs/staging.tfvars aws_iam_role_policy_attachment.app_cw_agent ${ROLE_NAME}/arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
echo "  tofu import -var-file=envs/staging.tfvars aws_iam_instance_profile.app ${PROFILE_NAME}"
