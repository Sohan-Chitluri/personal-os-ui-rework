# ruff: noqa: T201, E501
"""Budget-capped live smoke test for Amazon Bedrock Nova 2 Lite in us-east-1.

Verifies:
1. Valid AWS credentials via standard SDK chain.
2. Bedrock model availability and authorization status.
3. Budget-capped single-turn invocation (max 20 tokens, budget cap < $0.001 USD).
"""

from __future__ import annotations

import asyncio
import sys

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from personal_os.core.llm import ChatMessage
from personal_os.core.llm_bedrock import BedrockNovaProvider, calculate_nova_cost


async def run_live_smoke(
    model_id: str = "us.amazon.nova-2-lite-v1:0",
    region_name: str = "us-east-1",
    budget_cap_usd: float = 0.001,
) -> int:
    print("=" * 70)
    print("PERSONAL OS — BEDROCK NOVA 2 LITE LIVE SMOKE TEST")
    print(f"Region: {region_name}")
    print(f"Model ID: {model_id}")
    print(f"Budget Cap: ${budget_cap_usd:.4f} USD")
    print("=" * 70)

    # 1. Check AWS credentials
    session = boto3.Session(region_name=region_name)
    creds = session.get_credentials()
    if not creds:
        print("❌ FAILED: No AWS credentials found in the environment or standard chain.")
        return 1
    print(f"✓ AWS Credentials detected (AccessKey: {creds.access_key[:8]}...)")

    # 2. Check model access status via Bedrock control plane
    bedrock = session.client("bedrock", region_name=region_name)
    try:
        # Check model availability status
        raw_model_id = "amazon.nova-2-lite-v1:0"
        avail = bedrock.get_foundation_model_availability(modelId=raw_model_id)
        auth_status = avail.get("authorizationStatus", "UNKNOWN")
        print(f"✓ Foundation model availability status: {auth_status}")

        if auth_status == "NOT_AUTHORIZED":
            print("\n⚠️ ACCOUNT ACCESS BLOCKER DETECTED:")
            print(f"  AWS Account 414329795881 is NOT_AUTHORIZED for model '{raw_model_id}'.")
            print("  Amazon Bedrock requires filling out the model access request form in the AWS Console.")
            print("  Console URL: https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess")
            print("  Action needed: Open the console, navigate to Model Access, and submit access request for Amazon Nova models.")
            print("  Live invocation cannot proceed until AWS approves/enables access.")
            return 2
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        msg = exc.response.get("Error", {}).get("Message")
        print(f"ℹ️ Bedrock model availability check returned [{code}]: {msg}")
    except Exception as exc:
        print(f"ℹ️ Bedrock control plane check skipped: {exc}")

    # 3. Budget-capped live invocation test
    print("\nAttempting budget-capped Bedrock Converse invocation (maxTokens=15)...")
    provider = BedrockNovaProvider(
        model_id=model_id,
        region_name=region_name,
        timeout_seconds=15.0,
        max_tokens=15,
        temperature=0.0,
    )

    try:
        response = await provider.chat(
            messages=[ChatMessage(role="user", content="Ping. Respond with 'OK'.")],
            max_tokens=15,
        )
    except Exception as exc:
        err_str = str(exc)
        if "Operation not allowed" in err_str or "AccessDenied" in err_str:
            print("\n⚠️ LIVE INVOCATION BLOCKED BY BEDROCK PERMISSIONS:")
            print(f"  {err_str}")
            print("  Cause: The AWS account does not yet have foundation model entitlement enabled in AWS Bedrock.")
            return 2
        print(f"\n❌ Live invocation failed with error: {exc}")
        return 1

    # Verify budget and response
    tokens = response.usage
    cost = calculate_nova_cost(model_id, tokens.input_tokens, tokens.output_tokens)

    print("\n✓ LIVE SMOKE TEST SUCCEEDED!")
    print(f"  Assistant reply: {response.message.content.strip()!r}")
    print(f"  Latency:         {response.latency_ms:.1f} ms")
    print(f"  Input tokens:    {tokens.input_tokens}")
    print(f"  Output tokens:   {tokens.output_tokens}")
    print(f"  Total tokens:    {tokens.total_tokens}")
    print(f"  Incurred Cost:   ${cost:.6f} USD")
    assert cost <= budget_cap_usd, f"Cost ${cost} exceeded budget cap ${budget_cap_usd}"

    return 0


def main() -> None:
    code = asyncio.run(run_live_smoke())
    sys.exit(code)


if __name__ == "__main__":
    main()
