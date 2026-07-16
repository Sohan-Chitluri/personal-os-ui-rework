"""Project panel - interactive project tree with actions."""
from __future__ import annotations
from textual.widgets import Tree, Static
from textual.message import Message
from textual.binding import Binding

from personal_os.tui.widgets.panels.base import SidebarPanel


class ProjectSelected(Message):
    """Posted when a project is selected."""
    def __init__(self, project_name: str):
        super().__init__()
        self.project_name = project_name


class ProjectsPanel(SidebarPanel):
    """Interactive projects tree view."""
    
    BINDINGS = [
        Binding("enter", "select", "Open"),
        Binding("n", "new", "New Project"),
        Binding("a", "archive", "Archive"),
        Binding("r", "refresh", "Refresh"),
        Binding("e", "edit", "Edit"),
    ]
    
    def __init__(self, vault, **kwargs):
        super().__init__("Projects", "projects", vault=vault, **kwargs)
    
    def compose(self):
        yield Static("Projects", classes="panel-title")
        tree = Tree("Projects", id="projects-tree")
        tree.show_root = False
        yield tree
    
    async def refresh_content(self):
        tree = self.query_one("#projects-tree", Tree)
        tree.clear()
        
        projects = self.vault.list_projects()
        
        # Group by status
        groups = {}
        for p in projects:
            status = p.get('status', 'active')
            if status not in groups:
                groups[status] = []
            groups[status].append(p)
        
        status_icons = {"active": "🟢", "planning": "🟡", "blocked": "🔴", "archived": "⚪"}
        status_order = ["active", "planning", "blocked", "archived"]
        
        for status in status_order:
            if status in groups and groups[status]:
                icon = status_icons.get(status, "⚪")
                status_node = tree.root.add(f"{icon} {status.title()} ({len(groups[status])})")
                status_node.expand()
                for p in groups[status]:
                    project_node = status_node.add_leaf(f"📁 {p['name']}")
                    project_node.data = {"type": "project", "name": p['name']}
    
    def on_tree_node_selected(self, event: Tree.NodeSelected):
        """Handle project selection."""
        if event.node.data and event.node.data.get("type") == "project":
            self.post_message(ProjectSelected(event.node.data["name"]))
    
    def action_select(self):
        """Open selected project."""
        tree = self.query_one("#projects-tree", Tree)
        if tree.cursor_node and tree.cursor_node.data:
            data = tree.cursor_node.data
            if data.get("type") == "project":
                self.post_message(ProjectSelected(data["name"]))
    
    def action_new(self):
        """Create new project - will be handled by app."""
        self.app.post_message(self.app.NewProjectRequested())
    
    def action_archive(self):
        """Archive selected project."""
        tree = self.query_one("#projects-tree", Tree)
        if tree.cursor_node and tree.cursor_node.data:
            data = tree.cursor_node.data
            if data.get("type") == "project":
                self.app.post_message(self.app.ArchiveProjectRequested(data["name"]))
    
    def action_refresh(self):
        """Refresh project list."""
        self.app.run_worker(self.refresh_content())
    
    def action_edit(self):
        """Edit selected project."""
        tree = self.query_one("#projects-tree", Tree)
        if tree.cursor_node and tree.cursor_node.data:
            data = tree.cursor_node.data
            if data.get("type") == "project":
                self.app.post_message(self.app.EditProjectRequested(data["name"]))