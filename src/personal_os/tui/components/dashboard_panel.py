"""DashboardPanel — reusable, embeddable dashboard component.

Placeholder implementation. Defines the embeddable surface a workspace uses to
show at-a-glance metrics/tiles. No real data this milestone (per scope: no
dashboards backed by real data).
"""
from __future__ import annotations

from textual.widgets import Static

from personal_os.tui.components.base import Component


class DashboardPanel(Component):
    """An embeddable dashboard surface (placeholder)."""

    component_id = "dashboard-panel"
    title = "Dashboard"

    def compose(self):
        yield Static("[Dashboard] embeddable component (placeholder)", classes="component-placeholder")
