"""Shared tag/project → color mapping so badges are consistent across panels.

Mirrors the color-coded project badges from the design reference (e.g.
"Maestro" always cyan, "Personal OS" always purple, "VLSI" always pink).
"""
from __future__ import annotations

_PALETTE = ["#7dd3fc", "#a78bfa", "#f472b6", "#4ade80", "#fb923c", "#60a5fa", "#fbbf24"]


def color_for(tag: str) -> str:
    """Deterministically map a tag/project name to a stable accent color."""
    if not tag:
        return "#94a3b8"
    digest = sum(ord(c) * (i + 1) for i, c in enumerate(tag))
    return _PALETTE[digest % len(_PALETTE)]
