"""ProjectsProvider — data layer for the Projects workspace.

Reads project data from the vault via VaultManager.list_projects() and
get_project_files(), and provides a structured data model for the ProjectsView
and ProjectTree component.

Integrates with BigTasksProvider to include big task summaries per project.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from personal_os.core.vault import VaultManager
from personal_os.core.config import get_config
from personal_os.tui.big_tasks_provider import BigTasksProvider, BigTask


@dataclass
class ProjectDocument:
    """A project markdown document."""
    name: str
    path: Path
    exists: bool
    content: str = ""

    def preview(self, max_chars: int = 120) -> str:
        """Return a preview of the document content."""
        if not self.content:
            return "(empty)"
        preview = self.content.strip().replace('\n', ' ')
        if len(preview) > max_chars:
            preview = preview[:max_chars] + "..."
        return preview


@dataclass
class ProjectDetail:
    """Full detail data for a single project."""
    name: str
    path: Path
    status: str = "active"
    health: str = "green"
    last_activity: Optional[str] = None
    open_tasks: int = 0
    total_hours: float = 0.0
    description: str = ""
    next_task: Optional[str] = None
    milestone: Optional[str] = None
    progress: str = ""
    big_tasks: List[BigTask] = field(default_factory=list)
    documents: Dict[str, ProjectDocument] = field(default_factory=dict)

    # Standard document keys
    DOCUMENT_KEYS = [
        "readme", "tasks", "progress", "notes",
        "decisions", "research", "meetings", "timeline",
        "architecture"
    ]

    def get_document(self, key: str) -> Optional[ProjectDocument]:
        """Get a document by key."""
        return self.documents.get(key)

    def has_documents(self) -> bool:
        """Check if project has any documents with content."""
        return any(doc.exists and doc.content.strip() for doc in self.documents.values())


@dataclass
class ProjectsData:
    """Complete data bundle for the Projects workspace."""
    projects: List[ProjectDetail] = field(default_factory=list)
    selected_project: Optional[str] = None

    def get_project(self, name: str) -> Optional[ProjectDetail]:
        """Get project detail by name."""
        for p in self.projects:
            if p.name == name:
                return p
        return None

    def get_selected(self) -> Optional[ProjectDetail]:
        """Get the currently selected project."""
        if self.selected_project:
            return self.get_project(self.selected_project)
        return None


class ProjectsProvider:
    """Prepares and serves all data for the Projects workspace.

    This is the ONLY place that should read from the vault for projects data.
    Views and components receive pre-prepared data via this provider.
    """

    def __init__(
        self,
        vault: VaultManager,
        big_tasks_provider: Optional[BigTasksProvider] = None,
        config: Any = None,
    ):
        self.vault = vault
        self.big_tasks_provider = big_tasks_provider
        self.config = config or get_config()
        self._cached_data: Optional[ProjectsData] = None
        self._cache_valid = False

    def invalidate_cache(self):
        """Mark cached data as stale."""
        self._cache_valid = False

    def get_data(self, force_refresh: bool = False) -> ProjectsData:
        """Get complete projects data, using cache if valid."""
        if not self._cache_valid or force_refresh or self._cached_data is None:
            self._cached_data = self._build_data()
            self._cache_valid = True
        return self._cached_data

    def _build_data(self) -> ProjectsData:
        """Build complete projects data from vault."""
        data = ProjectsData()

        # Load all projects
        raw_projects = self.vault.list_projects()

        # Get big tasks data if provider available
        big_tasks_by_project: Dict[str, List[BigTask]] = {}
        if self.big_tasks_provider:
            bt_data = self.big_tasks_provider.get_data()
            for bt in bt_data.big_tasks:
                big_tasks_by_project.setdefault(bt.project, []).append(bt)

        for project in raw_projects:
            detail = self._build_project_detail(project, big_tasks_by_project.get(project['name'], []))
            data.projects.append(detail)

        # Select first project by default if none selected
        if not data.selected_project and data.projects:
            data.selected_project = data.projects[0].name

        return data

    def _build_project_detail(
        self,
        raw_project: Dict[str, Any],
        big_tasks: List[BigTask]
    ) -> ProjectDetail:
        """Build a ProjectDetail from raw project data."""
        project_name = raw_project['name']
        project_path = raw_project['path']

        # Get standard file paths
        file_paths = self.vault.get_project_files(project_name)

        # Read each document
        documents = {}
        for key in ProjectDetail.DOCUMENT_KEYS:
            path = file_paths.get(key)
            if path is not None:
                exists = path.exists()
                content = self.vault.read_file(path) if exists else ""
                doc_path = path
            else:
                exists = False
                content = ""
                doc_path = Path("")
            documents[key] = ProjectDocument(
                name=key.capitalize(),
                path=doc_path,
                exists=exists,
                content=content
            )

        # Extract description from README
        description = ""
        readme_doc = documents.get("readme")
        if readme_doc and readme_doc.content:
            # Get first non-header paragraph
            lines = readme_doc.content.split('\n')
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#') and not line.startswith('---'):
                    description = line[:200]
                    break

        # Get next task from Tasks.md
        next_task = None
        tasks_doc = documents.get("tasks")
        if tasks_doc and tasks_doc.content:
            lines = tasks_doc.content.split('\n')
            in_active = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('## Active Tasks'):
                    in_active = True
                    continue
                elif stripped.startswith('## ') and in_active:
                    in_active = False
                    continue
                if in_active and stripped.startswith('- [ ]'):
                    next_task = stripped[5:].strip()
                    break

        # Extract milestone from README or Progress
        milestone = None
        for doc_key in ["readme", "progress"]:
            doc = documents.get(doc_key)
            if doc and doc.content:
                import re
                # Look for milestone patterns
                matches = re.findall(r'(?:milestone|target|v\d+\.\d+)[:\s]+([^\n]+)', doc.content, re.IGNORECASE)
                if matches:
                    milestone = matches[0].strip()
                    break

        # Calculate progress bar
        progress = self._calculate_progress(raw_project.get('open_tasks', 0))

        return ProjectDetail(
            name=project_name,
            path=project_path,
            status=raw_project.get('status', 'active'),
            health=raw_project.get('health', 'green'),
            last_activity=raw_project.get('last_activity'),
            open_tasks=raw_project.get('open_tasks', 0),
            total_hours=raw_project.get('total_hours', 0.0),
            description=description,
            next_task=next_task,
            milestone=milestone,
            progress=progress,
            big_tasks=big_tasks,
            documents=documents
        )

    def _calculate_progress(self, open_tasks: int) -> str:
        """Generate a progress bar string."""
        if open_tasks == 0:
            return "██████████ 100%"
        elif open_tasks <= 2:
            return "████████░░ 80%"
        elif open_tasks <= 5:
            return "██████░░░░ 60%"
        elif open_tasks <= 10:
            return "████░░░░░░ 40%"
        else:
            return "██░░░░░░░░ 20%"

    def select_project(self, project_name: str) -> bool:
        """Select a project for detail view."""
        data = self.get_data()
        if data.get_project(project_name):
            data.selected_project = project_name
            return True
        return False

    def get_project_files(self, project_name: str) -> Dict[str, Path]:
        """Get standard project file paths (delegates to vault)."""
        return self.vault.get_project_files(project_name)

    def read_document(self, project_name: str, doc_key: str) -> str:
        """Read a specific project document."""
        file_paths = self.vault.get_project_files(project_name)
        path = file_paths.get(doc_key)
        if path and path.exists():
            return self.vault.read_file(path)
        return ""

    def refresh(self) -> ProjectsData:
        """Force refresh and return new data."""
        self.invalidate_cache()
        return self.get_data(force_refresh=True)


def create_projects_provider(app_services: Any) -> ProjectsProvider:
    """Factory function to create ProjectsProvider from AppServices."""
    # Try to get BigTasksProvider from app services if available
    big_tasks_provider = None
    if hasattr(app_services, 'big_tasks_provider'):
        big_tasks_provider = app_services.big_tasks_provider
    else:
        # Create one if not available
        from personal_os.tui.big_tasks_provider import create_big_tasks_provider
        try:
            big_tasks_provider = create_big_tasks_provider(app_services)
        except Exception:
            pass

    return ProjectsProvider(
        vault=app_services.vault,
        big_tasks_provider=big_tasks_provider,
        config=app_services.config
    )