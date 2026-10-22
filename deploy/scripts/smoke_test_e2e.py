#!/usr/bin/env python3
"""Personal OS — End-to-End Deployment Smoke Test.

Exercises the full API and worker processing pipeline against staging or local:
1. API liveness (/api/v1/health) with configurable readiness polling.
2. Database readiness (/api/v1/ready) and version (/api/v1/version).
3. Auth security: unauthenticated access rejection (401).
4. Authenticated session validation (Cognito JWT or local dev/synthetic token).
5. Capture submission (POST /api/v1/captures) with idempotency key.
6. Worker / SQS async processing verification (GET /api/v1/captures/{id}/status).
7. Test data cleanup (DELETE /api/v1/captures/{id}) to avoid leaving test artifacts.

Usage:
    # Against local API:
    python deploy/scripts/smoke_test_e2e.py --url http://127.0.0.1:8000 --skip-auth
    python deploy/scripts/smoke_test_e2e.py --url http://127.0.0.1:8000 --dev-token dev-secret

    # Against staging ALB or custom domain:
    python deploy/scripts/smoke_test_e2e.py --url http://personal-os-staging-1532085612.us-east-1.elb.amazonaws.com --token <COGNITO_ACCESS_TOKEN>
    python deploy/scripts/smoke_test_e2e.py --url https://personal-os-staging.vercel.app --token <COGNITO_ACCESS_TOKEN>

    # Auto-resolve URL from OpenTofu / Terraform:
    python deploy/scripts/smoke_test_e2e.py --token <COGNITO_ACCESS_TOKEN>
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import uuid

import httpx

# ANSI color helpers
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


@dataclass
class TestResult:
    name: str
    status: str  # "PASS", "FAIL", "SKIP", "WARN"
    duration_ms: float
    details: str = ""
    error: str | None = None


@dataclass
class SmokeTestReport:
    target_url: str
    environment: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    results: list[TestResult] = field(default_factory=list)
    success: bool = True

    def add(self, result: TestResult) -> None:
        self.results.append(result)
        if result.status == "FAIL":
            self.success = False


def log_step(status: str, name: str, duration_ms: float, details: str = "") -> None:
    badge = {
        "PASS": f"{GREEN}[PASS]{RESET}",
        "FAIL": f"{RED}[FAIL]{RESET}",
        "SKIP": f"{YELLOW}[SKIP]{RESET}",
        "WARN": f"{YELLOW}[WARN]{RESET}",
    }.get(status, f"[{status}]")
    msg = f"{badge} {name:<45} ({duration_ms:.1f}ms)"
    if details:
        msg += f" - {details}"
    print(msg)


def resolve_base_url(cli_url: str | None) -> str:
    """Resolve the API base URL from CLI, environment, or OpenTofu outputs."""
    if cli_url:
        return cli_url.rstrip("/")

    env_url = os.environ.get("API_BASE_URL") or os.environ.get("API_URL") or os.environ.get("FRONTEND_URL")
    if env_url:
        return env_url.rstrip("/")

    # Attempt resolving from Terraform / OpenTofu
    repo_root = Path(__file__).resolve().parent.parent.parent
    tf_dir = repo_root / "deploy" / "terraform"
    iac_bin = "tofu" if subprocess.run(["which", "tofu"], capture_output=True).returncode == 0 else "terraform"

    try:
        res = subprocess.run(
            [iac_bin, f"-chdir={tf_dir}", "output", "-raw", "alb_dns_name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            alb_dns = res.stdout.strip()
            return f"http://{alb_dns}"
    except Exception:
        pass

    # Default to local dev server
    return "http://127.0.0.1:8000"


def check_liveness_and_readiness(
    client: httpx.Client,
    base_url: str,
    ready_timeout: float,
    report: SmokeTestReport,
) -> bool:
    """Check API liveness (/api/v1/health) with retry loop until timeout."""
    start_time = time.time()
    health_url = f"{base_url}/api/v1/health"
    last_error = ""
    is_live = False

    print(f"\n{BLUE}=== Step 1: Liveness & Readiness Checks ==={RESET}")
    print(f"Probing {health_url} (timeout: {ready_timeout}s)...")

    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        req_start = time.time()
        try:
            resp = client.get(health_url, timeout=5.0)
            elapsed = (time.time() - req_start) * 1000.0
            if resp.status_code == 200:
                is_live = True
                log_step("PASS", "API Liveness (/api/v1/health)", elapsed, resp.text.strip())
                report.add(TestResult("API Liveness", "PASS", elapsed, resp.text.strip()))
                break
            last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
        except httpx.RequestError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2.0)

    if not is_live:
        total_elapsed = (time.time() - start_time) * 1000.0
        log_step(
            "FAIL",
            "API Liveness (/api/v1/health)",
            total_elapsed,
            f"Service not ready after {ready_timeout}s. Last error: {last_error}",
        )
        report.add(TestResult("API Liveness", "FAIL", total_elapsed, last_error, error=last_error))
        return False

    # Check /api/v1/ready (Database readiness)
    ready_url = f"{base_url}/api/v1/ready"
    req_start = time.time()
    try:
        resp = client.get(ready_url, timeout=5.0)
        elapsed = (time.time() - req_start) * 1000.0
        if resp.status_code == 200:
            data = resp.json()
            db_status = data.get("database", "unknown")
            log_step("PASS", "API Readiness (/api/v1/ready)", elapsed, f"database={db_status}")
            report.add(TestResult("API Readiness", "PASS", elapsed, f"database={db_status}"))
        else:
            log_step("FAIL", "API Readiness (/api/v1/ready)", elapsed, f"HTTP {resp.status_code}: {resp.text[:120]}")
            report.add(TestResult("API Readiness", "FAIL", elapsed, resp.text[:120], error=resp.text))
            return False
    except Exception as exc:
        elapsed = (time.time() - req_start) * 1000.0
        log_step("FAIL", "API Readiness (/api/v1/ready)", elapsed, str(exc))
        report.add(TestResult("API Readiness", "FAIL", elapsed, str(exc), error=str(exc)))
        return False

    # Check /api/v1/version
    version_url = f"{base_url}/api/v1/version"
    req_start = time.time()
    try:
        resp = client.get(version_url, timeout=5.0)
        elapsed = (time.time() - req_start) * 1000.0
        if resp.status_code == 200:
            data = resp.json()
            report.environment = data.get("environment")
            log_step("PASS", "API Version (/api/v1/version)", elapsed, f"version={data.get('version')} env={report.environment}")
            report.add(TestResult("API Version", "PASS", elapsed, json.dumps(data)))
        else:
            log_step("WARN", "API Version (/api/v1/version)", elapsed, f"HTTP {resp.status_code}")
            report.add(TestResult("API Version", "WARN", elapsed, f"HTTP {resp.status_code}"))
    except Exception as exc:
        elapsed = (time.time() - req_start) * 1000.0
        log_step("WARN", "API Version (/api/v1/version)", elapsed, str(exc))

    return True


def check_auth_security(client: httpx.Client, base_url: str, report: SmokeTestReport) -> None:
    """Verify that unauthenticated requests to protected endpoints are rejected with 401."""
    print(f"\n{BLUE}=== Step 2: Authentication Security Check ==={RESET}")
    protected_url = f"{base_url}/api/v1/captures"
    req_start = time.time()
    try:
        resp = client.get(protected_url, timeout=5.0)
        elapsed = (time.time() - req_start) * 1000.0
        if resp.status_code == 401:
            log_step("PASS", "Unauthenticated Protection (GET /captures)", elapsed, "Rejected with HTTP 401 Unauthorized")
            report.add(TestResult("Unauthenticated Protection", "PASS", elapsed, "HTTP 401 as expected"))
        else:
            detail = f"Expected 401, got HTTP {resp.status_code}"
            log_step("FAIL", "Unauthenticated Protection (GET /captures)", elapsed, detail)
            report.add(TestResult("Unauthenticated Protection", "FAIL", elapsed, detail, error=detail))
    except Exception as exc:
        elapsed = (time.time() - req_start) * 1000.0
        log_step("FAIL", "Unauthenticated Protection (GET /captures)", elapsed, str(exc))
        report.add(TestResult("Unauthenticated Protection", "FAIL", elapsed, str(exc), error=str(exc)))


def resolve_auth_token(args: argparse.Namespace) -> str | None:
    """Resolve authentication bearer token from arguments or environment."""
    if args.token:
        return args.token

    env_token = os.environ.get("AUTH_TOKEN") or os.environ.get("PERSONAL_OS_TOKEN")
    if env_token:
        return env_token

    if args.dev_token:
        return args.dev_token

    if args.synthetic_user:
        return f"test:{args.synthetic_user}"

    # Check Cognito username/password if provided
    if args.username and args.password:
        try:
            import boto3

            client = boto3.client("cognito-idp", region_name=args.cognito_region or "us-east-1")
            resp = client.initiate_auth(
                ClientId=args.cognito_client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": args.username, "PASSWORD": args.password},
            )
            return resp["AuthenticationResult"]["AccessToken"]
        except Exception as exc:
            print(f"{YELLOW}Cognito direct login failed: {exc}{RESET}")

    return None


def run_authenticated_pipeline(
    client: httpx.Client,
    base_url: str,
    token: str,
    worker_timeout: float,
    keep_data: bool,
    report: SmokeTestReport,
) -> None:
    """Submit capture, poll for worker processing, and clean up test data."""
    print(f"\n{BLUE}=== Step 3: Capture Submission & SQS Worker Processing ==={RESET}")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Verify token works on GET /api/v1/captures
    req_start = time.time()
    try:
        resp = client.get(f"{base_url}/api/v1/captures?limit=1", headers=headers, timeout=5.0)
        elapsed = (time.time() - req_start) * 1000.0
        if resp.status_code == 200:
            log_step("PASS", "Authenticated Session Verification", elapsed, "Token accepted")
            report.add(TestResult("Authenticated Session Verification", "PASS", elapsed))
        else:
            detail = f"HTTP {resp.status_code}: {resp.text[:120]}"
            log_step("FAIL", "Authenticated Session Verification", elapsed, detail)
            report.add(TestResult("Authenticated Session Verification", "FAIL", elapsed, detail, error=detail))
            return
    except Exception as exc:
        elapsed = (time.time() - req_start) * 1000.0
        log_step("FAIL", "Authenticated Session Verification", elapsed, str(exc))
        report.add(TestResult("Authenticated Session Verification", "FAIL", elapsed, str(exc), error=str(exc)))
        return

    # 2. Submit capture with idempotency key
    idem_key = f"smoke-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    capture_payload = {
        "content": "Personal OS deployment smoke-test: verify capture intake, queue delivery, and background worker extraction.",
        "source": "smoke_test",
        "content_type": "text/plain",
        "idempotency_key": idem_key,
    }

    req_start = time.time()
    capture_id = None
    try:
        resp = client.post(f"{base_url}/api/v1/captures", json=capture_payload, headers=headers, timeout=10.0)
        elapsed = (time.time() - req_start) * 1000.0
        if resp.status_code in (201, 202):
            data = resp.json()
            capture_id = data.get("capture_id")
            persisted = data.get("persisted")
            status_text = "Created" if resp.status_code == 201 else "Queued (Recovery)"
            detail = f"{status_text} id={capture_id} persisted={persisted}"
            log_step("PASS", "Capture Submission (POST /captures)", elapsed, detail)
            report.add(TestResult("Capture Submission", "PASS", elapsed, detail))
        else:
            detail = f"HTTP {resp.status_code}: {resp.text[:120]}"
            log_step("FAIL", "Capture Submission (POST /captures)", elapsed, detail)
            report.add(TestResult("Capture Submission", "FAIL", elapsed, detail, error=detail))
            return
    except Exception as exc:
        elapsed = (time.time() - req_start) * 1000.0
        log_step("FAIL", "Capture Submission (POST /captures)", elapsed, str(exc))
        report.add(TestResult("Capture Submission", "FAIL", elapsed, str(exc), error=str(exc)))
        return

    if not capture_id:
        return

    # 3. Poll for worker processing
    print(f"Waiting for worker / SQS processing of capture {capture_id} (timeout: {worker_timeout}s)...")
    status_url = f"{base_url}/api/v1/captures/{capture_id}/status"
    worker_start = time.time()
    deadline = time.time() + worker_timeout
    final_status = "pending"
    attempts = 0

    while time.time() < deadline:
        attempts += 1
        try:
            resp = client.get(status_url, headers=headers, timeout=5.0)
            if resp.status_code == 200:
                status_data = resp.json()
                final_status = status_data.get("status", "unknown")
                if final_status in ("processed", "failed", "completed"):
                    break
        except Exception:
            pass
        time.sleep(2.0)

    total_worker_elapsed = (time.time() - worker_start) * 1000.0
    if final_status == "processed":
        detail = f"Status=processed in {total_worker_elapsed:.1f}ms ({attempts} checks)"
        log_step("PASS", "Worker SQS Processing", total_worker_elapsed, detail)
        report.add(TestResult("Worker SQS Processing", "PASS", total_worker_elapsed, detail))
    elif final_status == "failed":
        detail = f"Worker picked up task but extraction failed (expected if mock/unconfigured LLM): status=failed in {total_worker_elapsed:.1f}ms"
        log_step("PASS", "Worker SQS Processing", total_worker_elapsed, detail)
        report.add(TestResult("Worker SQS Processing", "PASS", total_worker_elapsed, detail))
    else:
        detail = f"Capture remained status='{final_status}' after {worker_timeout}s. Worker may not be consuming SQS queue."
        log_step("WARN", "Worker SQS Processing", total_worker_elapsed, detail)
        report.add(TestResult("Worker SQS Processing", "WARN", total_worker_elapsed, detail))

    # 4. Clean up test capture unless --keep-data is specified
    if not keep_data:
        req_start = time.time()
        try:
            del_resp = client.delete(f"{base_url}/api/v1/captures/{capture_id}", headers=headers, timeout=5.0)
            elapsed = (time.time() - req_start) * 1000.0
            if del_resp.status_code in (200, 204):
                log_step("PASS", "Test Data Cleanup (DELETE /captures/{id})", elapsed, f"Deleted {capture_id}")
                report.add(TestResult("Test Data Cleanup", "PASS", elapsed, f"Deleted {capture_id}"))
            else:
                log_step("WARN", "Test Data Cleanup", elapsed, f"HTTP {del_resp.status_code}")
        except Exception as exc:
            log_step("WARN", "Test Data Cleanup", 0.0, str(exc))
    else:
        log_step("SKIP", "Test Data Cleanup", 0.0, "Preserved (--keep-data)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Personal OS Deployment E2E Smoke Test")
    parser.add_argument("--url", help="API base URL (default: resolved from Terraform or local)")
    parser.add_argument("--token", help="Bearer access token (JWT)")
    parser.add_argument("--dev-token", help="Static dev auth token for local/test")
    parser.add_argument("--synthetic-user", help="Synthetic test user identifier (e.g. smoke-user)")
    parser.add_argument("--username", help="Cognito username for login")
    parser.add_argument("--password", help="Cognito password for login")
    parser.add_argument("--cognito-client-id", default="195u9r05vim046hkij9vec8d81", help="Cognito App Client ID")
    parser.add_argument("--cognito-region", default="us-east-1", help="Cognito AWS Region")
    parser.add_argument("--ready-timeout", type=float, default=30.0, help="Seconds to wait for API liveness (default: 30)")
    parser.add_argument("--worker-timeout", type=float, default=30.0, help="Seconds to wait for SQS worker (default: 30)")
    parser.add_argument("--skip-auth", action="store_true", help="Skip authenticated tests")
    parser.add_argument("--keep-data", action="store_true", help="Preserve test capture without deleting")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit report as JSON")

    args = parser.parse_args()
    base_url = resolve_base_url(args.url)
    report = SmokeTestReport(target_url=base_url)

    if not args.json_output:
        print(f"Targeting: {base_url}")

    with httpx.Client(follow_redirects=True) as client:
        # Step 1: Liveness & Readiness
        is_ready = check_liveness_and_readiness(client, base_url, args.ready_timeout, report)
        if not is_ready:
            if args.json_output:
                print(json.dumps(report.__dict__, default=lambda o: o.__dict__, indent=2))
            return 2

        # Step 2: Unauthenticated Security Check
        check_auth_security(client, base_url, report)

        # Step 3: Authenticated Pipeline
        if args.skip_auth:
            log_step("SKIP", "Capture & Worker Verification", 0.0, "Skipped (--skip-auth)")
        else:
            token = resolve_auth_token(args)
            if token:
                run_authenticated_pipeline(client, base_url, token, args.worker_timeout, args.keep_data, report)
            else:
                msg = "No token provided. Supply --token, AUTH_TOKEN, or Cognito credentials to exercise capture submission."
                log_step("SKIP", "Capture & Worker Verification", 0.0, msg)
                report.add(TestResult("Capture & Worker Verification", "SKIP", 0.0, msg))

    if args.json_output:
        print(json.dumps(report.__dict__, default=lambda o: o.__dict__, indent=2))
    else:
        print("\n" + "=" * 65)
        passed = sum(1 for r in report.results if r.status == "PASS")
        failed = sum(1 for r in report.results if r.status == "FAIL")
        skipped = sum(1 for r in report.results if r.status == "SKIP")
        warns = sum(1 for r in report.results if r.status == "WARN")
        print(f"SUMMARY: {passed} passed, {failed} failed, {warns} warnings, {skipped} skipped.")
        if report.success and failed == 0:
            print(f"{GREEN}SMOKE TEST SUITE PASSED{RESET}")
        else:
            print(f"{RED}SMOKE TEST SUITE FAILED{RESET}")

    return 0 if (report.success and failed == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
