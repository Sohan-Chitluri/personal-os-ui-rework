"""ExtractionContext — runtime context provided to the SemanticExtractor.

Separating this from models.py avoids circular imports and makes the
context's purpose explicit: it carries everything the LLM needs to interpret
a capture in relation to existing knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class ExtractionContext:
    """Context provided to SemanticExtractor for contextual interpretation.

    The LLM alone cannot reliably resolve temporal expressions ("tomorrow",
    "Thursday") or implicit references ("that scaling problem") without
    knowing the current date/time and recent capture history. This context
    supplies that information.

    Attributes:
        now: Current local datetime. REQUIRED for temporal normalization.
            "I'll finish it tomorrow" cannot be resolved without this.
        timezone: IANA timezone name (e.g. "Asia/Kolkata", "UTC").
        projects: Known vault project names. Supplied to the LLM so it can
            resolve "the CORDIC work" → "CORDIC Accelerator" correctly.
        recent_captures: Raw text of the last N captures. Enables contextual
            resolution: "that scaling problem" → CORDIC scaling issue from
            yesterday's capture.
        active_tasks: Titles of currently open tasks. Used for completion
            resolution: "I finished the synthesis" → match against open task.
        current_project: The project the user is most recently working on.
            Provides implicit project context when none is mentioned.
    """
    now: datetime
    timezone: str = "UTC"
    projects: list[str] = field(default_factory=list)
    recent_captures: list[str] = field(default_factory=list)
    active_tasks: list[str] = field(default_factory=list)
    current_project: Optional[str] = None

    def to_llm_dict(self) -> dict[str, Any]:
        """Serialize for injection into the LLM prompt."""
        return {
            "current_datetime": self.now.isoformat(timespec="seconds"),
            "current_date": self.now.strftime("%Y-%m-%d"),
            "current_day_of_week": self.now.strftime("%A"),
            "timezone": self.timezone,
            "known_projects": self.projects,
            "recent_captures": self.recent_captures[-5:],
            "active_tasks": self.active_tasks[:10],
            "current_project": self.current_project,
        }
