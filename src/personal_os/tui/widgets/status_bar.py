"""StatusBar — persistent bottom bar, part of the application shell.

Displays: current mode, current workspace, background-processor status
(placeholder), and future shortcut hints. It is owned by the App shell, not by
any individual view. No final styling this milestone.
"""
from __future__ import annotations

from textual.widgets import Static


class StatusBar(Static):
    """Single-line status bar rendered at the bottom of the shell."""

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._mode = "Explore"
        self._workspace = "Today"
        self._processor = "idle"
        self._hints = ""
        self._refresh_text()

    def _refresh_text(self) -> None:
        left = f"{self._mode}  ·  {self._workspace}"
        mid = f"proc: {self._processor}"
        parts = [left, mid]
        if self._hints:
            parts.append(self._hints)
        self._text = "   ".join(parts)
        self.update(self._text)

    @property
    def text(self) -> str:
        return getattr(self, "_text", "")

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._refresh_text()

    def set_workspace(self, workspace: str) -> None:
        self._workspace = workspace
        self._refresh_text()

    def set_processor(self, status: str) -> None:
        self._processor = status
        self._refresh_text()

    def set_hints(self, hints: str) -> None:
        self._hints = hints
        self._refresh_text()
