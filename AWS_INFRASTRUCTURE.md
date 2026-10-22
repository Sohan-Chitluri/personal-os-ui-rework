# AWS Infrastructure — Deku / Pos-Deku Backend

**Cross-references**: [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md), [BACKEND_IMPLEMENTATION_PLAN.md](BACKEND_IMPLEMENTATION_PLAN.md) (Workstream F)

---

## 1. Deployment Topology — Hybrid Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                    AWS ACCOUNT                                       │
│  ┌──────────────┐    ┌──────────────────────────────────────────────────────────┐  │
│  │   Route 53   │───▶│              CloudFront Distribution                      │  │
│  │  (optional)  │    │  • Alternate Domain Name (ACM cert)                        │  │
│  └──────────────┘    │  • Origin: S3 (frontend) + ALB (API + WebSocket)          │  │
│                      │  • Cache behaviors: /api/* → ALB, /ws/* → ALB, /* → S3    │  │
│                      └──────────────────────────────────────────────────────────┘  │
│                                       │                                             │
│                      ┌────────────────┴────────────────┐                           │
│                      │                                 │                           │
│              ┌───────▼────────┐               ┌────────▼────────┐                 │
│              │   S3 Bucket    │               │  ALB (HTTPS)    │                 │
│              │  (Frontend)    │               │  → Target Group │                 │
│              │  • Static site │               │  • EC2 Instances│                 │
│              │  • Versioned   │               │  • Health check │                 │
│              └────────────────┘               └────────┬────────┘                 │
│                                                       │                            │
│                      ┌────────────────┬───────────────┘                            │
│                      │                │                                            │
│              ┌───────▼───────┐ ┌──────▼───────┐                                 │
│              │  EC2 Instance │ │  EC2 Instance│  (ASG: min=1, max=3)             │
│              │  (API +       │ │  (Worker +   │                                 │
│              │   Session     │ │   Workspace  │                                 │
│              │   Broker)     │ │   Worker)    │                                 │
│              └───────┬───────┘ └──────┬───────┘                                 │
│                      │                │                                          │
│         ┌────────────┼────────────┐   │                                          │
│         │            │            │   │                                          │
│  ┌──────▼─────┐ ┌────▼────┐ ┌──────▼──────┐ ┌──────────────────┐               │
│  │ RDS        │ │ SQS     │ │ S3          │ │ Cloud Workspace  │               │
│  │ PostgreSQL │ │ (FIFO)  │ │ (Files +    │ │ EC2 Instances    │               │
│  │ Multi-AZ   │ │ + DLQ   │ │  Templates) │ │ (On-Demand)      │               │
│  └────────────┘ └─────────┘ └─────────────┘ └──────────────────┘               │
│         │            │            │                                           │
│  ┌──────▼─────┐ ┌────▼────┐ ┌────▼──────┐                                    │
│  │ Secrets    │ │CloudWatch│ │ Cognito   │                                    │
│  │ Manager    │ │Logs/Metr.│ │ User Pool │                                    │
│  └────────────┘ └─────────┘ └───────────┘                                    │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                         LOCAL MACHINE                                     │  │
│  │  ┌────────────────────────────────────────────────────────────────────┐  │  │
│  │  │                    POS-COMPANION DAEMON                            │  │  │
│  │  │  • Outbound WebSocket → Session Broker (EC2)                      │  │  │
│  │  │  • Device registration + heartbeat                                 │  │  │
│  │  │  • Local project discovery                                         │  │  │
│  │  │  • Authenticated cloud protocol client (online only)                │  │  │
│  │  │  • Approved RPC: terminal, filesystem, process, agent              │  │  │
│  │  └────────────────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Service Responsibilities (Extended)

### 2.1 EC2 (Compute) — Three Roles on ASG

| Role | Instance Type | Services | Ports |
|------|---------------|----------|-------|
| **API + Session Broker** | t3.medium (dev), t3.large (prod) | FastAPI (8000), WebSocket Session Broker (8001) | 8000, 8001 |
| **Capture Worker** | t3.medium | `capture_worker` (SQS consumer) | — |
| **Workspace Worker** | t3.medium | `workspace_worker` (provisions cloud workspaces) | — |

**ASG**: Min=1, Max=3, Desired=1 (API) + 1 (Capture Worker) + 0-1 (Workspace Worker, scaled by queue depth)

**User Data** (all roles):
```bash
#!/bin/bash
# Cloud-init for Amazon Linux 2023
dnf update -y
dnf install -y python3.11 python3.11-pip git docker
pip3.11 install uv

# Clone and deploy
cd /opt
git clone https://github.com/your-org/pos-deku.git
cd pos-deku
uv sync --frozen

# Install systemd services based on role
ROLE="${ROLE:-api}"  # api, capture_worker, workspace_worker
cp deploy/systemd/pos-${ROLE}.service /etc/systemd/system/
systemctl enable pos-${ROLE}
systemctl start pos-${ROLE}
```

**Systemd Services**:

```ini
# pos-api.service (API + Session Broker)
[Unit]
Description=Pos-Deku FastAPI + Session Broker
After=network.target
[Service]
Type=exec
User=posdeku
WorkingDirectory=/opt/pos-deku
EnvironmentFile=/opt/pos-deku/.env
ExecStart=/opt/pos-deku/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=on-failure
RestartSec=5

# pos-capture-worker.service
[Unit]
Description=Pos-Deku Capture Worker
After=network.target
[Service]
Type=exec
User=posdeku
WorkingDirectory=/opt/pos-deku
EnvironmentFile=/opt/pos-deku/.env
ExecStart=/opt/pos-deku/.venv/bin/python -m workers.capture_worker
Restart=on-failure
RestartSec=5

# pos-workspace-worker.service
[Unit]
Description=Pos-Deku Workspace Worker
After=network.target
[Service]
Type=exec
User=posdeku
WorkingDirectory=/opt/pos-deku
EnvironmentFile=/opt/pos-deku/.env
ExecStart=/opt/pos-deku/.venv/bin/python -m workers.workspace_worker
Restart=on-failure
RestartSec=5
```

### 2.2 RDS PostgreSQL (Unchanged)

| Setting | Value |
|---------|-------|
| Engine | PostgreSQL 16.x |
| Instance | `db.t3.micro` (dev), `db.t3.small` (prod) |
| Multi-AZ | Yes (prod), No (dev) |
| Storage | 20 GB GP3, auto-scaling to 100 GB |
| Encryption | AWS KMS (default) |
| Backup | 7-day retention, daily snapshot 03:00 UTC |
| Parameter Group | `rds.force_ssl=1`, `shared_preload_libraries=pg_stat_statements` |
| Security Group | Inbound: EC2 SG on 5432 only |
| Subnet Group | Private subnets (2 AZs) |

### 2.3 S3 (Extended)

| Bucket | Purpose | Config |
|--------|---------|--------|
| `pos-deku-frontend-{env}` | React build artifacts | Static website OFF; CloudFront OAI; Versioning ON; Block public access |
| `pos-deku-files-{env}` | User uploads, drafts, exports | Versioning ON; Lifecycle: incomplete MPU → 7 days; CORS for upload |
| `pos-deku-exports-{env}` | Generated markdown exports | Versioning ON; Lifecycle: 90 days → Glacier |
| `pos-deku-templates-{env}` | **NEW: Workspace templates** (devcontainer, Nix, scripts) | Versioning ON; Private; Per-user prefixes |

### 2.4 CloudFront (Extended)

| Behavior | Path Pattern | Origin | Cache Policy | Origin Request Policy |
|----------|--------------|--------|--------------|----------------------|
| API | `/api/*` | ALB | `Managed-CachingDisabled` | `Managed-AllViewerExceptHostHeader` |
| WebSocket | `/ws/*` | ALB | `Managed-CachingDisabled` | `Managed-AllViewerExceptHostHeader` |
| Frontend | `/*` | S3 (OAI) | `Managed-CachingOptimized` | `Managed-CORS-S3Origin` |

### 2.5 SQS (Extended)

| Queue | Type | Config |
|-------|------|--------|
| `pos-deku-capture-processing-{env}` | FIFO | Content-based dedup; Visibility 300s; Retention 14d; DLQ (max 3) |
| `pos-deku-export-{env}` | Standard | Async export jobs; Retention 7d |
| `pos-deku-workspace-provisioning-{env}` | **NEW: Standard** | Workspace provisioning requests; Retention 7d; DLQ |

### 2.6 Cognito (Extended)

| Setting | Value |
|---------|-------|
| User Pool | Email as username; MFA optional (TOTP); Password policy: 12 chars |
| App Client | Public client; Auth flows: USER_PASSWORD_AUTH, REFRESH_TOKEN_AUTH |
| **Device Tracking** | **ENABLED** — for companion device credentials |
| Token Expiry | Access: 1h; Refresh: 30d; ID: 1h |
| Groups | `user`, `admin` |
| Domain | `auth.pos-deku.com` (Cognito hosted UI) |
| Lambda Triggers | Pre-token (custom claims), Post-confirmation (sync to `users`), **Pre-auth (device validation)** |

### 2.7 Secrets Manager (Extended)

| Secret Name | Keys | Rotation |
|-------------|------|----------|
| `pos-deku/database-{env}` | `username`, `password`, `host`, `port`, `dbname` | 90d (RDS managed) |
| `pos-deku/openrouter-{env}` | `api_key` | Manual |
| `pos-deku/cognito-{env}` | `client_id`, `client_secret`, `user_pool_id` | Manual |
| `pos-deku/github-{env}` | `app_id`, `private_key`, `installation_id` | Manual |
| `pos-deku/companion-jwt-{env}` | `secret_key` | 90d |
| `pos-deku/session-broker-{env}` | `jwt_secret` | 90d |

### 2.8 IAM Role: `PosDekuEC2Role` (Extended)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:*:*:secret:pos-deku/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:SendMessage"
      ],
      "Resource": "arn:aws:sqs:*:*:pos-deku-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::pos-deku-files-*",
        "arn:aws:s3:::pos-deku-exports-*",
        "arn:aws:s3:::pos-deku-templates-*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:CreateTags",
        "ec2:DescribeInstanceStatus"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": { "aws:RequestTag/CreatedBy": "pos-deku-workspace" }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "iam:PassRole"
      ],
      "Resource": "arn:aws:iam::*:role/PosDekuWorkspaceInstanceRole"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameters",
        "ssm:SendCommand"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "rds-data:ExecuteStatement",
        "rds-data:BatchExecuteStatement"
      ],
      "Resource": "arn:aws:rds:*:*:cluster:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:log-group:/aws/ec2/pos-deku/*"
    }
  ]
}
```

### 2.9 NEW: IAM Role `PosDekuWorkspaceInstanceRole`

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameters",
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:*:*:secret:pos-deku/github-*",
        "arn:aws:ssm:*:*:parameter/pos-deku/workspace/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::pos-deku-templates-*",
        "arn:aws:s3:::pos-deku-files-*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:CreateTags"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## 3. Security Boundaries (Extended)

### Network
- VPC: 2 public (ALB, NAT), 2 private (EC2, RDS)
- ALB in public; EC2 + RDS in private
- NAT Gateway for outbound (PyPI, OpenRouter, GitHub, S3)
- VPC Endpoints: S3 (Gateway), Secrets Manager (Interface)

### Session Broker Security
- WebSocket endpoint `/ws/companion` on port 8001 (internal ALB target)
- **mTLS or JWT** validation for companion connections
- Rate limiting: 100 connections per device, 1000 msg/min per session
- All RPC calls logged to `session_audit` table

### Workspace Instance Security
- Instances launched with `PosDekuWorkspaceInstanceRole`
- Security Group: Outbound only (GitHub, S3, Secrets Manager, PyPI)
- No inbound SSH; access via SSM Session Manager or portal WebSocket
- UserData script validates instance identity via IMDSv2

---

## 4. Deployment Pipeline (Extended)

### Build (GitHub Actions) — Added Companion Build
```yaml
jobs:
  build-companion:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: cd companion && uv build --wheel
      - uses: actions/upload-artifact@v4
        with:
          name: companion-wheel
          path: companion/dist/*.whl
```

### Deploy Script (Extended)
```bash
#!/bin/bash
# scripts/deploy.sh
ENV=$1  # dev, staging, prod
ROLE=$2  # api, capture_worker, workspace_worker, all

aws ssm send-command \
  --targets "Key=tag:Environment,Values=$ENV" \
  --document-name AWS-RunShellScript \
  --parameters "commands=[
    \"cd /opt/pos-deku\",
    \"uv pip install --upgrade dist/pos_deku-*.whl\",
    \"alembic upgrade head\",
    \"systemctl restart pos-api pos-capture-worker pos-workspace-worker\"
  ]"
```

---

## 5. Cost Estimation (Extended, Monthly us-east-1)

| Component | Dev | Prod | Notes |
|-----------|-----|------|-------|
| EC2 API (t3.medium) | $30 | $60 | |
| EC2 Capture Worker (t3.medium) | $30 | $30 | Always running |
| EC2 Workspace Worker (t3.medium, spot) | $10 | $10 | Scales to 0 when idle |
| Cloud Workspace Instances | $0 | ~$20 | On-demand; user-controlled |
| RDS | $15 | $45 | Multi-AZ prod |
| S3 + Templates | $5 | $15 | |
| CloudFront | $2 | $10 | |
| SQS | <$1 | <$1 | |
| Cognito | Free | ~$5 | |
| Secrets Manager | $1 | $1 | 6 secrets |
| CloudWatch | $5 | $20 | Added session/workspace metrics |
| Data Transfer | $3 | $15 | |
| **Total** | **~$96** | **~$230** | |

---

## 6. Rollout Strategy (Phased)

| Phase | Milestone | Action | Validation |
|-------|-----------|--------|------------|
| 1 | M1: Cloud Portal | Deploy infra, deploy API + capture worker | Contract tests, smoke tests |
| 2 | M2: Local Integration | Deploy session broker, build companion | Companion registration, heartbeat |
| 3 | M3: Sync | Enable sync endpoints, offline queue | Offline→online reconciliation |
| 4 | M4: Remote Access | Enable companion RPC, terminal | Secure terminal session |
| 5 | M5: Cloud Workspaces | Deploy workspace worker, templates | Provision → terminal → handoff |

---

## 7. Disaster Recovery (Extended)

| Scenario | RPO | RTO | Procedure |
|----------|-----|-----|-----------|
| RDS failure | < 1s | < 2 min | Auto failover (Multi-AZ) |
| EC2 API failure | 0 | < 3 min | ASG replaces; ALB health check |
| Session broker failure | 0 | < 3 min | ASG replaces; companions reconnect |
| Workspace instance failure | 0 | < 5 min | Auto-terminate; Git branch recovery |
| Region failure | 24h | 2h | Restore RDS snapshot; redeploy |

---

## 8. Monitoring Dashboard (Extended)

**New Widgets**:
- Session Broker: Active connections, RPC rate, error rate, latency
- Companion: Devices online/offline, heartbeat lag, sync acknowledgement and conflict counts
- Workspaces: Provisioning rate, success rate, active instances, cost/day
- Sync: Operations queued, conflicts/day, reconciliation latency

---

## 9. Acceptance Criteria (Infrastructure — Extended)

1. Terraform applies cleanly for `dev`/`prod`
2. EC2 instances join ASG, register with ALB, pass health checks (8000, 8001)
3. RDS accessible; migrations run
4. SQS queues receive/process messages (capture, export, workspace)
5. Cognito issues user JWT + device JWT; API validates both
6. S3 presigned URLs work (CORS OK); template bucket accessible
7. CloudFront serves frontend + proxies API + WebSocket; TLS valid
8. Secrets retrieved at runtime; no secrets in artifacts
9. CloudWatch alarms fire on simulated failures
10. Deploy script updates code + migrations + restarts < 2 min
11. **Companion** registers, heartbeats, receives RPC manifest
12. **Session broker** accepts WebSocket, validates JWT, forwards RPC
13. **Workspace worker** provisions instance, applies template, returns WS URL
14. **Workspace instance** accessible via portal terminal; Git handoff works
