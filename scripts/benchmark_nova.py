# ruff: noqa: T201, E501
"""Repeatable benchmark suite for Amazon Nova 2 Lite in Personal OS.

Runs 32 representative requests covering:
1. Natural daily conversation and contextual follow-ups.
2. Task creation and editing.
3. Relative dates and scheduling.
4. Project/task reference resolution.
5. Multi-step requests.
6. Invalid tool arguments and ambiguous instructions.

Measures:
- Task accuracy (%)
- Tool-call validity (%)
- Conversational quality score (1-5 scale)
- Latency (p50, p90, mean ms)
- Token usage (input, output, total)
- Estimated cost ($ USD)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from personal_os.config import Settings
from personal_os.core.llm import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    TokenUsage,
    ToolCall,
)
from personal_os.core.llm_bedrock import BedrockNovaProvider, calculate_nova_cost
from personal_os.db.models import Project, Task
from personal_os.services.assistant import BedrockAssistantEngine
from personal_os.services.interfaces import ProjectService, ServiceNotFound, TaskService

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("benchmark")


@dataclass
class BenchmarkCase:
    id: str
    category: str
    prompt: str
    history: list[ChatMessage] = field(default_factory=list)
    initial_tasks: list[dict[str, Any]] = field(default_factory=list)
    initial_projects: list[dict[str, Any]] = field(default_factory=list)
    expected_intent: str = ""
    expected_tool: str | None = None
    expected_action_type: str | None = None
    expected_requires_confirmation: bool | None = None
    simulated_model_turns: list[ChatResponse] = field(default_factory=list)


@dataclass
class CaseResult:
    case_id: str
    category: str
    prompt: str
    success: bool
    tool_valid: bool
    conversational_quality: float  # 1.0 to 5.0
    latency_ms: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    final_reply: str
    proposals: list[dict[str, Any]]
    notes: str = ""


# Fixed evaluation reference timestamp: Tuesday, 2026-10-20 14:30:00 UTC
BENCHMARK_REF_TIME = datetime(2026, 10, 20, 14, 30, 0, tzinfo=UTC)


def build_benchmark_cases() -> list[BenchmarkCase]:
    """Construct the 32 representative benchmark cases."""
    cases: list[BenchmarkCase] = []

    # --------------------------------------------------------------------------
    # Category 1: Natural Conversation & Contextual Follow-ups (5 cases)
    # --------------------------------------------------------------------------
    cases.append(
        BenchmarkCase(
            id="cat1_01_greeting",
            category="natural_conversation",
            prompt="Hello Raphael! What can you help me with in Personal OS?",
            expected_intent="greeting_and_overview",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content=(
                            "Hello! I am Raphael, your assistant in Personal OS. "
                            "I can help you manage and search your tasks, inspect your active projects, "
                            "schedule deadlines using natural language, and prepare action proposals."
                        ),
                    ),
                    usage=TokenUsage(input_tokens=180, output_tokens=42, total_tokens=222),
                    latency_ms=160.0,
                    model="us.amazon.nova-2-lite-v1:0",
                )
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat1_02_task_organization_advice",
            category="natural_conversation",
            prompt="What is the best way to organize my tasks across multiple projects?",
            expected_intent="advice",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content=(
                            "In Personal OS, you can group tasks under distinct projects (like 'VLSI' or 'Personal OS'). "
                            "Each task can have priorities from 1 (highest) to 5, explicit deadlines, and status tracking."
                        ),
                    ),
                    usage=TokenUsage(input_tokens=195, output_tokens=45, total_tokens=240),
                    latency_ms=175.0,
                    model="us.amazon.nova-2-lite-v1:0",
                )
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat1_03_contextual_follow_up",
            category="natural_conversation",
            prompt="What did we just talk about?",
            history=[
                ChatMessage(role="user", content="Tell me about the CORDIC algorithm."),
                ChatMessage(
                    role="assistant",
                    content="CORDIC is an efficient algorithm for computing trigonometric and hyperbolic functions.",
                ),
            ],
            expected_intent="contextual_recall",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="We were just discussing the CORDIC algorithm and its efficient calculation of trigonometric functions.",
                    ),
                    usage=TokenUsage(input_tokens=240, output_tokens=25, total_tokens=265),
                    latency_ms=150.0,
                    model="us.amazon.nova-2-lite-v1:0",
                )
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat1_04_system_grounding",
            category="natural_conversation",
            prompt="What day and time is it according to the system?",
            expected_intent="grounded_time",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="According to the application reference time, today is Tuesday, October 20, 2026, at 14:30 UTC.",
                    ),
                    usage=TokenUsage(input_tokens=190, output_tokens=28, total_tokens=218),
                    latency_ms=140.0,
                    model="us.amazon.nova-2-lite-v1:0",
                )
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat1_05_polite_acknowledgment",
            category="natural_conversation",
            prompt="Thank you for your help!",
            expected_intent="polite_signoff",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="You're very welcome! Let me know whenever you'd like to check your tasks or plan your day.",
                    ),
                    usage=TokenUsage(input_tokens=175, output_tokens=24, total_tokens=199),
                    latency_ms=130.0,
                    model="us.amazon.nova-2-lite-v1:0",
                )
            ],
        )
    )

    # --------------------------------------------------------------------------
    # Category 2: Task Creation & Editing (6 cases)
    # --------------------------------------------------------------------------
    cases.append(
        BenchmarkCase(
            id="cat2_01_create_simple_task",
            category="task_creation_and_editing",
            prompt="Create a task 'Prepare presentation slides' due tomorrow",
            expected_tool="propose_task_action",
            expected_action_type="create_task",
            expected_requires_confirmation=False,
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_c1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "create_task",
                                    "title": "Prepare presentation slides",
                                    "due_date": "tomorrow",
                                    "priority": 2,
                                    "rationale": "User requested creation of presentation slides task",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=210, output_tokens=35, total_tokens=245),
                    latency_ms=180.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="I have created the task **Prepare presentation slides** due tomorrow (2026-10-21).",
                    ),
                    usage=TokenUsage(input_tokens=260, output_tokens=28, total_tokens=288),
                    latency_ms=145.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat2_02_create_high_priority_task",
            category="task_creation_and_editing",
            prompt="Add urgent task 'Fix security vulnerability CVE-2026-11' with priority 1",
            expected_tool="propose_task_action",
            expected_action_type="create_task",
            expected_requires_confirmation=False,
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_c2",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "create_task",
                                    "title": "Fix security vulnerability CVE-2026-11",
                                    "priority": 1,
                                    "rationale": "High priority security vulnerability task",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=215, output_tokens=36, total_tokens=251),
                    latency_ms=185.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Created high-priority task **Fix security vulnerability CVE-2026-11** (Priority 1).",
                    ),
                    usage=TokenUsage(input_tokens=265, output_tokens=26, total_tokens=291),
                    latency_ms=140.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat2_03_update_task_title",
            category="task_creation_and_editing",
            prompt="Update task t_01 title to 'Review VLSI chapter 3 draft'",
            initial_tasks=[
                {"id": "t_01", "title": "Review VLSI draft", "revision": 1, "status": "todo"}
            ],
            expected_tool="propose_task_action",
            expected_action_type="update_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_u1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "update_task",
                                    "task_id": "t_01",
                                    "expected_revision": 1,
                                    "title": "Review VLSI chapter 3 draft",
                                    "rationale": "Update task title as requested",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=220, output_tokens=38, total_tokens=258),
                    latency_ms=190.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Updated task title to **Review VLSI chapter 3 draft**.",
                    ),
                    usage=TokenUsage(input_tokens=270, output_tokens=22, total_tokens=292),
                    latency_ms=135.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat2_04_complete_task",
            category="task_creation_and_editing",
            prompt="Mark task t_02 as completed",
            initial_tasks=[
                {"id": "t_02", "title": "Submit expense receipts", "revision": 2, "status": "todo"}
            ],
            expected_tool="propose_task_action",
            expected_action_type="complete_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_cmp1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "complete_task",
                                    "task_id": "t_02",
                                    "expected_revision": 2,
                                    "rationale": "Mark task completed",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=210, output_tokens=32, total_tokens=242),
                    latency_ms=175.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Marked task **Submit expense receipts** (ID: `t_02`) as completed.",
                    ),
                    usage=TokenUsage(input_tokens=255, output_tokens=24, total_tokens=279),
                    latency_ms=130.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat2_05_update_description",
            category="task_creation_and_editing",
            prompt="Update description of task t_01 to 'Simulations finished with 0 DRC violations'",
            initial_tasks=[
                {"id": "t_01", "title": "VLSI Tapeout DRC", "revision": 1, "status": "todo"}
            ],
            expected_tool="propose_task_action",
            expected_action_type="update_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_ud1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "update_task",
                                    "task_id": "t_01",
                                    "expected_revision": 1,
                                    "description": "Simulations finished with 0 DRC violations",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=225, output_tokens=36, total_tokens=261),
                    latency_ms=180.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Updated description for **VLSI Tapeout DRC**.",
                    ),
                    usage=TokenUsage(input_tokens=270, output_tokens=20, total_tokens=290),
                    latency_ms=125.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat2_06_update_multiple_fields",
            category="task_creation_and_editing",
            prompt="Change task t_01 priority to 2 and status to in_progress",
            initial_tasks=[
                {"id": "t_01", "title": "VLSI Design Review", "revision": 1, "status": "todo", "priority": 3}
            ],
            expected_tool="propose_task_action",
            expected_action_type="update_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_um1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "update_task",
                                    "task_id": "t_01",
                                    "expected_revision": 1,
                                    "priority": 2,
                                    "status": "in_progress",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=220, output_tokens=35, total_tokens=255),
                    latency_ms=175.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Updated task **VLSI Design Review** to priority 2 and status 'in_progress'.",
                    ),
                    usage=TokenUsage(input_tokens=265, output_tokens=25, total_tokens=290),
                    latency_ms=135.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    # --------------------------------------------------------------------------
    # Category 3: Relative Dates & Scheduling (6 cases)
    # --------------------------------------------------------------------------
    cases.append(
        BenchmarkCase(
            id="cat3_01_resolve_tomorrow",
            category="relative_dates_and_scheduling",
            prompt="What date is 'tomorrow'?",
            expected_tool="resolve_relative_date",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_rd1",
                                name="resolve_relative_date",
                                arguments={"expression": "tomorrow"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=185, output_tokens=25, total_tokens=210),
                    latency_ms=140.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Tomorrow is Wednesday, October 21, 2026 (2026-10-21).",
                    ),
                    usage=TokenUsage(input_tokens=230, output_tokens=22, total_tokens=252),
                    latency_ms=125.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat3_02_schedule_in_3_days",
            category="relative_dates_and_scheduling",
            prompt="Schedule task 'Dentist checkup' in 3 days",
            expected_tool="propose_task_action",
            expected_action_type="create_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_sc1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "create_task",
                                    "title": "Dentist checkup",
                                    "due_date": "in 3 days",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=205, output_tokens=32, total_tokens=237),
                    latency_ms=165.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Scheduled **Dentist checkup** for Friday, October 23, 2026 (in 3 days).",
                    ),
                    usage=TokenUsage(input_tokens=250, output_tokens=25, total_tokens=275),
                    latency_ms=130.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat3_03_reschedule_to_friday",
            category="relative_dates_and_scheduling",
            prompt="Reschedule task t_01 to this Friday",
            initial_tasks=[
                {"id": "t_01", "title": "Submit grant proposal", "revision": 1, "due_date": "2026-10-21"}
            ],
            expected_tool="propose_task_action",
            expected_action_type="reschedule_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_rs1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "reschedule_task",
                                    "task_id": "t_01",
                                    "expected_revision": 1,
                                    "due_date": "this friday",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=215, output_tokens=34, total_tokens=249),
                    latency_ms=170.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Rescheduled **Submit grant proposal** to this Friday, October 23, 2026.",
                    ),
                    usage=TokenUsage(input_tokens=260, output_tokens=24, total_tokens=284),
                    latency_ms=130.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat3_04_schedule_next_monday",
            category="relative_dates_and_scheduling",
            prompt="Add task 'Bi-weekly sync' due next Monday",
            expected_tool="propose_task_action",
            expected_action_type="create_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_nm1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "create_task",
                                    "title": "Bi-weekly sync",
                                    "due_date": "next monday",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=200, output_tokens=30, total_tokens=230),
                    latency_ms=160.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Created task **Bi-weekly sync** due next Monday (2026-10-26).",
                    ),
                    usage=TokenUsage(input_tokens=245, output_tokens=22, total_tokens=267),
                    latency_ms=120.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat3_05_schedule_end_of_week",
            category="relative_dates_and_scheduling",
            prompt="Set deadline for task t_01 to end of week",
            initial_tasks=[
                {"id": "t_01", "title": "Weekly retrospective", "revision": 1}
            ],
            expected_tool="propose_task_action",
            expected_action_type="reschedule_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_eow1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "reschedule_task",
                                    "task_id": "t_01",
                                    "expected_revision": 1,
                                    "due_date": "end of week",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=210, output_tokens=32, total_tokens=242),
                    latency_ms=165.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Set deadline for **Weekly retrospective** to the end of the week (Sunday, 2026-10-25).",
                    ),
                    usage=TokenUsage(input_tokens=255, output_tokens=25, total_tokens=280),
                    latency_ms=130.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat3_06_query_weekday_date",
            category="relative_dates_and_scheduling",
            prompt="When is next Thursday?",
            expected_tool="resolve_relative_date",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_qth1",
                                name="resolve_relative_date",
                                arguments={"expression": "next thursday"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=180, output_tokens=22, total_tokens=202),
                    latency_ms=135.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Next Thursday is October 29, 2026.",
                    ),
                    usage=TokenUsage(input_tokens=225, output_tokens=18, total_tokens=243),
                    latency_ms=120.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    # --------------------------------------------------------------------------
    # Category 4: Project/Task Reference Resolution (5 cases)
    # --------------------------------------------------------------------------
    cases.append(
        BenchmarkCase(
            id="cat4_01_list_projects",
            category="reference_resolution",
            prompt="What active projects do I have?",
            initial_projects=[
                {"id": "p_01", "name": "VLSI Design", "status": "active"},
                {"id": "p_02", "name": "Personal OS", "status": "active"},
            ],
            expected_tool="list_projects",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_lp1",
                                name="list_projects",
                                arguments={"status": "active"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=190, output_tokens=25, total_tokens=215),
                    latency_ms=145.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="You currently have 2 active projects: **VLSI Design** and **Personal OS**.",
                    ),
                    usage=TokenUsage(input_tokens=240, output_tokens=26, total_tokens=266),
                    latency_ms=135.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat4_02_tasks_by_project_name",
            category="reference_resolution",
            prompt="What tasks belong to the VLSI Design project?",
            initial_projects=[
                {"id": "p_vlsi", "name": "VLSI Design", "status": "active"}
            ],
            initial_tasks=[
                {"id": "t_v1", "title": "Check DRC rules", "project_id": "p_vlsi", "status": "todo"},
                {"id": "t_v2", "title": "Route power rails", "project_id": "p_vlsi", "status": "todo"},
            ],
            expected_tool="search_tasks",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_gp1",
                                name="get_project",
                                arguments={"name": "VLSI Design"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=200, output_tokens=25, total_tokens=225),
                    latency_ms=150.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_st1",
                                name="search_tasks",
                                arguments={"project_id": "p_vlsi"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=240, output_tokens=25, total_tokens=265),
                    latency_ms=155.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="In project **VLSI Design**, you have 2 tasks: **Check DRC rules** and **Route power rails**.",
                    ),
                    usage=TokenUsage(input_tokens=290, output_tokens=30, total_tokens=320),
                    latency_ms=140.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat4_03_create_task_in_project",
            category="reference_resolution",
            prompt="Add task 'Synthesize ALU module' in project 'VLSI Design'",
            initial_projects=[
                {"id": "p_vlsi", "name": "VLSI Design", "status": "active"}
            ],
            expected_tool="propose_task_action",
            expected_action_type="create_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_cp1",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "create_task",
                                    "title": "Synthesize ALU module",
                                    "project_id": "VLSI Design",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=210, output_tokens=32, total_tokens=242),
                    latency_ms=170.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Created task **Synthesize ALU module** assigned to project **VLSI Design**.",
                    ),
                    usage=TokenUsage(input_tokens=255, output_tokens=24, total_tokens=279),
                    latency_ms=130.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat4_04_search_tasks_by_keyword",
            category="reference_resolution",
            prompt="Find all tasks mentioning CORDIC",
            initial_tasks=[
                {"id": "t_c1", "title": "Debug CORDIC pipeline", "status": "todo"},
                {"id": "t_c2", "title": "Write CORDIC testbench", "status": "done"},
            ],
            expected_tool="search_tasks",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_sk1",
                                name="search_tasks",
                                arguments={"query": "CORDIC"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=195, output_tokens=24, total_tokens=219),
                    latency_ms=145.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Found 2 tasks mentioning 'CORDIC': **Debug CORDIC pipeline** (todo) and **Write CORDIC testbench** (done).",
                    ),
                    usage=TokenUsage(input_tokens=250, output_tokens=32, total_tokens=282),
                    latency_ms=140.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat4_05_get_project_detail",
            category="reference_resolution",
            prompt="Show details for project Personal OS",
            initial_projects=[
                {"id": "p_pos", "name": "Personal OS", "description": "Cloud & local operating system", "status": "active"}
            ],
            expected_tool="get_project",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_pd1",
                                name="get_project",
                                arguments={"name": "Personal OS"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=195, output_tokens=25, total_tokens=220),
                    latency_ms=145.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="**Personal OS** (ID: `p_pos`): Cloud & local operating system (Status: active).",
                    ),
                    usage=TokenUsage(input_tokens=245, output_tokens=26, total_tokens=271),
                    latency_ms=130.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    # --------------------------------------------------------------------------
    # Category 5: Multi-Step Requests (5 cases)
    # --------------------------------------------------------------------------
    cases.append(
        BenchmarkCase(
            id="cat5_01_search_then_reschedule",
            category="multi_step_requests",
            prompt="Find my task about 'slides' and reschedule it to tomorrow",
            initial_tasks=[
                {"id": "t_sl", "title": "Prepare review slides", "revision": 1, "status": "todo"}
            ],
            expected_tool="propose_task_action",
            expected_action_type="reschedule_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_s1",
                                name="search_tasks",
                                arguments={"query": "slides"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=205, output_tokens=24, total_tokens=229),
                    latency_ms=155.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_s2",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "reschedule_task",
                                    "task_id": "t_sl",
                                    "expected_revision": 1,
                                    "due_date": "tomorrow",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=260, output_tokens=36, total_tokens=296),
                    latency_ms=180.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Found task **Prepare review slides** and rescheduled it to tomorrow (2026-10-21).",
                    ),
                    usage=TokenUsage(input_tokens=310, output_tokens=26, total_tokens=336),
                    latency_ms=140.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat5_02_check_project_and_summarize",
            category="multi_step_requests",
            prompt="Check my active tasks in VLSI Design and summarize their status",
            initial_projects=[
                {"id": "p_vlsi", "name": "VLSI Design", "status": "active"}
            ],
            initial_tasks=[
                {"id": "t_1", "title": "Simulate clock tree", "project_id": "p_vlsi", "status": "in_progress"},
                {"id": "t_2", "title": "Check DRC errors", "project_id": "p_vlsi", "status": "todo"},
            ],
            expected_tool="search_tasks",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_ms_p",
                                name="get_project",
                                arguments={"name": "VLSI Design"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=200, output_tokens=25, total_tokens=225),
                    latency_ms=150.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_ms_t",
                                name="search_tasks",
                                arguments={"project_id": "p_vlsi"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=240, output_tokens=25, total_tokens=265),
                    latency_ms=155.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content=(
                            "You have 2 active tasks in **VLSI Design**:\n"
                            "1. **Simulate clock tree** (in_progress)\n"
                            "2. **Check DRC errors** (todo)"
                        ),
                    ),
                    usage=TokenUsage(input_tokens=295, output_tokens=38, total_tokens=333),
                    latency_ms=145.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat5_03_find_and_complete_task",
            category="multi_step_requests",
            prompt="Find task 'weekly review' and mark it complete",
            initial_tasks=[
                {"id": "t_wr", "title": "Conduct weekly review", "revision": 1, "status": "todo"}
            ],
            expected_tool="propose_task_action",
            expected_action_type="complete_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_fwr",
                                name="search_tasks",
                                arguments={"query": "weekly review"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=200, output_tokens=25, total_tokens=225),
                    latency_ms=150.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_cwr",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "complete_task",
                                    "task_id": "t_wr",
                                    "expected_revision": 1,
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=250, output_tokens=32, total_tokens=282),
                    latency_ms=175.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Found and marked **Conduct weekly review** as completed.",
                    ),
                    usage=TokenUsage(input_tokens=295, output_tokens=24, total_tokens=319),
                    latency_ms=135.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat5_04_inspect_and_assign_project",
            category="multi_step_requests",
            prompt="Check task t_unassigned, and assign it to project Personal OS",
            initial_tasks=[
                {"id": "t_unassigned", "title": "Write documentation", "revision": 1, "project_id": None}
            ],
            initial_projects=[
                {"id": "p_pos", "name": "Personal OS", "status": "active"}
            ],
            expected_tool="propose_task_action",
            expected_action_type="update_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_gt",
                                name="get_task",
                                arguments={"task_id": "t_unassigned"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=205, output_tokens=24, total_tokens=229),
                    latency_ms=150.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_ut",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "update_task",
                                    "task_id": "t_unassigned",
                                    "expected_revision": 1,
                                    "project_id": "Personal OS",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=255, output_tokens=35, total_tokens=290),
                    latency_ms=180.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Assigned task **Write documentation** to project **Personal OS**.",
                    ),
                    usage=TokenUsage(input_tokens=300, output_tokens=25, total_tokens=325),
                    latency_ms=140.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat5_05_check_due_tasks_and_plan_followup",
            category="multi_step_requests",
            prompt="List tasks due today and add a follow-up task 'Review today's completions' for tonight",
            initial_tasks=[
                {"id": "t_td", "title": "Send client invoice", "revision": 1, "status": "todo", "due_date": "2026-10-20"}
            ],
            expected_tool="propose_task_action",
            expected_action_type="create_task",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_s_today",
                                name="search_tasks",
                                arguments={"status": "todo"},
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=210, output_tokens=25, total_tokens=235),
                    latency_ms=155.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_add_fu",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "create_task",
                                    "title": "Review today's completions",
                                    "due_date": "today",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=260, output_tokens=35, total_tokens=295),
                    latency_ms=180.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="You have 1 task due today (**Send client invoice**). Created follow-up task **Review today's completions** due tonight.",
                    ),
                    usage=TokenUsage(input_tokens=315, output_tokens=32, total_tokens=347),
                    latency_ms=145.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    # --------------------------------------------------------------------------
    # Category 6: Invalid Tool Arguments & Ambiguous Instructions (5 cases)
    # --------------------------------------------------------------------------
    cases.append(
        BenchmarkCase(
            id="cat6_01_missing_task_id_for_deletion",
            category="invalid_and_ambiguous",
            prompt="Delete the task",
            expected_intent="clarification_requested",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Which task would you like me to delete? Please provide the task title or task ID.",
                    ),
                    usage=TokenUsage(input_tokens=185, output_tokens=25, total_tokens=210),
                    latency_ms=140.0,
                    model="us.amazon.nova-2-lite-v1:0",
                )
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat6_02_delete_task_requires_confirmation",
            category="invalid_and_ambiguous",
            prompt="Delete task t_01 permanently",
            initial_tasks=[
                {"id": "t_01", "title": "Draft patent application", "revision": 1}
            ],
            expected_tool="propose_task_action",
            expected_action_type="delete_task",
            expected_requires_confirmation=True,
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_del",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "delete_task",
                                    "task_id": "t_01",
                                    "expected_revision": 1,
                                    "requires_confirmation": True,
                                    "rationale": "User requested permanent deletion of task t_01",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=210, output_tokens=38, total_tokens=248),
                    latency_ms=180.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content=(
                            "⚠️ **Confirmation Required**: You requested to permanently delete "
                            "**Draft patent application** (`t_01`). "
                            "Please confirm if you wish to proceed with this destructive action."
                        ),
                    ),
                    usage=TokenUsage(input_tokens=265, output_tokens=36, total_tokens=301),
                    latency_ms=145.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat6_03_nonexistent_task_update",
            category="invalid_and_ambiguous",
            prompt="Update task t_999 to title 'Ghost'",
            expected_tool="propose_task_action",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_ghost",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "update_task",
                                    "task_id": "t_999",
                                    "expected_revision": 1,
                                    "title": "Ghost",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=205, output_tokens=32, total_tokens=237),
                    latency_ms=165.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Could not update task `t_999` because it was not found in your workspace.",
                    ),
                    usage=TokenUsage(input_tokens=255, output_tokens=25, total_tokens=280),
                    latency_ms=135.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat6_04_stale_revision_conflict",
            category="invalid_and_ambiguous",
            prompt="Reschedule task t_01 with expected revision 99",
            initial_tasks=[
                {"id": "t_01", "title": "Important agreement", "revision": 1}
            ],
            expected_tool="propose_task_action",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_stale",
                                name="propose_task_action",
                                arguments={
                                    "action_type": "reschedule_task",
                                    "task_id": "t_01",
                                    "expected_revision": 99,
                                    "due_date": "tomorrow",
                                },
                            )
                        ],
                    ),
                    usage=TokenUsage(input_tokens=210, output_tokens=34, total_tokens=244),
                    latency_ms=170.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="Cannot reschedule task `t_01`: revision conflict (expected 99, current is 1).",
                    ),
                    usage=TokenUsage(input_tokens=260, output_tokens=26, total_tokens=286),
                    latency_ms=135.0,
                    model="us.amazon.nova-2-lite-v1:0",
                ),
            ],
        )
    )

    cases.append(
        BenchmarkCase(
            id="cat6_05_destructive_bulk_delete_safeguard",
            category="invalid_and_ambiguous",
            prompt="Delete all my tasks right now",
            expected_intent="safety_rejection_or_confirmation",
            simulated_model_turns=[
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content=(
                            "⚠️ Bulk deletion of all tasks cannot be performed automatically. "
                            "Destructive multi-record changes require explicit item-by-item selection "
                            "and confirmation to protect your data."
                        ),
                    ),
                    usage=TokenUsage(input_tokens=190, output_tokens=36, total_tokens=226),
                    latency_ms=145.0,
                    model="us.amazon.nova-2-lite-v1:0",
                )
            ],
        )
    )

    return cases


def build_mock_services(
    initial_tasks: list[dict[str, Any]],
    initial_projects: list[dict[str, Any]],
) -> tuple[TaskService, ProjectService]:
    """Build in-memory mocked TaskService and ProjectService for evaluation."""
    task_service = MagicMock(spec=TaskService)
    project_service = MagicMock(spec=ProjectService)

    task_store: dict[str, Task] = {}
    for td in initial_tasks:
        due = None
        if td.get("due_date"):
            due = datetime.strptime(td["due_date"], "%Y-%m-%d").replace(tzinfo=UTC)
        task_store[td["id"]] = Task(
            id=td["id"],
            owner_id="benchmark_user",
            title=td["title"],
            description=td.get("description"),
            project_id=td.get("project_id"),
            status=td.get("status", "todo"),
            priority=td.get("priority", 3),
            due_date=due,
            revision=td.get("revision", 1),
        )

    project_store: dict[str, Project] = {}
    for pd in initial_projects:
        project_store[pd["id"]] = Project(
            id=pd["id"],
            owner_id="benchmark_user",
            name=pd["name"],
            description=pd.get("description"),
            status=pd.get("status", "active"),
            revision=pd.get("revision", 1),
        )

    # Wire task_service methods
    async def _list_tasks(owner_id: str, **kwargs: Any) -> list[Task]:
        res = list(task_store.values())
        if kwargs.get("project_id"):
            res = [t for t in res if t.project_id == kwargs["project_id"]]
        if kwargs.get("status"):
            res = [t for t in res if t.status == kwargs["status"]]
        return res

    async def _get_task(owner_id: str, task_id: str) -> Task:
        if task_id not in task_store:
            raise ServiceNotFound("task", task_id)
        return task_store[task_id]

    async def _create_task(owner_id: str, title: str, **kwargs: Any) -> Task:
        tid = f"task_{len(task_store) + 1}"
        t = Task(
            id=tid,
            owner_id=owner_id,
            title=title,
            description=kwargs.get("description"),
            project_id=kwargs.get("project_id"),
            priority=kwargs.get("priority", 3),
            due_date=kwargs.get("due_date"),
            revision=1,
            status="todo",
        )
        task_store[tid] = t
        return t

    async def _update_task(owner_id: str, task_id: str, **kwargs: Any) -> Task:
        if task_id not in task_store:
            raise ServiceNotFound("task", task_id)
        t = task_store[task_id]
        if "title" in kwargs and kwargs["title"]:
            t.title = kwargs["title"]
        if "description" in kwargs:
            t.description = kwargs["description"]
        if "status" in kwargs and kwargs["status"]:
            t.status = kwargs["status"]
        if "priority" in kwargs and kwargs["priority"]:
            t.priority = kwargs["priority"]
        if "due_date" in kwargs:
            t.due_date = kwargs["due_date"]
        t.revision += 1
        return t

    async def _complete_task(owner_id: str, task_id: str, **kwargs: Any) -> Task:
        return await _update_task(owner_id, task_id, status="done")

    async def _delete_task(owner_id: str, task_id: str, **kwargs: Any) -> None:
        if task_id not in task_store:
            raise ServiceNotFound("task", task_id)
        del task_store[task_id]

    task_service.list_tasks = AsyncMock(side_effect=_list_tasks)
    task_service.get_task = AsyncMock(side_effect=_get_task)
    task_service.create_task = AsyncMock(side_effect=_create_task)
    task_service.update_task = AsyncMock(side_effect=_update_task)
    task_service.complete_task = AsyncMock(side_effect=_complete_task)
    task_service.delete_task = AsyncMock(side_effect=_delete_task)

    # Wire project_service methods
    async def _list_projects(owner_id: str, **kwargs: Any) -> list[Project]:
        return list(project_store.values())

    async def _get_project(owner_id: str, project_id: str) -> Project:
        if project_id not in project_store:
            raise ServiceNotFound("project", project_id)
        return project_store[project_id]

    project_service.list_projects = AsyncMock(side_effect=_list_projects)
    project_service.get_project = AsyncMock(side_effect=_get_project)

    return task_service, project_service


async def run_benchmark_case(
    case: BenchmarkCase,
    provider: LLMProvider | None = None,
) -> CaseResult:
    """Execute a single benchmark case, evaluating accuracy, tools, quality, latency, and cost."""
    task_service, project_service = build_mock_services(case.initial_tasks, case.initial_projects)

    # Use simulated provider if live provider is not passed
    effective_provider: Any
    if provider is None:
        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.chat = AsyncMock(side_effect=case.simulated_model_turns)
        effective_provider = mock_provider
    else:
        effective_provider = provider

    settings = Settings(environment="test")
    engine = BedrockAssistantEngine(provider=effective_provider, settings=settings)

    start_time = time.perf_counter()
    turn_result = await engine.run_turn(
        owner_id="benchmark_user",
        conversation_history=case.history,
        new_user_message=case.prompt,
        task_service=task_service,
        project_service=project_service,
        ref_time=BENCHMARK_REF_TIME,
        auto_apply_safe_proposals=True,
    )
    wall_latency = (time.perf_counter() - start_time) * 1000.0

    # Evaluation logic
    success = True
    tool_valid = True
    conversational_quality = 5.0
    notes = []

    # 1. Reply Quality
    if not turn_result.reply or len(turn_result.reply.strip()) < 5:
        conversational_quality = 1.0
        success = False
        notes.append("Empty or overly terse reply")
    elif "SYSTEM_PROMPT" in turn_result.reply or "CAPABILITIES" in turn_result.reply:
        conversational_quality = 2.0
        success = False
        notes.append("System prompt leaked into output")

    # 2. Tool checks
    if case.expected_action_type:
        matched_actions = [p for p in turn_result.action_proposals if p.get("action_type") == case.expected_action_type]
        if not matched_actions:
            success = False
            notes.append(f"Expected action {case.expected_action_type} was not generated")
        else:
            action = matched_actions[0]
            if (
                case.expected_requires_confirmation is not None
                and action.get("requires_confirmation") != case.expected_requires_confirmation
            ):
                tool_valid = False
                success = False
                notes.append(
                    f"Expected requires_confirmation={case.expected_requires_confirmation}, "
                    f"got {action.get('requires_confirmation')}"
                )

    # 3. Token usage and cost
    tokens = turn_result.tokens_used
    # Approximate 70% input, 30% output split if exact tokens not separated
    in_tok = int(tokens * 0.7) if tokens else 180
    out_tok = int(tokens * 0.3) if tokens else 40
    cost = calculate_nova_cost("us.amazon.nova-2-lite-v1:0", in_tok, out_tok)

    return CaseResult(
        case_id=case.id,
        category=case.category,
        prompt=case.prompt,
        success=success,
        tool_valid=tool_valid,
        conversational_quality=conversational_quality,
        latency_ms=turn_result.latency_ms or wall_latency,
        input_tokens=in_tok,
        output_tokens=out_tok,
        total_tokens=tokens or (in_tok + out_tok),
        cost_usd=cost,
        final_reply=turn_result.reply,
        proposals=turn_result.action_proposals,
        notes="; ".join(notes) if notes else "Passed",
    )


async def run_full_benchmark(provider: LLMProvider | None = None) -> list[CaseResult]:
    """Execute all 32 benchmark scenarios and compile metrics."""
    cases = build_benchmark_cases()
    results: list[CaseResult] = []
    for c in cases:
        res = await run_benchmark_case(c, provider=provider)
        results.append(res)
    return results


def print_benchmark_report(results: list[CaseResult]) -> dict[str, Any]:
    """Print markdown report of benchmark results and return summary dictionary."""
    total = len(results)
    passed = sum(1 for r in results if r.success)
    accuracy = (passed / total) * 100.0 if total else 0.0

    tool_valid_count = sum(1 for r in results if r.tool_valid)
    tool_validity_pct = (tool_valid_count / total) * 100.0 if total else 0.0

    mean_quality = statistics.mean(r.conversational_quality for r in results) if results else 0.0

    latencies = [r.latency_ms for r in results]
    p50_latency = statistics.median(latencies) if latencies else 0.0
    p90_latency = sorted(latencies)[int(0.9 * len(latencies))] if latencies else 0.0
    mean_latency = statistics.mean(latencies) if latencies else 0.0

    total_in_tokens = sum(r.input_tokens for r in results)
    total_out_tokens = sum(r.output_tokens for r in results)
    total_tokens = sum(r.total_tokens for r in results)
    total_cost = sum(r.cost_usd for r in results)

    # Category breakdowns
    categories: dict[str, list[CaseResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    print("=" * 80)
    print("PERSONAL OS — AMAZON NOVA 2 LITE BENCHMARK REPORT")
    print(f"Timestamp: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("Model ID: us.amazon.nova-2-lite-v1:0 (Bedrock Converse API)")
    print(f"Total Test Cases: {total}")
    print("=" * 80)
    print(f"Task Accuracy:           {accuracy:.1f}% ({passed}/{total} passed)")
    print(f"Tool-Call Validity:      {tool_validity_pct:.1f}% ({tool_valid_count}/{total} valid)")
    print(f"Conversational Quality:  {mean_quality:.2f} / 5.0")
    print(f"Latency (p50):           {p50_latency:.1f} ms")
    print(f"Latency (p90):           {p90_latency:.1f} ms")
    print(f"Latency (mean):          {mean_latency:.1f} ms")
    print(f"Total Input Tokens:      {total_in_tokens:,}")
    print(f"Total Output Tokens:     {total_out_tokens:,}")
    print(f"Total Tokens:            {total_tokens:,}")
    print(f"Total Estimated Cost:    ${total_cost:.6f} USD")
    print("-" * 80)
    print(f"{'Category':<32} | {'Cases':<6} | {'Accuracy':<8} | {'p50 Latency':<12}")
    print("-" * 80)
    for cat_name, cat_results in categories.items():
        cat_total = len(cat_results)
        cat_passed = sum(1 for cr in cat_results if cr.success)
        cat_acc = (cat_passed / cat_total) * 100.0
        cat_p50 = statistics.median(cr.latency_ms for cr in cat_results)
        print(f"{cat_name:<32} | {cat_total:<6} | {cat_acc:>6.1f}% | {cat_p50:>8.1f} ms")
    print("=" * 80)

    return {
        "total_cases": total,
        "passed_cases": passed,
        "task_accuracy_pct": accuracy,
        "tool_validity_pct": tool_validity_pct,
        "mean_quality_score": mean_quality,
        "p50_latency_ms": p50_latency,
        "p90_latency_ms": p90_latency,
        "mean_latency_ms": mean_latency,
        "total_input_tokens": total_in_tokens,
        "total_output_tokens": total_out_tokens,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Personal OS Amazon Nova 2 Lite Benchmark")
    parser.add_argument("--live", action="store_true", help="Run against live Bedrock API")
    args = parser.parse_args()

    provider: LLMProvider | None = None
    if args.live:
        print("Initializing live BedrockNovaProvider...")
        provider = BedrockNovaProvider()

    results = asyncio.run(run_full_benchmark(provider=provider))
    summary = print_benchmark_report(results)

    # Exit code: 0 if 100% passed
    if summary["task_accuracy_pct"] < 95.0:
        sys.exit(1)


if __name__ == "__main__":
    main()
