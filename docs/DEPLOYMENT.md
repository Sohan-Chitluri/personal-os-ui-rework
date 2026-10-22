# Personal OS — Deployment Guide (M1-D)

**Status**: preparation only. Nothing has been provisioned or deployed. All
commands below assume credentials with sufficient IAM permissions and an
existing Route 53 hosted zone for `pos-deku.com`.

**Approach**: Terraform (the project's documented choice, see
`docs/IMPLEMENTATION_PLAN.md` workstream F). One root module under
`deploy/terraform/`, per-environment tfvars/backend files, systemd unit
definitions for API and worker under `deploy/systemd/`.

---

## 1. Process definitions (API vs Worker)

| | API | Worker |
|---|---|---|
| Unit | `deploy/systemd/pos-api.service` | `deploy/systemd/pos-worker.service` |
| Entry point | `uvicorn personal_os.api.main:app --host 0.0.0.0 --port 8000 --workers 2` | `python -m personal_os.deploy_worker` (thin runner around `personal_os.services.worker.CaptureWorker`) |
| Health | `/health` (liveness) and `/api/v1/ready` (readiness; checks DB) — existing endpoints, unchanged | No port. CloudWatch `WorkerHeartbeat` metric + alarm on 5-minute staleness |
| Shutdown | SIGTERM → uvicorn drains, ASG `deregistration_delay=30` | SIGTERM → runner finishes its current operation and exits; durable pending rows are recovered at worker startup |
| Restart policy | `Restart=on-failure` (5s) + ELB health check + ASG replace | `Restart=on-failure` (10s) + heartbeat alarm |
| Logging | journald → CloudWatch agent → `/personal-os/<env>/api` | journald → CloudWatch agent → `/personal-os/<env>/worker` |

The API and worker use the same AMI with role-specific launch templates; the
IMDSv2-protected `Role` instance tag (instance-tag metadata explicitly enabled)
selects which systemd unit starts. They are separate ASGs conceptually but
currently both run `min=1, max=1` per role — worker auto-scaling by queue
depth is not configured in this sprint.

## 2. Environment and secrets

`/etc/personal-os/env` (mode 0640, root:app) is written at instance boot from
Secrets Manager by `deploy/terraform/templates/user_data.sh.tftpl`. It holds:

```
ENVIRONMENT=staging            # app Settings.environment; production only after hardening
DEBUG=false
LOG_LEVEL=INFO
AWS_REGION=us-east-1
QUEUE_BACKEND=sqs
SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/<account>/<environment>-capture
SQS_REGION=us-east-1
SQS_WAIT_TIME_SECONDS=20
SQS_VISIBILITY_TIMEOUT=900
SQS_MAX_MESSAGES=1
DATABASE_URL=postgresql+asyncpg://...?sslmode=require
COGNITO_USER_POOL_ID=<pool-id>
COGNITO_APP_CLIENT_ID=<client-id>
COGNITO_TOKEN_USE=access
COMPANION_JWT_SECRET=<injected-from-secrets-manager>
GITHUB_CLIENT_ID=<injected-from-secrets-manager>
GITHUB_CLIENT_SECRET=<injected-from-secrets-manager>
GITHUB_TOKEN_ENCRYPTION_KEY=<injected-from-secrets-manager>
GITHUB_REDIRECT_URI=https://api.pos-deku.com/api/v1/github/callback
```

- GitHub App credentials: `personal-os/<env>/github` (Secrets Manager holding `client_id`, `client_secret`, and `token_encryption_key`). See `docs/GITHUB_APP_SETUP.md` for full GitHub App configuration and least-privilege permission details.

- RDS master credentials: `personal-os/<env>/database` (Secrets Manager,
  created by `rds.tf`; app fetches at boot via instance role — never embedded
  in the repo, the wheel, or state after rotation).
- Terraform provisions the Cognito user pool, public app client, and hosted UI
  domain. The API receives pool/client IDs through its root-only environment
  file and refuses staging startup if they are absent.
- Staging currently uses Cognito's AWS-managed hosted domain, exported as
  `cognito_hosted_ui_domain`; this avoids requiring a separate custom-domain
  certificate and DNS alias for the sign-in flow.
- API auth verifies RS256 signatures through cached Cognito JWKS, issuer,
  expiration, issue time, subject, and `token_use`. By default it accepts
  access tokens and checks `client_id`; set `COGNITO_TOKEN_USE=id` only when
  intentionally using ID tokens, which are checked against `aud`. Synthetic
  synthetic tokens require an additional explicit opt-in and are disabled
  outside `local` and `test`.
- Browser sign-in uses Cognito's authorization-code flow with PKCE. The
  frontend build reads the hosted domain, app client ID, and frontend URL from
  Terraform outputs; access tokens are held in session storage.
- OpenRouter credentials: `personal-os/<env>/openrouter-api-key` (Secrets
  Manager, managed in `security.tf`). The EC2 bootstrap fetches it at boot via
  the IAM instance role and injects `OPENROUTER_API_KEY` into `/etc/personal-os/env`
  for the worker process. The secret value may be formatted as plain text or JSON
  (`{"api_key": "..."}`). When unset or during provider outages, capture processing
  safely records a retryable failure rather than a false success.
- Local development: `.env` (git-ignored) continues to work via
  `pydantic-settings`; never commit it.
- Local queue default remains `QUEUE_BACKEND=inline`. Set `QUEUE_BACKEND=sqs`
  and `SQS_QUEUE_URL` only when intentionally using an AWS queue; missing or
  malformed SQS configuration fails startup. SQS delivers at least once, not
  exactly once. The 15-minute lease is extended periodically while the worker
  processes a message. Transient processing failures defer visibility; SQS
  redrive moves messages received five times to the DLQ.

## 3. RDS configuration

- PostgreSQL 16, private subnets (2 AZs), `publicly_accessible=false`.
- TLS enforced server-side: parameter group sets `rds.force_ssl=1`; app
  connects with `sslmode=require` in the URL. Clients must present the RDS
  CA bundle (system trust store on AL2023 includes it via `libpq`/asyncpg
  defaults — verify on first boot).
- Backups: 7-day retention, daily window 03:00–04:00 UTC. Production:
  `deletion_protection=true`, Multi-AZ, Performance Insights on.
- Migrations: `alembic upgrade head` runs on the deploy path (see
  `deploy/scripts/release.sh deploy`) with the instance's DATABASE_URL.
  Migrations are owned by workstream A — this repo runs them, never edits them.
- Security group: port 5432 open ONLY to the app SG; no public route.

## 4. Frontend build / deployment (S3 + CloudFront)

The UI is TanStack Start (nitro). S3 hosting requires a **static SPA output**
— the Lovable nitro preset currently defaults to a cloudflare target, which
may emit a server bundle. `deploy/scripts/build-frontend.sh` fails loudly if
`dist/server` appears. This is a known prerequisite (§8).

```
build:  cd ui && bun install --frozen-lockfile && bun run build   # → ui/dist
upload: ./deploy/scripts/deploy-frontend.sh dev
```

CloudFront serves `https://app.<domain>/` from S3 (OAC, private bucket) and
proxies `/api/*` to an internet-facing ALB over HTTPS with caching disabled.
The origin hostname is `api.<domain>` and its ACM certificate matches that
hostname. The ALB security group allows TLS only from AWS's CloudFront
origin-facing managed prefix list. The ALB forwards `/api/*` only when the CloudFront-injected
`X-Origin-Verify` header matches; its listener default rejects every other
path. Direct internet requests cannot reach the ALB listener; the random header
value is held in Terraform state and should be
protected accordingly; it is not returned by the application.

## 5. Build, validate, deploy — exact commands

One-time bootstrap:
```
aws configure                                     # credentials
./deploy/scripts/bootstrap-remote-state.sh        # S3 state bucket + lock table
# edit deploy/terraform/envs/*.tfvars  → route53_zone_id
# edit deploy/terraform/envs/*.backend.hcl → bucket name
```

Validate (static + local). OpenTofu (`tofu`) is used interchangeably with
`terraform` — both work; install per https://opentofu.org:
```
terraform -chdir=deploy/terraform fmt -recursive
terraform -chdir=deploy/terraform init -backend=false
terraform -chdir=deploy/terraform validate
./deploy/scripts/build-backend.sh                 # ruff, mypy, pytest, wheel
./deploy/scripts/build-frontend.sh                # emits ui/dist/client/index.html + assets
```

Plan / apply (this is the first step that touches AWS — deliberate, gated):
```
terraform -chdir=deploy/terraform init \
  -backend-config=deploy/terraform/envs/dev.backend.hcl
terraform -chdir=deploy/terraform plan \
  -var-file=deploy/terraform/envs/dev.tfvars -out=tfplan
terraform -chdir=deploy/terraform apply tfplan
```

Backend deploy:
```
./deploy/scripts/release.sh dev release           # build + upload wheel
./deploy/scripts/release.sh dev deploy            # SSM: install, migrate, restart
```

Frontend deploy:
```
./deploy/scripts/build-frontend.sh
./deploy/scripts/deploy-frontend.sh dev
```

Smoke test:
```
./deploy/scripts/smoke-test.sh https://app.dev.pos-deku.com
```

## 6. Cost estimate (monthly, us-east-1, dev environment)

Explicit assumptions: 720 h/month, 1 t3.small API + 1 t3.small worker running
24×7, RDS db.t4g.micro Single-AZ, 20 GB gp3, CloudFront ~10 GB/mo,
logs ~5 GB/mo.

| Component | Spec | Monthly |
|---|---|---|
| EC2 API | t3.small × 1 × 720 h | $15.0 |
| EC2 worker | t3.small × 1 × 720 h | $15.0 |
| EBS | 2 × 20 GB gp3 | $3.3 |
| RDS | db.t4g.micro Single-AZ + 20 GB + 7d backups | $13.0 |
| ALB | 1 × 720 h + minimal LCU | ~$17.5 |
| NAT Gateway | 1 (dev uses single-AZ NAT) + ~10 GB | ~$36.0 |
| CloudFront | 10 GB @ PriceClass_100 | ~$1.2 |
| S3 | frontend < 1 GB | <$0.5 |
| Secrets Manager | 1 secret | $0.4 |
| CloudWatch | logs 5 GB ingest+storage, alarms, agent | ~$7.5 |
| Route 53 | 1 zone + queries | ~$0.5 |
| SQS | not yet deployed | $0 |
| **Total (dev)** | | **≈ $110** |

Cost drivers: NAT Gateway (~$33), ALB (~$17.5), the two always-on EC2
instances (~$30). Cheapest meaningful reduction: host the worker only during
business hours (ASG scheduled action, −$15) and replace the NAT Gateway with
VPC interface endpoints where feasible (−~$25, adds ~$7 in endpoint-hours).
Production roughly doubles compute and adds Multi-AZ RDS (+$14), Multi-AZ NAT
(+$33), and higher ALB/LCU.

Teardown: `./deploy/scripts/teardown.sh dev` (see §7 of that script). Prod
requires an explicit snapshot flag. Dev teardown leaves nothing billable.

## 7. Validation performed

- **OpenTofu 1.10.4** (Terraform-compatible open-source CLI) is used for
  static validation of the module. `tofu init -backend=false` +
  `tofu validate` **pass** (see §7.1 for issues this validation caught and
  fixed). `tofu fmt -recursive` is applied.
- Shell scripts are POSIX bash with `set -euo pipefail`, each checked with
  `bash -n`.
- The user_data template was render-checked: all `templatefile` variables are
  accounted for, bash expansions are `$${...}`-escaped, and the rendered
  script passes `bash -n`.
- Frontend build script is defensive: fails if nitro emits a server bundle.
- **No live AWS validation** has been performed. The first AWS-touching
  command in the runbook is the gated `terraform plan/apply` in §5. The
  commands in this doc use `terraform` (OpenTofu is a drop-in; swap the
  binary name if you prefer `tofu`).

### 7.1 Issues caught and fixed during static validation

Running the actual `tofu validate` surfaced real defects that a
line-by-line review missed:

1. **SG dependency cycle** — `aws_security_group.alb` referenced
   `aws_security_group.app` in its egress rules while the app SG's ingress
   referenced the ALB SG. Fixed by widening ALB egress (the app SG's ingress
   remains the boundary).
2. **`templatefile` vs bash interpolation collision** — shell expansions like
   `${APP_VERSION}` inside the `.tftpl` were parsed as Terraform template
   variables. Fixed by `$${...}`-escaping them.
3. **ASG `override` block rejected** — worker ASG tried a nested
   `override { instance_type }`, which the provider doesn't support. Fixed
   with a dedicated worker launch template.
4. **IMDSv2 token fetch was broken** (nested `$()` inside single quotes).
   Fixed with a two-step token fetch.
5. **DB password not exported** for the URL-encoding python snippet.
6. **S3 lifecycle rule warnings** (missing `filter {}`) — fixed to stay ahead
   of the provider's future hard error.

## 8. Application prerequisites still blocking deployment

These conditions still need to be addressed or explicitly accepted before a
user-facing deployment.

1. **Capture dispatch is at least once.** API and worker select the same
   Standard SQS queue through `QUEUE_BACKEND=sqs`, `SQS_QUEUE_URL`, and
   `SQS_REGION`. The adapter long-polls, leaves failed deliveries unacknowledged,
   changes visibility for bounded retries, and extends the lease while work is
   active. Terraform configures server-side encryption, 15-minute visibility,
   a five-receive redrive policy, a 14-day DLQ, and backlog/DLQ alarms. IAM
   access is scoped to the capture queue. `QUEUE_BACKEND=inline` is for local
   development only; it is volatile and single-process. The database outbox
   is the durable publication boundary. The outbox dispatcher and local
   PostgreSQL integration suite are verified, but SQS has not been exercised
   against AWS; staging still needs an authorized queue smoke test.
2. **Static frontend output** — the TanStack Start/Lovable nitro build
   currently targets a server runtime; S3+CloudFront needs a static SPA
   build. Must be resolved in the UI build config (nitro preset change).
3. **CORS origins** — `create_app` hard-codes `allow_origins=["*"]` with
   `allow_credentials=True`, which browsers reject. Once the frontend calls
   the API cross-origin, this must become the explicit frontend origin. If
   the CloudFront same-origin proxy path is used exclusively, CORS is moot —
   but the setting must still be tightened before any public exposure.
4. **Migrations against PostgreSQL** — revisions `0001_initial` through
   `0003_capture_dispatch_outbox` were applied from scratch and the downgrade /
   upgrade recovery path passed on an isolated PostgreSQL 16.15 instance.
   Production RDS credentials and deployment approval remain outside this
   validation.

## Local PostgreSQL and queue verification (2026-10-18)

The complete Python suite was run against the isolated PostgreSQL 16.15
`personal_os_test` database with:

```bash
DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55439/personal_os_test \
  scripts/test-postgres.sh
```

Result: **324 passed**. The run applied Alembic revisions
`0001_initial -> 0002_uniqueness -> 0003_capture_dispatch_outbox` from a clean
test schema. It exercised migration downgrade/backfill/upgrade, repository and
API behavior, timezone round-trips, capture recovery, concurrent outbox
dispatch, and local-queue end-to-end processing. `tests/services/test_sqs_queue.py`
uses botocore stubs and passed without AWS access. These results do not verify
AWS IAM, SQS redrive behavior, CloudWatch delivery, RDS networking, or a staging
deployment. Use [PostgreSQL development setup](POSTGRES_DEVELOPMENT.md) for the
local Compose startup and disposable test commands.
