"""Deterministic mapping from ResolvedExtraction to VaultAction.

ActionPlanner translates semantic meaning (from the LLM-produced ResolvedExtraction)
into a concrete vault mutation (VaultAction). It contains no LLM calls, no I/O,
and no vault access. Given the same inputs it always returns the same output.

This is the critical boundary that prevents LLM output from directly
determining filesystem writes. The LLM reasons about intent; ActionPlanner
decides what typed action that intent implies; KnowledgeApplier does the write.

Certainty-based demotion:
    TASK with user certainty < TASK_CERTAINTY_THRESHOLD → CreatePossibleTask
    PROGRESS with user certainty < PROGRESS_CERTAINTY_THRESHOLD → RecordActivity

    This handles natural speech like "I should probably..." correctly without
    relying on keyword matching. The LLM classifies type; certainty controls promotion.
"""
from __future__ import annotations

import re
from typing import Optional

from personal_os.core.extraction.models import ExtractionType, ResolvedExtraction
from personal_os.core.extraction.actions import (
    AddDecision,
    AppendLearning,
    AppendNote,
    CreatePossibleTask,
    CreateTask,
    MarkComplete,
    NoOp,
    RecordActivity,
    RecordProblem,
    RecordProgress,
    VaultAction,
)

# Certainty threshold: TASK below this → CreatePossibleTask
TASK_CERTAINTY_THRESHOLD = 0.60
# Certainty threshold: PROGRESS below this → RecordActivity
PROGRESS_CERTAINTY_THRESHOLD = 0.40


class ActionPlanner:
    """Deterministic, stateless mapping from ResolvedExtraction → VaultAction."""

    _SKILL_KEYWORDS: dict[str, list[str]] = {
        "VLSI": [
            "verilog", "systemverilog", "rtl", "vlsi", "asic", "fpga",
            "timing", "synthesis", "place and route", "cordic", "ic design",
            "chip", "layout", "parasitic", "drc", "lvs", "pdk", "cadence",
            "spice", "netlist", "floorplan",
        ],
        "Embedded": [
            "embedded", "stm32", "esp32", "esp8266", "microcontroller",
            "rtos", "freertos", "firmware", "uart", "spi", "i2c", "pwm",
            "interrupt", "hal", "drone", "mcu", "baremetal", "bootloader",
        ],
        "Linux": [
            "linux", "bash", "shell", "nix", "nixos", "docker", "kernel",
            "systemd", "cgroup", "namespace", "iptables", "socket", "posix",
            "daemon", "service", "container",
        ],
        "Software": [
            "python", "javascript", "typescript", "react", "api", "database",
            "rust", "golang", "java", "sql", "backend", "frontend", "web",
            "server", "client", "algorithm", "data structure", "compiler",
            "parser", "concurrency", "async",
        ],
        "Mathematics": [
            "math", "calculus", "linear algebra", "signal processing", "dsp",
            "control theory", "statistics", "probability", "fourier", "laplace",
            "matrix", "eigenvalue", "integral", "differential equation",
            "optimization", "convex",
        ],
    }

    _HOURS_RE = re.compile(
        r'(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h\b)',
        re.IGNORECASE,
    )

    def plan(
        self,
        extraction: ResolvedExtraction,
        source: str = "Text",
        capture_id: str = "",
    ) -> VaultAction:
        """Map one ResolvedExtraction to the appropriate VaultAction.

        Args:
            extraction: Fully resolved extraction from the pipeline.
            source: Origin label ("Text", "Voice", etc.).
            capture_id: ULID of the originating capture for traceability.
        """
        t = extraction.type
        certainty = extraction.certainty

        if t == ExtractionType.TASK:
            # Demote uncertain task language to possible task.
            if certainty < TASK_CERTAINTY_THRESHOLD:
                return CreatePossibleTask(
                    title=extraction.description,
                    project=extraction.project,
                    source=source,
                    capture_id=capture_id,
                )
            return CreateTask(
                title=extraction.description,
                project=extraction.project,
                deadline=extraction.deadline,
                source=source,
                capture_id=capture_id,
            )

        if t == ExtractionType.POSSIBLE_TASK:
            return CreatePossibleTask(
                title=extraction.description,
                project=extraction.project,
                source=source,
                capture_id=capture_id,
            )

        if t == ExtractionType.PROGRESS:
            if extraction.project is None:
                return NoOp(reason="progress with no resolved project — Daily Stream is sufficient")
            if certainty < PROGRESS_CERTAINTY_THRESHOLD:
                # Uncertain progress ("I think it might be working") → activity
                return RecordActivity(
                    description=extraction.description,
                    project=extraction.project,
                    source=source,
                    capture_id=capture_id,
                )
            return RecordProgress(
                description=extraction.description,
                project=extraction.project,
                hours=self._extract_hours(extraction.description),
                source=source,
                capture_id=capture_id,
            )

        if t == ExtractionType.ACTIVITY:
            # project=None → NoOp (the Daily Stream already records this)
            if extraction.project is None:
                return NoOp(reason="activity with no resolved project — Daily Stream is sufficient")
            return RecordActivity(
                description=extraction.description,
                project=extraction.project,
                source=source,
                capture_id=capture_id,
            )

        if t == ExtractionType.DECISION:
            return AddDecision(
                decision=extraction.description,
                project=extraction.project,
                source=source,
                capture_id=capture_id,
            )

        if t == ExtractionType.COMPLETION:
            return MarkComplete(
                description=extraction.description,
                project=extraction.project,
                source=source,
                capture_id=capture_id,
            )

        if t == ExtractionType.LEARNING:
            return AppendLearning(
                topic=extraction.description,
                skill_area=self._classify_skill(extraction.description),
                source=source,
                capture_id=capture_id,
            )

        if t == ExtractionType.PROBLEM:
            # project=None → NoOp (KnowledgeApplier enforces this)
            return RecordProblem(
                description=extraction.description,
                project=extraction.project,
                source=source,
                capture_id=capture_id,
            )

        if t == ExtractionType.OBSERVATION:
            if extraction.project is None:
                return NoOp(reason="observation with no resolved project — Daily Stream is sufficient")
            return AppendNote(
                content=extraction.description,
                project=extraction.project,
                source=source,
                capture_id=capture_id,
            )

        # DEADLINE_CONTEXT, JOURNAL, or any unrecognised type:
        # The temporal mention or pure journal entry is already in the Daily Stream.
        return NoOp(reason=f"type={t.value} — Daily Stream is sufficient, no additional vault action")

    # ------------------------------------------------------------------ helpers

    def _extract_hours(self, text: str) -> Optional[float]:
        """Return the first hour value found in text, e.g. '2.5 hours' → 2.5."""
        m = self._HOURS_RE.search(text)
        return float(m.group(1)) if m else None

    def _classify_skill(self, text: str) -> str:
        """Return the best-matching skill area for a learning entry, or 'General'."""
        text_lower = text.lower()
        hits: dict[str, int] = {}
        for area, keywords in self._SKILL_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text_lower)
            if count:
                hits[area] = count
        return max(hits, key=lambda k: hits[k]) if hits else "General"
