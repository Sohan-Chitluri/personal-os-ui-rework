# Personal OS — IAM Deployment Policies & Security Architecture

This directory defines the least-privilege IAM policies for the deployment group `PersonalOSDeployment` and user `personal-os-deployer` targeting the Personal OS staging infrastructure on AWS (Account: `414329795881`).

---

## 1. Executive Summary & Policy Architecture

Deploying and operating Personal OS requires three distinct privilege tiers:
1. **Tier 1: Administrator-Assisted Bootstrap** — One-time setup of remote state (S3 bucket, DynamoDB lock table) and application IAM role/instance profile by an AWS Account Administrator.
2. **Tier 2: Infrastructure Provisioning** — OpenTofu initialization, planning, and deployment of the staging infrastructure using modular policies attached to `PersonalOSDeployment`.
3. **Tier 3: Routine Code & Asset Deployment** — Day-to-day release scripts (`release.sh`, `deploy-frontend.sh`) with zero IAM mutation rights and zero destruction permissions.

### Policy Files Overview

| Policy File | Scope & Purpose | Formatted Size | Compact Size | Console Status (<5,120) | API Status (<6,144) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| [`personal-os-admin-bootstrap-policy.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-admin-bootstrap-policy.json) | **Administrator One-Time Setup:** Remote state S3 bucket, DynamoDB lock table, and application IAM role/instance profile pre-creation. | **1,456 chars** | **1,055 chars** | **PASS** | **PASS** |
| [`personal-os-bootstrap-policy.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-bootstrap-policy.json) | **Administrator One-Time Setup (Mirror):** Identical bootstrap policy for remote state and application role. | **1,456 chars** | **1,055 chars** | **PASS** | **PASS** |
| [`personal-os-provisioning-policy-part1.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-provisioning-policy-part1.json) | **OpenTofu Staging (Part 1):** S3 application buckets, CloudFront distribution/OAC/functions, ACM TLS, Secrets Manager, SQS queues, CloudWatch logs/alarms, SNS alerts, SSM parameter, and remote state S3/DynamoDB. (Route 53 excluded). | 6,730 chars | **4,949 chars** | **PASS (compact)** | **PASS** |
| [`personal-os-provisioning-policy-part2.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-provisioning-policy-part2.json) | **OpenTofu Staging (Part 2):** EC2 discovery, VPC networking (VPC, subnets, route tables, IGW, NAT GW, security groups), launch templates, Auto Scaling Groups, and ALB load balancing (ALB, target groups, listeners, rules). | **3,990 chars** | **3,007 chars** | **PASS** (< 5,000) | **PASS** |
| [`personal-os-provisioning-policy-part3.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-provisioning-policy-part3.json) | **OpenTofu Staging (Part 3):** RDS PostgreSQL instances/parameter groups/subnets, Cognito user pools/clients/domains, read-only IAM state refresh, and hardened `iam:PassRole`. | **2,928 chars** | **2,139 chars** | **PASS** (< 5,000) | **PASS** |
| [`personal-os-routine-deployment-policy.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-routine-deployment-policy.json) | **Routine Day-to-Day Releases:** Upload backend bundles to S3 artifacts, sync frontend assets to S3, invalidate CloudFront cache, discover EC2 instances via tags, and execute migrations/restarts via SSM Run Command. Zero IAM modification rights. | **2,317 chars** | **1,608 chars** | **PASS** | **PASS** |
| [`personal-os-provisioning-policy.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-provisioning-policy.json) | **Unified Provisioning Reference:** Complete concatenated policy of Part 1, Part 2, and Part 3. | 13,546 chars | 10,019 chars | Exceeds | Reference only |

> [!NOTE]
> ### AWS IAM Console vs API Size Limits
> - **AWS IAM API / Backend Limit**: 6,144 characters (excluding whitespace).
> - **AWS IAM Console Policy Editor Input Limit**: 5,120 characters (including indentation and newlines).
>
> To ensure direct, error-free copy-pasting into the AWS IAM Console JSON editor:
> - **Part 2** and **Part 3** are split at clean statement boundaries, each safely under **5,000 characters** in fully indented formatted form (Part 2: 3,990 chars, Part 3: 2,928 chars).
> - **Part 1** has a compact length of 4,949 characters (< 5,120). If using the AWS Console editor, paste it in compact form or standard form without extraneous whitespace.

---

## 2. Dual Deployment Modes: Default CloudFront vs. Custom Domain

The Personal OS staging infrastructure supports two distinct deployment modes without requiring architectural redesign or breaking configuration changes:

```mermaid
flowchart TD
    subgraph Mode 1: Default CloudFront Mode (Active Staging Baseline)
        CF1["CloudFront Distribution\n(*.cloudfront.net)"]
        S3_1["S3 Frontend Bucket\n(OAC)"]
        ALB1["Application Load Balancer\n(HTTP Port 80, Prefix List Restricted)"]
        APP1["EC2 App Instances\n(Uvicorn API Port 8000)"]

        CF1 -- "Viewer HTTPS\nDefault Cert" --> User1["End User"]
        CF1 -- "/api/* (HTTP Port 80)\nAWS Backbone" --> ALB1
        CF1 -- "/* (S3 OAC)" --> S3_1
        ALB1 --> APP1
    end

    subgraph Mode 2: Custom Domain Mode (Optional Turnkey Upgrade)
        R53["Route 53 DNS\n(runs-on.dev)"]
        ACM["ACM Certificates\n(us-east-1 for CF, regional for ALB)"]
        CF2["CloudFront Distribution\n(app.runs-on.dev with SNI)"]
        ALB2["Application Load Balancer\n(HTTPS Port 443 with ACM)"]

        R53 --> CF2
        R53 --> ALB2
        ACM --> CF2
        ACM --> ALB2
    end
```

### Mode 1: Default CloudFront Domain (Staging Baseline)
When `root_domain` and `route53_zone_id` are empty strings in `staging.tfvars` (the default):
1. **Zero Route 53 Dependencies**: No Route 53 records or hosted zones are created or queried. Deployer policies contain **zero Route 53 permissions**.
2. **Zero ACM Certificates**: No ACM certificates are requested or validated. Deployment does not block on DNS validation.
3. **Viewer HTTPS**: End users connect via the CloudFront distribution's default HTTPS endpoint (`https://<distribution-id>.cloudfront.net`) using CloudFront's default wildcard SSL/TLS certificate.
4. **ALB Ingress**:
   - CloudFront routes `/api/*` to the ALB's default AWS DNS name (`aws_lb.main.dns_name`) over HTTP (port 80).
   - Traffic travels over AWS's internal network backbone.
   - The ALB security group restricts ingress on port 80 and port 443 strictly to CloudFront origin servers via AWS Managed Prefix List `com.amazonaws.global.cloudfront.origin-facing`.
   - The ALB listener rule requires the shared secret header `X-Origin-Verify` generated at plan time before forwarding traffic to target instances. Direct internet requests to the ALB return 404.
5. **Cognito Redirection**: Cognito user pool client callback and logout URLs dynamically resolve to `https://${aws_cloudfront_distribution.frontend.domain_name}/auth/callback` and `https://${aws_cloudfront_distribution.frontend.domain_name}/`.

### Mode 2: Custom Domain Integration (Turnkey Upgrade)
To bind a custom domain (e.g. `runs-on.dev` or `pos-deku.com`) once confirmed DNS control is established:
1. Update `deploy/terraform/envs/staging.tfvars`:
   ```hcl
   root_domain     = "runs-on.dev"
   route53_zone_id = "Z0123456789ABCDEF"
   ```
2. When `root_domain` and `route53_zone_id` are non-empty:
   - `local.custom_domain_enabled` evaluates to `true`.
   - `dns.tf` provisions ACM certificates (`aws_acm_certificate.api` and `aws_acm_certificate.frontend`) and automated DNS validation records in Route 53.
   - CloudFront enables custom domain aliases (`app.runs-on.dev`) and attaches the validated ACM certificate via SNI.
   - The ALB listener switches to port 443 with the regional ACM certificate.
   - CloudFront forwards `/api/*` to `api.runs-on.dev` via HTTPS on port 443.
   - Cognito callback URLs automatically point to `https://app.runs-on.dev/auth/callback`.

---

## 3. Prevention of Privilege Escalation & IAM Security Model

Allowing a deployment identity to create or modify IAM roles introduces severe privilege-escalation risks:
- Calling `iam:PutRolePolicy` to attach an unrestricted inline policy (e.g. `Action: "*"` on `Resource: "*"`), followed by querying instance metadata from EC2 to achieve full account takeover.
- Calling `iam:AttachRolePolicy` to attach `AdministratorAccess` to an application role.
- Calling `iam:PassRole` to pass an existing account administrator role to an EC2 instance.
- Modifying role trust relationships (`iam:UpdateAssumeRolePolicy`) to allow the deployer or an external entity to assume the role directly.
- Creating a rogue instance profile or swapping roles in an existing profile.

### Zero-Write IAM Architecture (Administrator Pre-Creation)

To eliminate all IAM privilege-escalation vectors, **all IAM write and lifecycle permissions have been completely removed** from the deployer policies (`part1`, `part2`, `part3`, and `routine`):
- `iam:CreateRole` — **REMOVED**
- `iam:DeleteRole` — **REMOVED**
- `iam:UpdateRole` — **REMOVED**
- `iam:PutRolePolicy` — **REMOVED**
- `iam:DeleteRolePolicy` — **REMOVED**
- `iam:AttachRolePolicy` — **REMOVED**
- `iam:DetachRolePolicy` — **REMOVED**
- `iam:CreateInstanceProfile` — **REMOVED**
- `iam:DeleteInstanceProfile` — **REMOVED**
- `iam:AddRoleToInstanceProfile` — **REMOVED**
- `iam:RemoveRoleFromInstanceProfile` — **REMOVED**
- `iam:UpdateAssumeRolePolicy` — **REMOVED**

### Remaining IAM Permissions for the Deployer (Part 3)

The deployer holds only two narrow, read-and-pass IAM capabilities in [`personal-os-provisioning-policy-part3.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-provisioning-policy-part3.json):

1. **Read-Only IAM for State Refresh**:
   ```json
   {
     "Sid": "IAMReadOnlyForStateRefresh",
     "Effect": "Allow",
     "Action": [
       "iam:GetRole",
       "iam:GetRolePolicy",
       "iam:ListRolePolicies",
       "iam:ListAttachedRolePolicies",
       "iam:GetInstanceProfile"
     ],
     "Resource": [
       "arn:aws:iam::414329795881:role/personal-os-staging-*",
       "arn:aws:iam::414329795881:instance-profile/personal-os-staging-*"
     ]
   }
   ```
   *Purpose:* Allows OpenTofu to refresh remote state for pre-created IAM resources and verify their existence during `tofu plan` and `tofu apply`. Cannot create, edit, or delete any role, policy, or profile.

2. **Strictly Scoped `iam:PassRole` with Service Boundary**:
   ```json
   {
     "Sid": "IAMPassRoleControl",
     "Effect": "Allow",
     "Action": "iam:PassRole",
     "Resource": "arn:aws:iam::414329795881:role/personal-os-staging-app-role",
     "Condition": {
       "StringEquals": {
         "iam:PassedToService": [
           "ec2.amazonaws.com",
           "rds.amazonaws.com"
         ]
       }
     }
   }
   ```
   *Mitigation:* The deployer can **only** pass the pre-created application role (`personal-os-staging-app-role`), and **only** to `ec2.amazonaws.com` (for launch templates / ASG) and `rds.amazonaws.com`. The deployer cannot pass arbitrary roles or pass roles to unintended services (e.g. Lambda, ECS).

---

## 4. Administrator Pre-Creation & OpenTofu Workflow

Because `compute.tf` declares `aws_iam_role.app`, `aws_iam_role_policy.app`, `aws_iam_role_policy_attachment.*`, and `aws_iam_instance_profile.app`, OpenTofu expects these resources in its state. Running `tofu apply` without pre-creating or importing them would fail because the deployer lacks `iam:CreateRole`.

### Administrator Bootstrap (One-Time)

The AWS Account Administrator executes the bootstrap scripts before handing deployment credentials to `personal-os-deployer`:

```bash
# Run with Administrator credentials:
./deploy/scripts/bootstrap-remote-state.sh
./deploy/scripts/bootstrap-iam-roles.sh
```

### Safe State Import (or Targeted Admin Apply)

To ensure OpenTofu tracks the pre-created resources in remote state without drift:

**Option 1: OpenTofu State Import (Recommended)**
```bash
cd deploy/terraform
tofu init -backend-config=envs/staging.backend.hcl

tofu import -var-file=envs/staging.tfvars aws_iam_role.app personal-os-staging-app-role
tofu import -var-file=envs/staging.tfvars aws_iam_role_policy.app personal-os-staging-app-role:personal-os-staging-app-policy
tofu import -var-file=envs/staging.tfvars aws_iam_role_policy_attachment.app_ssm_core personal-os-staging-app-role/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
tofu import -var-file=envs/staging.tfvars aws_iam_role_policy_attachment.app_cw_agent personal-os-staging-app-role/arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy
tofu import -var-file=envs/staging.tfvars aws_iam_instance_profile.app personal-os-staging-app-profile
```

**Option 2: Administrator Targeted Apply**
Alternatively, the Administrator can run an initial targeted apply that provisions the IAM resources directly into the remote state:
```bash
cd deploy/terraform
tofu init -backend-config=envs/staging.backend.hcl
tofu apply \
  -target=aws_iam_role.app \
  -target=aws_iam_role_policy.app \
  -target=aws_iam_role_policy_attachment.app_ssm_core \
  -target=aws_iam_role_policy_attachment.app_cw_agent \
  -target=aws_iam_instance_profile.app \
  -var-file=envs/staging.tfvars
```

Once imported or applied into state, `personal-os-deployer` running `tofu plan` or `tofu apply` will see 0 IAM changes needed. The deployer's read-only permissions (`iam:GetRole`, `iam:GetInstanceProfile`, etc.) will succeed, and the deployer will never attempt any IAM modification.

---

## 5. Real Configuration ARN Mapping (Account: `414329795881`)

Every ARN in the policies is verified against the actual configuration files:

| Config Variable / Resource | Value in Repository | Canonical Resource ARN |
| :--- | :--- | :--- |
| **Primary Region** | `us-east-1` ([`variables.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/variables.tf#L8)) | Regional services: `us-east-1` |
| **CloudFront ACM Region** | `us-east-1` ([`versions.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/versions.tf#L45)) | Global CloudFront ACM cert: strictly `us-east-1` |
| **Remote State Bucket** | `personal-os-tfstate-414329795881` ([`staging.backend.hcl`](file:///home/peskybird/Projects/personal-os/deploy/terraform/envs/staging.backend.hcl#L1)) | `arn:aws:s3:::personal-os-tfstate-414329795881` |
| **Remote State Objects** | `staging/terraform.tfstate` ([`staging.backend.hcl`](file:///home/peskybird/Projects/personal-os/deploy/terraform/envs/staging.backend.hcl#L2)) | `arn:aws:s3:::personal-os-tfstate-414329795881/staging/*` |
| **State Lock Table** | `personal-os-tfstate-lock` ([`staging.backend.hcl`](file:///home/peskybird/Projects/personal-os/deploy/terraform/envs/staging.backend.hcl#L4)) | `arn:aws:dynamodb:us-east-1:414329795881:table/personal-os-tfstate-lock` |
| **Artifacts Bucket** | `personal-os-staging-artifacts` ([`artifacts.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/artifacts.tf#L7)) | `arn:aws:s3:::personal-os-staging-artifacts` & `.../*` |
| **Frontend Bucket** | `personal-os-staging-frontend` ([`frontend.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/frontend.tf#L51)) | `arn:aws:s3:::personal-os-staging-frontend` & `.../*` |
| **Database Secret** | `personal-os/staging/database` ([`rds.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/rds.tf#L108)) | `arn:aws:secretsmanager:us-east-1:414329795881:secret:personal-os/staging/database*` |
| **Companion JWT Secret** | `personal-os/staging/companion-jwt` ([`security.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/security.tf#L11)) | `arn:aws:secretsmanager:us-east-1:414329795881:secret:personal-os/staging/companion-jwt*` |
| **OpenRouter API Key** | `personal-os/staging/openrouter-api-key` ([`security.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/security.tf#L21)) | `arn:aws:secretsmanager:us-east-1:414329795881:secret:personal-os/staging/openrouter-api-key*` |
| **SQS Capture Queue** | `personal-os-staging-capture` ([`queue.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/queue.tf#L15)) | `arn:aws:sqs:us-east-1:414329795881:personal-os-staging-capture` |
| **SQS DLQ** | `personal-os-staging-capture-dlq` ([`queue.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/queue.tf#L4)) | `arn:aws:sqs:us-east-1:414329795881:personal-os-staging-capture-dlq` |
| **RDS Instance** | `personal-os-staging-db` ([`rds.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/rds.tf#L32)) | `arn:aws:rds:us-east-1:414329795881:db:personal-os-staging-db` |
| **RDS Subnet Group** | `personal-os-staging-db-subnets` ([`rds.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/rds.tf#L7)) | `arn:aws:rds:us-east-1:414329795881:subgrp:personal-os-staging-db-subnets` |
| **RDS Parameter Group**| `personal-os-staging-pg16` ([`rds.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/rds.tf#L15)) | `arn:aws:rds:us-east-1:414329795881:pg:personal-os-staging-pg16` |
| **CloudWatch Log Groups**| `/personal-os/staging/api`, `/worker` ([`monitoring.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/monitoring.tf#L21)) | `arn:aws:logs:us-east-1:414329795881:log-group:/personal-os/staging/*` |
| **SNS Alerts Topic** | `personal-os-staging-alerts` ([`monitoring.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/monitoring.tf#L6)) | `arn:aws:sns:us-east-1:414329795881:personal-os-staging-alerts` |
| **SSM Parameter** | `/personal-os/staging/cloudwatch-agent-config` ([`monitoring.tf`](file:///home/peskybird/Projects/personal-os/deploy/terraform/monitoring.tf#L38)) | `arn:aws:ssm:us-east-1:414329795881:parameter/personal-os/staging/cloudwatch-agent-config` |
| **Route 53 Hosted Zone**| Optional / Deferred in staging ([`staging.tfvars`](file:///home/peskybird/Projects/personal-os/deploy/terraform/envs/staging.tfvars#L5)) | `arn:aws:route53:::hostedzone/<ROUTE53_ZONE_ID>` (Optional) |

---

## 6. Reassessment of Wildcard Resources (`Resource: "*"`)

AWS IAM authorization requirements mandate `"Resource": "*"` for specific operations because resource-level permissions are not supported by the underlying AWS APIs:

| Service / Category | Wildcard Actions | Authorization Rationale |
| :--- | :--- | :--- |
| **EC2 Discovery** | `ec2:DescribeAvailabilityZones`, `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeRouteTables`, `ec2:DescribeInternetGateways`, `ec2:DescribeNatGateways`, `ec2:DescribeAddresses`, `ec2:DescribeSecurityGroups`, `ec2:DescribeVpcEndpoints`, `ec2:DescribeImages`, `ec2:DescribeLaunchTemplates`, `ec2:DescribeLaunchTemplateVersions`, `ec2:DescribeInstances`, `ec2:DescribeTags`, `ec2:DescribeManagedPrefixLists`, `ec2:GetManagedPrefixListEntries` | AWS EC2 describe APIs do not support resource-level permissions. Scoping describe actions to ARNs causes immediate `AccessDenied` errors. |
| **ELBv2 Discovery** | `elasticloadbalancing:DescribeLoadBalancers`, `elasticloadbalancing:DescribeTargetGroups`, `elasticloadbalancing:DescribeListeners`, `elasticloadbalancing:DescribeRules`, `elasticloadbalancing:DescribeTags` | ELBv2 describe actions operate across the region and do not support resource-level ARN constraints. |
| **Auto Scaling Discovery** | `autoscaling:DescribeAutoScalingGroups`, `autoscaling:DescribeInstanceRefreshes`, `autoscaling:DescribeScalingActivities` | Auto Scaling queries operate at the account level. |
| **CloudFront Managed Policies**| `cloudfront:GetCachePolicy`, `cloudfront:GetOriginRequestPolicy`, `cloudfront:GetResponseHeadersPolicy`, `cloudfront:ListDistributions` | AWS-managed caching and request policies (`Managed-CachingOptimized`) are global AWS resources outside customer account ARNs. |
| **RDS Discovery** | `rds:DescribeDBSubnetGroups`, `rds:DescribeDBParameterGroups`, `rds:DescribeDBParameters` | Pre-creation and parameter evaluation queries require account-level access. |
| **SQS Discovery** | `sqs:ListQueues` | Queue lookup by name across the region requires account-level discovery. |
| **Cognito Pool Discovery** | `cognito-idp:ListUserPools` | User pool discovery operates across the region. |
| **CloudWatch Alarms** | `cloudwatch:DescribeAlarms` | Alarm discovery does not accept individual ARN scoping. |
| **SSM Parameters** | `ssm:DescribeParameters` | Parameter inventory queries operate across the region. |

*All other actions (including S3 object operations, RDS mutations, Secrets Manager actions, SQS queue mutations, and IAM PassRole) are strictly resource-scoped.*

---

## 7. Policy Validation & Testing

### Automated Test Suites

Two automated regression test suites are provided:

1. **Deployment Modes Test Suite** ([`deploy/scripts/test_deployment_modes.py`](file:///home/peskybird/Projects/personal-os/deploy/scripts/test_deployment_modes.py)):
   - Verifies `tofu validate` succeeds in both Mode 1 (default CloudFront) and Mode 2 (custom domain).
   - Enforces HCL resource conditioning across `dns.tf`, `compute.tf`, `security.tf`, `frontend.tf`, `auth.tf`, and `variables.tf`.
   - Asserts that deployer policies require zero Route 53 permissions in default mode.

2. **IAM Policy Regression Suite** ([`deploy/scripts/test_iam_policies.py`](file:///home/peskybird/Projects/personal-os/deploy/scripts/test_iam_policies.py)):
   - Verifies valid IAM JSON schema on all policies.
   - Enforces the 6,144-character size limit on Part 1, Part 2, Part 3, Routine, and Bootstrap policies.
   - Enforces that split policies (Part 2 and Part 3) are each under 5,000 formatted characters.
   - Asserts strict absence of dangerous IAM actions from deployer policies.
   - Verifies Part 1, Part 2, and Routine policies contain 0 IAM actions.
   - Asserts zero Route 53 actions in deployer policies.
   - Verifies Part 3 contains only read-only IAM actions and hardened `iam:PassRole`.

Run locally:
```bash
uv run python deploy/scripts/test_deployment_modes.py
uv run python deploy/scripts/test_iam_policies.py
# or via pytest:
uv run pytest deploy/scripts/test_deployment_modes.py deploy/scripts/test_iam_policies.py
```

### AWS IAM Access Analyzer Validation

AWS provides `aws accessanalyzer validate-policy` to validate IAM policies against security best practices and check for grammar errors.
> [!NOTE]
> `aws accessanalyzer validate-policy` requires active, authenticated AWS API credentials. It cannot run offline.

Once credentials are configured, run validation against all policies:
```bash
# Validate Admin Bootstrap policy
aws accessanalyzer validate-policy \
  --region us-east-1 \
  --policy-document file://deploy/iam/personal-os-admin-bootstrap-policy.json \
  --policy-type IDENTITY_POLICY

# Validate Provisioning Part 1 policy
aws accessanalyzer validate-policy \
  --region us-east-1 \
  --policy-document file://deploy/iam/personal-os-provisioning-policy-part1.json \
  --policy-type IDENTITY_POLICY

# Validate Provisioning Part 2 policy
aws accessanalyzer validate-policy \
  --region us-east-1 \
  --policy-document file://deploy/iam/personal-os-provisioning-policy-part2.json \
  --policy-type IDENTITY_POLICY

# Validate Provisioning Part 3 policy
aws accessanalyzer validate-policy \
  --region us-east-1 \
  --policy-document file://deploy/iam/personal-os-provisioning-policy-part3.json \
  --policy-type IDENTITY_POLICY

# Validate Routine Deployment policy
aws accessanalyzer validate-policy \
  --region us-east-1 \
  --policy-document file://deploy/iam/personal-os-routine-deployment-policy.json \
  --policy-type IDENTITY_POLICY
```

---

## 8. Safe Console Instructions for Attaching to `PersonalOSDeployment`

### Step 1: Open the IAM Console
1. Log in to the AWS Management Console as an Administrator.
2. Navigate to **IAM** -> **User groups** -> click on group **`PersonalOSDeployment`**.

### Step 2: Create Customer Managed Policies
1. In the left navigation bar, click **Policies** -> **Create policy**.
2. Select the **JSON** tab.
3. Paste the contents of [`personal-os-provisioning-policy-part1.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-provisioning-policy-part1.json). *(Paste in compact form if using the console editor, as its compact length is 4,949 characters)*.
4. Name the policy: `PersonalOS-Staging-Provisioning-Part1`.
5. Repeat for Part 2: Paste [`personal-os-provisioning-policy-part2.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-provisioning-policy-part2.json) (3,990 formatted chars, safely under the 5,120 limit) and name it `PersonalOS-Staging-Provisioning-Part2`.
6. Repeat for Part 3: Paste [`personal-os-provisioning-policy-part3.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-provisioning-policy-part3.json) (2,928 formatted chars, safely under the 5,120 limit) and name it `PersonalOS-Staging-Provisioning-Part3`.
7. (Optional for Routine Deployment): Paste [`personal-os-routine-deployment-policy.json`](file:///home/peskybird/Projects/personal-os/deploy/iam/personal-os-routine-deployment-policy.json) and name it `PersonalOS-Staging-Routine-Deployment`.

### Step 3: Attach Policies to Group `PersonalOSDeployment`
1. Navigate back to **User groups** -> **`PersonalOSDeployment`**.
2. In the **Permissions** tab, click **Add permissions** -> **Attach policies**.
3. Select `PersonalOS-Staging-Provisioning-Part1`, `PersonalOS-Staging-Provisioning-Part2`, and `PersonalOS-Staging-Provisioning-Part3`.
4. Click **Attach policies**.

---

## 9. Lifecycle Reduction of Permissions

1. **Phase 1 — Remote State & IAM Bootstrap (Admin)**:
   - Run once by an Account Administrator using [`bootstrap-remote-state.sh`](file:///home/peskybird/Projects/personal-os/deploy/scripts/bootstrap-remote-state.sh) and [`bootstrap-iam-roles.sh`](file:///home/peskybird/Projects/personal-os/deploy/scripts/bootstrap-iam-roles.sh).
2. **Phase 2 — Initial OpenTofu Provisioning**:
   - `PersonalOSDeployment` group holds `PersonalOS-Staging-Provisioning-Part1`, `Part2`, and `Part3`.
   - Run `tofu init` and `tofu apply`.
3. **Phase 3 — Routine Deployments (Code & Frontend Releases)**:
   - Detach the provisioning policies from `PersonalOSDeployment`.
   - Attach `PersonalOS-Staging-Routine-Deployment`.
   - The user `personal-os-deployer` can deploy backend packages, execute database migrations, restart systemd units, and publish frontend assets without possessing any rights to alter IAM or delete cloud infrastructure.
   - *Note: `personal-os-routine-deployment-policy.json` is strictly for release scripts and is NOT sufficient to run `tofu apply`.*
