"""StatusBar — persistent bottom bar, part of the application shell.

Displays: current mode, current workspace, background-processor status,
keyboard hints. It is owned by the App shell, not by any individual view.
"""
from __future__ import annotations

from textual.widgets import Static


class StatusBar(Static):
    """Single-line status bar rendered at the bottom of the shell."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._mode = "Explore"
        self._workspace = "Today"
        self._processor = "idle"
        self._refresh_text()

    def _refresh_text(self) -> None:
        # Left: mode · workspace
        left = f" [bold #a78bfa]{self._mode}[/]  ·  [#7dd3fc]{self._workspace}[/] "
        # Right: processor status
        right = f" proc: {self._processor} "
        # Keyboard hints — bold accent keys, muted labels (matches footer style)
        hint_pairs = [
            ("Ctrl+B", "Sidebar"),
            ("Space", "Capture"),
            ("Tab", "Next View"),
            ("/", "Search"),
            ("?", "Help"),
            ("Q", "Quit"),
        ]
        hints = "  ".join(f"[bold #7dd3fc]{k}[/] [dim]{v}[/]" for k, v in hint_pairs)
        self._text = f"{left} {hints}   {right}"
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