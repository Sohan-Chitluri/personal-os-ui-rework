"""Integration tests executing the Amazon Nova 2 Lite 32-case benchmark."""

from __future__ import annotations

import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest  # noqa: E402

from typing import Any

from scripts.benchmark_nova import build_benchmark_cases, run_benchmark_case  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize("case", build_benchmark_cases(), ids=lambda c: c.id)
async def test_benchmark_scenario(case: Any) -> None:
    result = await run_benchmark_case(case)
    assert result.success, f"Scenario {case.id} failed: {result.notes}"
    assert result.tool_valid, f"Scenario {case.id} produced invalid tool call: {result.notes}"
    assert result.conversational_quality >= 4.0, (
        f"Scenario {case.id} quality score {result.conversational_quality} < 4.0: {result.notes}"
    )
    assert result.total_tokens > 0
    assert result.cost_usd > 0.0
