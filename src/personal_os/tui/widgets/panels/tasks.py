"""Tasks panel - interactive unified task list."""
from __future__ import annotations
from textual.widgets import ListView, ListItem, Label, Static
from textual.message import Message
from textual.binding import Binding

from personal_os.tui.widgets.panels.base import SidebarPanel


class TaskSelected(Message):
    """Posted when a task is selected."""
    def __init__(self, project: str, task_text: str, done: bool, line: int):
        super().__init__()
        self.project = project
        self.task_text = task_text
        self.done = done
        self.line = line


class TasksPanel(SidebarPanel):
    """Interactive unified tasks view."""
    
    BINDINGS = [
        Binding("x", "toggle", "Toggle"),
        Binding("enter", "select", "Open"),
        Binding("n", "new", "New Task"),
        Binding("d", "due", "Set Due"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open_project", "Open Project"),
    ]
    
    def __init__(self, vault, **kwargs):
        super().__init__("Tasks", "tasks", vault=vault, **kwargs)
    
    def compose(self):
        yield Static("Tasks", classes="panel-title")
        yield ListView(id="tasks-list")
    
    async def refresh_content(self):
        list_view = self.query_one("#tasks-list", ListView)
        list_view.clear()
        
        projects = self.vault.list_projects()
        
        for proj in projects:
            project_name = proj['name']
            tasks_file = self.vault.config.projects_dir / project_name / "Tasks.md"
            if not tasks_file.exists():
                continue
            
            content = self.vault.read_file(tasks_file)
            lines = content.split('\n')
            
            # Add project header
            header_item = ListItem(Label(f"📁 {project_name} ({proj.get('open_tasks', 0)} open)", classes="task-header"))
            header_item.data = {"type": "header", "project": project_name}
            await list_view.append(header_item)
            
            current_section = ""
            for i, line in enumerate(lines):
                if line.startswith("## "):
                    current_section = line[3:].strip()
                    section_item = ListItem(Label(f"  └─ {current_section}", classes="task-section"))
                    section_item.data = {"type": "section", "project": project_name, "section": current_section}
                    await list_view.append(section_item)
                    continue
                
                if line.strip().startswith("- [") and "]" in line:
                    done = line.strip().startswith("- [x]")
                    task_text = line.strip()[5:]  # Remove "- [x] " or "- [ ] "
                    
                    status_icon = "✅" if done else "☐"
                    item = ListItem(Label(f"    {status_icon} {task_text}", classes="task-item"))
                    item.data = {
                        "type": "task",
                        "project": project_name,
                        "task_text": task_text,
                        "done": done,
                        "line": i + 1,
                        "section": current_section
                    }
                    await list_view.append(item)
    
    def on_list_view_selected(self, event: ListView.Selected):
        """Handle task selection."""
        if event.item.data and event.item.data.get("type") == "task":
            data = event.item.data
            self.post_message(TaskSelected(
                data["project"],
                data["task_text"],
                data["done"],
                data["line"]
            ))
    
    def action_toggle(self):
        """Toggle task completion."""
        list_view = self.query_one("#tasks-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") == "task":
                self.app.post_message(self.app.ToggleTaskRequested(
                    data["project"],
                    data["task_text"],
                    data["done"],
                    data["line"]
                ))
    
    def action_select(self):
        """Open task in project."""
        list_view = self.query_one("#tasks-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") == "task":
                self.post_message(TaskSelected(
                    data["project"],
                    data["task_text"],
                    data["done"],
                    data["line"]
                ))
    
    def action_new(self):
        """Create new task."""
        list_view = self.query_one("#tasks-list", ListView)
        project = None
        # Find current project from selection or header
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") in ("header", "section", "task"):
                project = data["project"]
        
        self.app.post_message(self.app.NewTaskRequested(project))
    
    def action_due(self):
        """Set due date for task."""
        list_view = self.query_one("#tasks-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") == "task":
                self.app.post_message(self.app.SetDueDateRequested(
                    data["project"],
                    data["task_text"]
                ))
    
    def action_refresh(self):
        """Refresh task list."""
        self.app.run_worker(self.refresh_content())
    
    def action_open_project(self):
        """Open project of selected task."""
        list_view = self.query_one("#tasks-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") in ("header", "section", "task"):
                self.app.post_message(self.app.ProjectSelected(data["project"]))