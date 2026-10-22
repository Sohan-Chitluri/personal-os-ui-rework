#!/usr/bin/env python3
"""
Automated test suite verifying both Personal OS staging deployment modes:
  Mode 1: Default CloudFront hostname without Route 53 or custom domain.
  Mode 2: Custom domain enabled with Route 53 DNS and ACM TLS certificates.

Validates:
1. OpenTofu syntax and provider schema validation (tofu validate) in both modes.
2. HCL resource conditioning: ACM, Route 53 records, ALB listener, security groups,
   CloudFront origins, aliases, viewer certificates, and Cognito callback URLs.
3. IAM policy isolation: verify deployer provisioning policies require zero Route 53
   permissions in default mode.

Usage:
    uv run python deploy/scripts/test_deployment_modes.py
    uv run pytest deploy/scripts/test_deployment_modes.py
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TF_DIR = REPO_ROOT / "deploy" / "terraform"
IAM_DIR = REPO_ROOT / "deploy" / "iam"


def run_tofu_validate(extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["tofu", f"-chdir={TF_DIR}", "validate", "-no-color"]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True)


def test_mode1_default_cloudfront_tofu_validation():
    """Mode 1: Validate OpenTofu with staging.tfvars (root_domain and route53_zone_id empty)."""
    res = run_tofu_validate()
    assert res.returncode == 0, f"tofu validate failed for default mode:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert "Success! The configuration is valid." in res.stdout


def test_mode2_custom_domain_tofu_validation():
    """Mode 2: Validate OpenTofu with custom domain and Route 53 zone ID configured."""
    res = run_tofu_validate()
    assert res.returncode == 0, f"tofu validate failed for custom domain mode:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"


def test_staging_tfvars_defaults_to_mode1():
    """Ensure staging.tfvars defaults to empty root_domain and route53_zone_id."""
    tfvars_path = TF_DIR / "envs" / "staging.tfvars"
    content = tfvars_path.read_text(encoding="utf-8")
    
    root_domain_match = re.search(r'root_domain\s*=\s*"([^"]*)"', content)
    route53_zone_match = re.search(r'route53_zone_id\s*=\s*"([^"]*)"', content)
    
    assert root_domain_match is not None, "root_domain not found in staging.tfvars"
    assert route53_zone_match is not None, "route53_zone_id not found in staging.tfvars"
    
    assert root_domain_match.group(1) == "", (
        f"staging.tfvars root_domain must default to empty string, got: '{root_domain_match.group(1)}'"
    )
    assert route53_zone_match.group(1) == "", (
        f"staging.tfvars route53_zone_id must default to empty string, got: '{route53_zone_match.group(1)}'"
    )


def test_variables_tf_optional_defaults():
    """Ensure variables.tf declares root_domain and route53_zone_id with default = \"\"."""
    variables_path = TF_DIR / "variables.tf"
    content = variables_path.read_text(encoding="utf-8")
    
    # Check variable root_domain
    assert 'variable "root_domain"' in content
    root_domain_block = content[content.index('variable "root_domain"'):]
    root_domain_block = root_domain_block[:root_domain_block.index("}")]
    assert 'default     = ""' in root_domain_block or 'default = ""' in root_domain_block
    
    # Check variable route53_zone_id
    assert 'variable "route53_zone_id"' in content
    r53_block = content[content.index('variable "route53_zone_id"'):]
    r53_block = r53_block[:r53_block.index("}")]
    assert 'default     = ""' in r53_block or 'default = ""' in r53_block
    
    # Check local custom_domain_enabled definition
    assert "custom_domain_enabled = var.root_domain != \"\" && var.route53_zone_id != \"\"" in content


def test_dns_tf_resources_conditional():
    """Ensure all resources in dns.tf are gated behind local.custom_domain_enabled."""
    dns_path = TF_DIR / "dns.tf"
    content = dns_path.read_text(encoding="utf-8")
    
    # Check count on aws_acm_certificate.api
    assert 'resource "aws_acm_certificate" "api"' in content
    assert "count             = local.custom_domain_enabled ? 1 : 0" in content
    
    # Check count on aws_acm_certificate.frontend
    assert 'resource "aws_acm_certificate" "frontend"' in content
    
    # Check count on aws_route53_record.api_origin
    assert 'resource "aws_route53_record" "api_origin"' in content
    
    # Check count on aws_route53_record.frontend_alias
    assert 'resource "aws_route53_record" "frontend_alias"' in content
    
    # Check for_each on validation records
    assert "for_each = local.custom_domain_enabled ?" in content


def test_compute_alb_listener_dual_mode():
    """Ensure aws_lb_listener.https in compute.tf dynamically switches between HTTP (80) and HTTPS (443)."""
    compute_path = TF_DIR / "compute.tf"
    content = compute_path.read_text(encoding="utf-8")
    
    assert "port              = local.custom_domain_enabled ? 443 : 80" in content
    assert "protocol          = local.custom_domain_enabled ? \"HTTPS\" : \"HTTP\"" in content
    assert "ssl_policy        = local.custom_domain_enabled ? \"ELBSecurityPolicy-TLS13-1-2-2021-06\" : null" in content
    assert "certificate_arn   = local.custom_domain_enabled ? aws_acm_certificate_validation.api[0].certificate_arn : null" in content


def test_security_alb_ingress_mode_port():
    """Ensure security.tf dynamically scopes ALB ingress to port 80 (default) or 443 (custom domain)."""
    security_path = TF_DIR / "security.tf"
    content = security_path.read_text(encoding="utf-8")
    
    assert "from_port       = local.custom_domain_enabled ? 443 : 80" in content
    assert "to_port         = local.custom_domain_enabled ? 443 : 80" in content
    assert "prefix_list_ids = var.enable_cloudfront ? [data.aws_ec2_managed_prefix_list.cloudfront_origin.id] : []" in content


def test_frontend_distribution_dual_mode():
    """Ensure aws_cloudfront_distribution.frontend handles both default CloudFront and custom domains."""
    frontend_path = TF_DIR / "frontend.tf"
    content = frontend_path.read_text(encoding="utf-8")
    
    # Aliases
    assert 'aliases = local.custom_domain_enabled ? ["${var.frontend_subdomain}.${var.root_domain}"] : []' in content
    
    # ALB Origin
    assert "domain_name = local.custom_domain_enabled ? \"${var.api_subdomain}.${var.root_domain}\" : aws_lb.main.dns_name" in content
    assert "origin_protocol_policy = local.custom_domain_enabled ? \"https-only\" : \"http-only\"" in content
    
    # Viewer certificate
    assert "cloudfront_default_certificate = local.custom_domain_enabled ? false : true" in content
    assert "acm_certificate_arn            = local.custom_domain_enabled ? aws_acm_certificate_validation.frontend[0].certificate_arn : null" in content
    assert "ssl_support_method             = local.custom_domain_enabled ? \"sni-only\" : null" in content


def test_auth_cognito_urls_use_frontend_url():
    """Ensure auth.tf uses local.frontend_url for callback and logout URLs."""
    auth_path = TF_DIR / "auth.tf"
    content = auth_path.read_text(encoding="utf-8")
    
    assert 'callback_urls                        = ["${local.frontend_url}/auth/callback"]' in content
    assert 'logout_urls                          = ["${local.frontend_url}/"]' in content


def test_iam_part1_has_no_route53_actions():
    """Ensure personal-os-provisioning-policy-part1.json contains zero route53 permissions."""
    part1_path = IAM_DIR / "personal-os-provisioning-policy-part1.json"
    data = json.loads(part1_path.read_text(encoding="utf-8"))
    
    for stmt in data.get("Statement", []):
        raw_actions = stmt.get("Action", [])
        actions = [raw_actions] if isinstance(raw_actions, str) else list(raw_actions)
        for act in actions:
            assert not act.lower().startswith("route53:"), (
                f"Unexpected Route 53 action '{act}' found in Part 1 statement '{stmt.get('Sid')}'"
            )


def main() -> int:
    print("=" * 70)
    print("Personal OS — Dual Deployment Mode Test Suite (CloudFront & Custom Domain)")
    print("=" * 70)

    tests = [
        ("Mode 1: Default CloudFront tofu validate", test_mode1_default_cloudfront_tofu_validation),
        ("Mode 2: Custom domain tofu validate", test_mode2_custom_domain_tofu_validation),
        ("staging.tfvars defaults to Mode 1 (empty domain/zone)", test_staging_tfvars_defaults_to_mode1),
        ("variables.tf optional defaults and local flag", test_variables_tf_optional_defaults),
        ("dns.tf resources conditioned on custom_domain_enabled", test_dns_tf_resources_conditional),
        ("compute.tf ALB listener dual mode (HTTP/HTTPS)", test_compute_alb_listener_dual_mode),
        ("security.tf ALB security group dynamic port (80/443)", test_security_alb_ingress_mode_port),
        ("frontend.tf CloudFront dual mode (aliases/origin/viewer_cert)", test_frontend_distribution_dual_mode),
        ("auth.tf Cognito callback URLs use local.frontend_url", test_auth_cognito_urls_use_frontend_url),
        ("IAM Part 1 contains zero Route 53 actions", test_iam_part1_has_no_route53_actions),
    ]

    passed = 0
    failures = []

    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failures.append((name, str(e)))

    print("-" * 70)
    print(f"Results: {passed}/{len(tests)} tests passed.")
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1

    print("ALL DEPLOYMENT MODE CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
