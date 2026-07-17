"""MarkdownRenderer — reusable, embeddable markdown component.

Placeholder implementation. This milestone defines the INTERFACE future
workspaces will use to render markdown (a document path or raw text); it does
NOT implement a real rendering engine. Real rendering arrives in a later
milestone by overriding the render path only — no consumer changes needed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.widgets import Static

from personal_os.tui.components.base import Component


class MarkdownRenderer(Component):
    """Renders markdown from a source path or raw string (placeholder)."""

    component_id = "markdown-renderer"
    title = "Markdown"

    def __init__(self, *, source: Optional[Path] = None, text: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._source = source
        self._text = text

    # ----- public interface (stable across future real implementation) -----

    def load_path(self, path: Path) -> None:
        """Point the renderer at a markdown file."""
        self._source = path
        self._text = None
        self._render_placeholder()

    def load_text(self, text: str) -> None:
        """Render raw markdown text."""
        self._text = text
        self._source = None
        self._render_placeholder()

    # ----- placeholder rendering -----

    def compose(self):
        yield Static(self._current_placeholder(), id="md-body", classes="component-placeholder")

    def _current_placeholder(self) -> str:
        if self._source is not None:
            return f"[Markdown] would render file: {self._source.name} (placeholder)"
        if self._text is not None:
            preview = self._text.strip().splitlines()[0] if self._text.strip() else ""
            return f"[Markdown] would render text: {preview[:60]} (placeholder)"
        return "[Markdown] (no source) (placeholder)"

    def _render_placeholder(self) -> None:
        try:
            self.query_one("#md-body", Static).update(self._current_placeholder())
        except Exception:
            # Not mounted yet; compose() will pick up current state.
            pass
