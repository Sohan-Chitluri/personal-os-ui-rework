#!/usr/bin/env python3
"""
Automated regression test suite for Personal OS IAM deployment policies.

Validates:
1. JSON syntax and AWS IAM policy structure across all policy files.
2. Compliance with the AWS 6,144-character managed policy size limit.
3. Strict enforcement that split policies (Part 2 and Part 3) are each under 5,000
   formatted characters for direct pasting in the AWS IAM Console JSON editor.
4. Strict absence of dangerous IAM actions (CreateRole, PutRolePolicy, AttachRolePolicy,
   CreateInstanceProfile, UpdateAssumeRolePolicy, etc.) from deployer policies.
5. Zero IAM mutation permissions in Part 1, Part 2, and Routine Deployment policies.
6. Hardened least-privilege scoping of iam:PassRole to the specific application role
   with mandatory ec2.amazonaws.com and rds.amazonaws.com service constraints in Part 3.
7. Zero Route 53 permissions in deployer policies (since staging uses default CloudFront).
8. Proper separation of S3 bucket-level vs. object-level ARNs.
9. Completeness of the Administrator Bootstrap policy for remote state and application role.

Usage:
    uv run python deploy/scripts/test_iam_policies.py
    uv run pytest deploy/scripts/test_iam_policies.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import pytest

AWS_MANAGED_POLICY_CHAR_LIMIT = 6144
CONSOLE_FORMATTED_LIMIT = 5000
TARGET_ACCOUNT_ID = "414329795881"

IAM_DIR = Path(__file__).resolve().parent.parent / "iam"

DEPLOYER_POLICY_FILES = [
    "personal-os-provisioning-policy-part1.json",
    "personal-os-provisioning-policy-part2.json",
    "personal-os-provisioning-policy-part3.json",
    "personal-os-routine-deployment-policy.json",
]

ALL_POLICY_FILES = [
    "personal-os-provisioning-policy-part1.json",
    "personal-os-provisioning-policy-part1.min.json",
    "personal-os-provisioning-policy-part2.json",
    "personal-os-provisioning-policy-part3.json",
    "personal-os-routine-deployment-policy.json",
    "personal-os-admin-bootstrap-policy.json",
    "personal-os-bootstrap-policy.json",
    "personal-os-provisioning-policy.json",
]

# Blacklist of dangerous IAM actions that must NEVER appear in deployer policies
DANGEROUS_IAM_ACTIONS = {
    "iam:createrole",
    "iam:deleterole",
    "iam:updaterole",
    "iam:putrolepolicy",
    "iam:deleterolepolicy",
    "iam:attachrolepolicy",
    "iam:detachrolepolicy",
    "iam:createinstanceprofile",
    "iam:deleteinstanceprofile",
    "iam:addroletoinstanceprofile",
    "iam:removerolefrominstanceprofile",
    "iam:updateassumerolepolicy",
    "iam:createpolicy",
    "iam:deletepolicy",
    "iam:createpolicyversion",
    "iam:deletepolicyversion",
    "iam:setdefaultpolicyversion",
    "iam:putrolepermissionsboundary",
    "iam:deleterolepermissionsboundary",
}


def load_policy(filename: str) -> dict:
    filepath = IAM_DIR / filename
    assert filepath.is_file(), f"Policy file not found: {filepath}"
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def get_compact_length(data: dict) -> int:
    return len(json.dumps(data, separators=(",", ":")))


def get_formatted_length(data: dict) -> int:
    return len(json.dumps(data, indent=2))


def extract_actions(statement: dict) -> list[str]:
    raw = statement.get("Action", [])
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def extract_resources(statement: dict) -> list[str]:
    raw = statement.get("Resource", [])
    if isinstance(raw, str):
        return [raw]
    return list(raw)


@pytest.mark.parametrize("filename", ALL_POLICY_FILES)
def test_policy_json_schema(filename: str):
    """Every policy file must be valid JSON with correct IAM top-level keys."""
    data = load_policy(filename)
    assert data.get("Version") == "2012-10-17", f"{filename} missing Version 2012-10-17"
    assert "Statement" in data, f"{filename} missing Statement"
    assert isinstance(data["Statement"], list), f"{filename} Statement must be a list"
    assert len(data["Statement"]) > 0, f"{filename} Statement must not be empty"

    for i, stmt in enumerate(data["Statement"]):
        assert "Effect" in stmt, f"{filename} statement[{i}] missing Effect"
        assert stmt["Effect"] in ("Allow", "Deny"), f"{filename} statement[{i}] invalid Effect: {stmt['Effect']}"
        assert "Action" in stmt, f"{filename} statement[{i}] missing Action"
        assert "Resource" in stmt, f"{filename} statement[{i}] missing Resource"


@pytest.mark.parametrize(
    "filename",
    [
        "personal-os-provisioning-policy-part1.json",
        "personal-os-provisioning-policy-part1.min.json",
        "personal-os-provisioning-policy-part2.json",
        "personal-os-provisioning-policy-part3.json",
        "personal-os-routine-deployment-policy.json",
        "personal-os-admin-bootstrap-policy.json",
        "personal-os-bootstrap-policy.json",
    ],
)
def test_managed_policy_size_limits(filename: str):
    """Policies intended as AWS managed policies must not exceed 6,144 compact characters."""
    data = load_policy(filename)
    compact_len = get_compact_length(data)
    assert compact_len <= AWS_MANAGED_POLICY_CHAR_LIMIT, (
        f"{filename} exceeds 6,144 chars ({compact_len} chars, "
        f"oversized by {compact_len - AWS_MANAGED_POLICY_CHAR_LIMIT})"
    )


@pytest.mark.parametrize(
    "filename",
    [
        "personal-os-provisioning-policy-part2.json",
        "personal-os-provisioning-policy-part3.json",
    ],
)
def test_split_policies_formatted_under_5000_chars(filename: str):
    """Split policies Part 2 and Part 3 must each be under 5,000 formatted characters for AWS Console input."""
    data = load_policy(filename)
    formatted_len = get_formatted_length(data)
    assert formatted_len < CONSOLE_FORMATTED_LIMIT, (
        f"{filename} formatted JSON exceeds {CONSOLE_FORMATTED_LIMIT} chars (got {formatted_len} chars)"
    )


def test_reference_policy_marked_oversized():
    """Consolidated policy is an un-split reference document that exceeds 6,144 chars."""
    data = load_policy("personal-os-provisioning-policy.json")
    compact_len = get_compact_length(data)
    assert compact_len > AWS_MANAGED_POLICY_CHAR_LIMIT, (
        f"Consolidated reference policy was expected to exceed limit, but is {compact_len} chars"
    )


@pytest.mark.parametrize("filename", DEPLOYER_POLICY_FILES)
def test_no_dangerous_iam_actions_in_deployer_policies(filename: str):
    """Deployer policies must NEVER contain any dangerous IAM lifecycle or escalation actions."""
    data = load_policy(filename)
    found_violations = []

    for stmt in data.get("Statement", []):
        sid = stmt.get("Sid", "UnknownSid")
        for action in extract_actions(stmt):
            action_lower = action.lower()
            if action_lower in DANGEROUS_IAM_ACTIONS:
                found_violations.append(f"Statement '{sid}': forbidden action '{action}'")
            if action_lower.startswith("iam:") and ("*" in action_lower or "?" in action_lower):
                found_violations.append(f"Statement '{sid}': forbidden IAM wildcard '{action}'")

    assert not found_violations, (
        f"Dangerous IAM actions detected in {filename}:\n" + "\n".join(found_violations)
    )


def test_part1_has_zero_iam_actions():
    """Provisioning Part 1 must contain zero IAM actions."""
    data = load_policy("personal-os-provisioning-policy-part1.json")
    iam_actions = []
    for stmt in data.get("Statement", []):
        for action in extract_actions(stmt):
            if action.lower().startswith("iam:"):
                iam_actions.append(f"{stmt.get('Sid')}: {action}")
    assert not iam_actions, f"Part 1 should have 0 IAM actions, but found:\n" + "\n".join(iam_actions)


def test_part2_has_zero_iam_actions():
    """Provisioning Part 2 (VPC, Compute, ALB) must contain zero IAM actions."""
    data = load_policy("personal-os-provisioning-policy-part2.json")
    iam_actions = []
    for stmt in data.get("Statement", []):
        for action in extract_actions(stmt):
            if action.lower().startswith("iam:"):
                iam_actions.append(f"{stmt.get('Sid')}: {action}")
    assert not iam_actions, f"Part 2 should have 0 IAM actions, but found:\n" + "\n".join(iam_actions)


def test_routine_has_zero_iam_actions():
    """Routine deployment policy must contain zero IAM actions."""
    data = load_policy("personal-os-routine-deployment-policy.json")
    iam_actions = []
    for stmt in data.get("Statement", []):
        for action in extract_actions(stmt):
            if action.lower().startswith("iam:"):
                iam_actions.append(f"{stmt.get('Sid')}: {action}")
    assert not iam_actions, f"Routine policy should have 0 IAM actions, but found:\n" + "\n".join(iam_actions)


@pytest.mark.parametrize("filename", DEPLOYER_POLICY_FILES)
def test_deployer_policies_have_zero_route53_actions(filename: str):
    """Deployer policies must contain zero Route 53 actions when staging uses default CloudFront."""
    data = load_policy(filename)
    r53_actions = []
    for stmt in data.get("Statement", []):
        for action in extract_actions(stmt):
            if action.lower().startswith("route53:"):
                r53_actions.append(f"{stmt.get('Sid')}: {action}")
    assert not r53_actions, f"{filename} contains unexpected Route 53 actions:\n" + "\n".join(r53_actions)


def test_part3_iam_actions_strictly_whitelisted():
    """Part 3 must contain only read-only IAM actions and hardened iam:PassRole."""
    data = load_policy("personal-os-provisioning-policy-part3.json")
    allowed_iam_actions = {
        "iam:getrole",
        "iam:getrolepolicy",
        "iam:listrolepolicies",
        "iam:listattachedrolepolicies",
        "iam:getinstanceprofile",
        "iam:passrole",
    }

    found_iam_actions = set()
    pass_role_stmts = []

    for stmt in data.get("Statement", []):
        for action in extract_actions(stmt):
            if action.lower().startswith("iam:"):
                found_iam_actions.add(action.lower())
                assert action.lower() in allowed_iam_actions, (
                    f"Unexpected IAM action in Part 3: '{action}' in statement '{stmt.get('Sid')}'"
                )
                if action.lower() == "iam:passrole":
                    pass_role_stmts.append(stmt)

    assert len(pass_role_stmts) == 1, f"Expected exactly 1 iam:PassRole statement in Part 3, found {len(pass_role_stmts)}"
    pr_stmt = pass_role_stmts[0]

    # Verify passrole resource is strictly the app-role
    resources = extract_resources(pr_stmt)
    assert len(resources) == 1, f"PassRole must target exactly 1 resource, got: {resources}"
    expected_arns = [
        f"arn:aws:iam::{TARGET_ACCOUNT_ID}:role/personal-os-staging-app-role",
        "arn:aws:iam::<ACCOUNT_ID>:role/personal-os-staging-app-role",
    ]
    assert resources[0] in expected_arns, (
        f"PassRole target ARN mismatch: {resources[0]}"
    )

    # Verify passed-to-service condition
    cond = pr_stmt.get("Condition", {})
    string_equals = cond.get("StringEquals", {})
    passed_to = string_equals.get("iam:PassedToService", [])
    if isinstance(passed_to, str):
        passed_to = [passed_to]
    assert "ec2.amazonaws.com" in passed_to, "PassRole condition missing ec2.amazonaws.com"
    assert "rds.amazonaws.com" in passed_to, "PassRole condition missing rds.amazonaws.com"
    assert len(passed_to) == 2, f"PassRole condition has extra services: {passed_to}"


def test_s3_bucket_vs_object_arn_separation():
    """Verify S3 statements separate bucket-level actions from object-level actions."""
    part1 = load_policy("personal-os-provisioning-policy-part1.json")
    for stmt in part1.get("Statement", []):
        actions = extract_actions(stmt)
        resources = extract_resources(stmt)
        s3_actions = [a for a in actions if a.lower().startswith("s3:")]
        if not s3_actions:
            continue

        bucket_level_only = {"s3:listbucket", "s3:getbucketlocation", "s3:createbucket", "s3:deletebucket"}
        object_level_only = {"s3:getobject", "s3:putobject", "s3:deleteobject"}

        has_bucket_action = any(a.lower() in bucket_level_only for a in s3_actions)
        has_object_action = any(a.lower() in object_level_only for a in s3_actions)

        for res in resources:
            if has_bucket_action and not has_object_action:
                assert not res.endswith("/*"), (
                    f"Statement '{stmt.get('Sid')}' has bucket-only actions {s3_actions} but targets object ARN '{res}'"
                )
            if has_object_action and not has_bucket_action:
                assert res.endswith("/*"), (
                    f"Statement '{stmt.get('Sid')}' has object-only actions {s3_actions} but targets bucket ARN '{res}'"
                )


def test_admin_bootstrap_policy():
    """Admin bootstrap policy must provide remote state bucket, lock table, and IAM app role creation."""
    data = load_policy("personal-os-admin-bootstrap-policy.json")
    sids = {stmt.get("Sid") for stmt in data.get("Statement", [])}
    assert "RemoteStateBucketBootstrap" in sids
    assert "StateLockTableBootstrap" in sids
    assert "ApplicationIAMRoleBootstrap" in sids

    # Find the IAM bootstrap statement and verify it is scoped to personal-os-staging-*
    iam_stmt = next(s for s in data["Statement"] if s.get("Sid") == "ApplicationIAMRoleBootstrap")
    resources = extract_resources(iam_stmt)
    expected_role_arns = [
        f"arn:aws:iam::{TARGET_ACCOUNT_ID}:role/personal-os-staging-*",
        "arn:aws:iam::<ACCOUNT_ID>:role/personal-os-staging-*",
    ]
    expected_profile_arns = [
        f"arn:aws:iam::{TARGET_ACCOUNT_ID}:instance-profile/personal-os-staging-*",
        "arn:aws:iam::<ACCOUNT_ID>:instance-profile/personal-os-staging-*",
    ]
    assert any(arn in resources for arn in expected_role_arns)
    assert any(arn in resources for arn in expected_profile_arns)


def main() -> int:
    print("=" * 70)
    print("Personal OS — Automated IAM Policy Regression Test Suite")
    print("=" * 70)

    total_tests = 0
    passed_tests = 0
    failures = []

    def run_check(name: str, fn):
        nonlocal total_tests, passed_tests
        total_tests += 1
        try:
            fn()
            print(f"  [PASS] {name}")
            passed_tests += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failures.append((name, str(e)))

    for f in ALL_POLICY_FILES:
        run_check(f"Schema: {f}", lambda f=f: test_policy_json_schema(f))

    for f in [
        "personal-os-provisioning-policy-part1.json",
        "personal-os-provisioning-policy-part2.json",
        "personal-os-provisioning-policy-part3.json",
        "personal-os-routine-deployment-policy.json",
        "personal-os-admin-bootstrap-policy.json",
        "personal-os-bootstrap-policy.json",
    ]:
        run_check(f"Size limit (<6144 compact): {f}", lambda f=f: test_managed_policy_size_limits(f))

    for f in [
        "personal-os-provisioning-policy-part2.json",
        "personal-os-provisioning-policy-part3.json",
    ]:
        run_check(f"Formatted Console Limit (<5000 chars): {f}", lambda f=f: test_split_policies_formatted_under_5000_chars(f))

    run_check("Reference policy oversized (>6144)", test_reference_policy_marked_oversized)

    for f in DEPLOYER_POLICY_FILES:
        run_check(f"No dangerous IAM actions: {f}", lambda f=f: test_no_dangerous_iam_actions_in_deployer_policies(f))

    run_check("Part 1 zero IAM actions", test_part1_has_zero_iam_actions)
    run_check("Part 2 zero IAM actions", test_part2_has_zero_iam_actions)
    run_check("Routine policy zero IAM actions", test_routine_has_zero_iam_actions)

    for f in DEPLOYER_POLICY_FILES:
        run_check(f"Zero Route 53 actions: {f}", lambda f=f: test_deployer_policies_have_zero_route53_actions(f))

    run_check("Part 3 IAM actions strictly whitelisted and PassRole conditioned", test_part3_iam_actions_strictly_whitelisted)
    run_check("S3 bucket vs object ARN separation", test_s3_bucket_vs_object_arn_separation)
    run_check("Admin bootstrap policy coverage", test_admin_bootstrap_policy)

    print("-" * 70)
    print(f"Results: {passed_tests}/{total_tests} tests passed.")
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1

    print("ALL IAM POLICY REGRESSION CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
