"""TodayDashboardProvider — data provider for Today workspace.

This is the single source of truth for all data displayed in the Today dashboard.
It sits between the vault/services and the dashboard components, ensuring:

1. Components render data only — no business logic
2. Data is prepared/cached appropriately
3. Real backend data replaces all placeholder/fake data
4. Clean empty states when data is unavailable

Desired flow:
    Vault
        ↓
    Application Services (AppServices)
        ↓
    TodayDashboardProvider
        ↓
    Dashboard Components (render only)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, List, Dict
from dataclasses import dataclass, field
from collections import defaultdict

from personal_os.core.vault import get_vault, VaultManager
from personal_os.core.processor import BackgroundProcessor
from personal_os.core.config import get_config


@dataclass
class ProjectSummary:
    """Summary of a project for dashboard display."""
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


@dataclass
class CaptureEntry:
    """A capture/journal entry for display."""
    time_str: str
    source: str
    status: str  # "processed", "pending", "error"
    preview: str
    timestamp: datetime


@dataclass
class FocusItem:
    """A focus/task item for Today's Focus panel."""
    done: bool
    title: str
    project: str
    high_priority: bool = False


@dataclass
class UpcomingItem:
    """An upcoming deadline or scheduled item."""
    date_str: str
    title: str
    project: str
    status: str  # "overdue", "today", "this_week", "later"


@dataclass
class NoteItem:
    """A recent note/document."""
    title: str
    path: str
    modified: datetime
    project: Optional[str] = None


@dataclass
class TaskItem:
    """An active task."""
    done: bool
    title: str
    project: str


@dataclass
class DashboardData:
    """Complete data bundle for Today dashboard."""
    # Current Project
    current_project: Optional[ProjectSummary] = None
    projects_count: int = 0
    
    # Metrics
    active_tasks_count: int = 0
    captures_today: int = 0
    hours_logged: float = 0.0
    
    # Panels
    recent_captures: List[CaptureEntry] = field(default_factory=list)
    focus_items: Dict[str, List[FocusItem]] = field(default_factory=dict)
    upcoming_items: Dict[str, List[UpcomingItem]] = field(default_factory=dict)
    recent_notes: List[NoteItem] = field(default_factory=list)
    active_tasks: List[TaskItem] = field(default_factory=list)


class TodayDashboardProvider:
    """Prepares and serves all data for the Today dashboard.
    
    This is the ONLY place that should read from the vault for dashboard data.
    Components receive pre-prepared data via this provider.
    """
    
    def __init__(
        self,
        vault: VaultManager,
        processor: Optional[BackgroundProcessor] = None,
        config: Any = None,
    ):
        self.vault = vault
        self.processor = processor
        self.config = config or get_config()
        self._cached_data: Optional[DashboardData] = None
        self._cache_valid = False
    
    def invalidate_cache(self):
        """Mark cached data as stale."""
        self._cache_valid = False
    
    def get_data(self, force_refresh: bool = False) -> DashboardData:
        """Get complete dashboard data, using cache if valid."""
        if not self._cache_valid or force_refresh or self._cached_data is None:
            self._cached_data = self._build_data()
            self._cache_valid = True
        return self._cached_data
    
    def _build_data(self) -> DashboardData:
        """Build complete dashboard data from vault."""
        data = DashboardData()
        
        # Load all data
        self._load_projects(data)
        self._load_captures(data)
        self._load_focus(data)
        self._load_upcoming(data)
        self._load_notes(data)
        self._load_tasks(data)
        
        return data
    
    def _load_projects(self, data: DashboardData):
        """Load project data and determine current project."""
        projects = self.vault.list_projects()
        data.projects_count = len(projects)
        
        if not projects:
            return
        
        # Calculate totals
        total_tasks = 0
        total_hours = 0.0
        project_summaries = []
        
        for project in projects:
            # Count active tasks
            tasks_file = project['path'] / "Tasks.md"
            open_tasks = 0
            if tasks_file.exists():
                content = self.vault.read_file(tasks_file)
                open_tasks = content.count('- [ ]')
            total_tasks += open_tasks
            
            # Build project summary
            summary = ProjectSummary(
                name=project['name'],
                path=project['path'],
                status=project.get('status', 'active'),
                health=project.get('health', 'green'),
                last_activity=project.get('last_activity'),
                open_tasks=open_tasks,
                total_hours=project.get('total_hours', 0.0),
                description=project.get('description', ''),
            )
            
            # Try to get next task from Tasks.md
            if tasks_file.exists():
                content = self.vault.read_file(tasks_file)
                lines = content.split('\n')
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
                        summary.next_task = stripped[5:].strip()
                        break
            
            total_hours += summary.total_hours
            project_summaries.append(summary)
        
        data.active_tasks_count = total_tasks
        data.hours_logged = total_hours
        
        # Determine current project (most recently active)
        projects_with_activity = [p for p in project_summaries if p.last_activity]
        if projects_with_activity:
            projects_with_activity.sort(key=lambda p: p.last_activity or '', reverse=True)
            data.current_project = projects_with_activity[0]
    
    def _load_captures(self, data: DashboardData):
        """Load recent captures from journal."""
        entries = self.vault.get_journal_entries()
        
        # Take last 10, newest first
        for entry in entries[-10:][::-1]:
            content = entry.get('content', '').strip()
            preview = content[:80].replace('\n', ' ') if content else "(empty)"
            
            time_str = entry.get('time', '')
            source = entry.get('source', 'Text')
            status = "processed"  # All journal entries are considered processed
            
            try:
                timestamp = datetime.strptime(f"{datetime.now().strftime('%Y-%m-%d')} {time_str}", "%Y-%m-%d %H:%M")
            except ValueError:
                timestamp = datetime.now()
            
            data.recent_captures.append(CaptureEntry(
                time_str=time_str,
                source=source,
                status=status,
                preview=preview,
                timestamp=timestamp,
            ))
        
        data.captures_today = len(entries)
    
    def _load_focus(self, data: DashboardData):
        """Load focus items from project tasks."""
        projects = self.vault.list_projects()
        
        daily_items = []
        big_items = []
        
        for project in projects:
            tasks_file = project['path'] / "Tasks.md"
            if not tasks_file.exists():
                continue
            
            content = self.vault.read_file(tasks_file)
            lines = content.split('\n')
            
            in_active = False
            for line in lines:
                stripped = line.strip()
                
                if stripped.startswith('## Active Tasks'):
                    in_active = True
                    continue
                elif stripped.startswith('## ') and in_active:
                    in_active = False
                    continue
                
                if in_active and stripped.startswith('- [') and ']' in stripped:
                    done = stripped.startswith('- [x]') or stripped.startswith('- [X]')
                    task_text = stripped[5:].strip() if len(stripped) > 5 else ""
                    
                    if not task_text or done:
                        continue
                    
                    # Determine if it's a "big task"
                    is_big = any(kw in task_text.lower() for kw in [
                        'milestone', 'release', 'v0.', 'v1.', 'launch', 'deploy',
                        'merge', 'architecture', 'refactor', 'benchmark', 'timing closure'
                    ])
                    
                    item = FocusItem(
                        done=done,
                        title=task_text,
                        project=project['name'],
                        high_priority=is_big,
                    )
                    
                    if is_big:
                        big_items.append(item)
                    else:
                        daily_items.append(item)
        
        # Limit items
        if daily_items:
            data.focus_items["Daily Tasks"] = daily_items[:5]
        if big_items:
            data.focus_items["Big Tasks"] = big_items[:5]
    
    def _load_upcoming(self, data: DashboardData):
        """Load upcoming deadlines from project files."""
        projects = self.vault.list_projects()
        now = datetime.now()
        
        overdue = []
        today_items = []
        week_items = []
        later_items = []
        
        for project in projects:
            project_name = project['name']
            project_dir = project['path']
            
            # Check Tasks.md, Progress.md, Timeline.md for dates
            for fname in ['Tasks.md', 'Progress.md', 'Timeline.md']:
                fpath = project_dir / fname
                if not fpath.exists():
                    continue
                
                content = self.vault.read_file(fpath)
                items = self._extract_dated_items(content, project_name)
                
                for date_str, title, status in items:
                    dt = self._parse_flexible_date(date_str, now)
                    if dt is None:
                        continue
                    
                    diff_days = (dt.date() - now.date()).days
                    
                    item = UpcomingItem(
                        date_str=self._format_date_for_display(dt, now),
                        title=title[:60],
                        project=project_name,
                        status=status,
                    )
                    
                    if diff_days < 0:
                        overdue.append(item)
                    elif diff_days == 0:
                        today_items.append(item)
                    elif diff_days <= 7:
                        week_items.append(item)
                    else:
                        later_items.append(item)
        
        if overdue:
            data.upcoming_items["Overdue"] = overdue[:5]
        if today_items:
            data.upcoming_items["Today"] = today_items[:5]
        if week_items:
            data.upcoming_items["This Week"] = week_items[:5]
        if later_items:
            data.upcoming_items["Later"] = later_items[:5]
    
    def _extract_dated_items(self, content: str, project: str) -> List[tuple]:
        """Extract items with dates from markdown content."""
        import re
        items = []
        lines = content.split('\n')
        
        for line in lines:
            stripped = line.strip()
            
            # Pattern: - [ ] Task (due: date) or - [ ] Task (deadline: date)
            if '- [' in stripped and ']' in stripped:
                if 'due' in stripped.lower() or 'deadline' in stripped.lower() or 'by ' in stripped.lower():
                    # Try to extract date
                    date_match = re.search(r'(?:due|deadline|by)\s*:?\s*([^\)]+)', stripped, re.IGNORECASE)
                    if date_match:
                        date_str = date_match.group(1).strip()
                        task_text = stripped[5:].strip() if stripped.startswith('- [ ]') else stripped
                        items.append((date_str, task_text, "pending"))
            
            # Pattern: **date**: task
            bold_date_match = re.match(r'\*\*(\d{4}-\d{2}-\d{2}|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{4})?)\*\*:?\s*(.+)', stripped)
            if bold_date_match:
                date_str = bold_date_match.group(1)
                task_text = bold_date_match.group(2)
                items.append((date_str, task_text, "pending"))
        
        return items
    
    def _parse_flexible_date(self, date_str: str, now: datetime) -> Optional[datetime]:
        """Parse various date formats."""
        import re
        date_str = date_str.strip().lower()
        
        if 'today' in date_str:
            return now
        elif 'tomorrow' in date_str:
            return now + timedelta(days=1)
        elif 'yesterday' in date_str:
            return now - timedelta(days=1)
        
        # Day of week
        weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for i, day in enumerate(weekdays):
            if day in date_str:
                days_ahead = (i - now.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                return now + timedelta(days=days_ahead)
        
        # YYYY-MM-DD
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', date_str)
        if date_match:
            try:
                return datetime.strptime(date_match.group(1), '%Y-%m-%d')
            except ValueError:
                pass
        
        # MM/DD or MM-DD
        date_match = re.search(r'(\d{1,2})[/\-](\d{1,2})', date_str)
        if date_match:
            try:
                month, day = int(date_match.group(1)), int(date_match.group(2))
                if 1 <= month <= 12 and 1 <= day <= 31:
                    return datetime(now.year, month, day)
            except ValueError:
                pass
        
        return None
    
    def _format_date_for_display(self, dt: datetime, now: datetime) -> str:
        """Format datetime for display."""
        diff = (dt.date() - now.date()).days
        
        if diff < 0:
            return f"{abs(diff)}d ago"
        elif diff == 0:
            return "Today"
        elif diff == 1:
            return "Tomorrow"
        elif diff <= 7:
            return dt.strftime("%a")
        else:
            return dt.strftime("%m/%d")
    
    def _load_notes(self, data: DashboardData):
        """Load recently modified markdown files."""
        md_files = self.vault.get_all_markdown_files()
        
        files_with_time = []
        for file_path in md_files:
            try:
                stat = file_path.stat()
                modified = datetime.fromtimestamp(stat.st_mtime)
                
                rel_path = file_path.relative_to(self.vault.config.root)
                content = self.vault.read_file(file_path)
                title = self._extract_title(content, file_path.stem)
                
                # Try to determine project
                project = None
                parts = rel_path.parts
                if len(parts) > 1 and parts[0] == "02-Projects":
                    project = parts[1]
                
                files_with_time.append(NoteItem(
                    title=title,
                    path=str(rel_path),
                    modified=modified,
                    project=project,
                ))
            except Exception:
                continue
        
        # Sort by modification time, newest first
        files_with_time.sort(key=lambda n: n.modified, reverse=True)
        data.recent_notes = files_with_time[:10]
    
    def _extract_title(self, content: str, fallback: str) -> str:
        """Extract title from markdown content."""
        if not content:
            return fallback
        
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('# '):
                return line[2:].strip()
            elif line.startswith('## '):
                return line[3:].strip()
        
        return fallback
    
    def _load_tasks(self, data: DashboardData):
        """Load active tasks from all projects and Inbox."""
        projects = self.vault.list_projects()

        # Load from project tasks
        for project in projects:
            tasks_file = project['path'] / "Tasks.md"
            if not tasks_file.exists():
                continue

            content = self.vault.read_file(tasks_file)
            lines = content.split('\n')

            in_active = False
            for line in lines:
                stripped = line.strip()

                if stripped.startswith('## Active Tasks'):
                    in_active = True
                    continue
                elif stripped.startswith('## ') and in_active:
                    in_active = False
                    continue

                if in_active and stripped.startswith('- [') and ']' in stripped:
                    done = stripped.startswith('- [x]') or stripped.startswith('- [X]')
                    task_text = stripped[5:].strip() if len(stripped) > 5 else ""

                    if task_text:
                        data.active_tasks.append(TaskItem(
                            done=done,
                            title=task_text,
                            project=project['name'],
                        ))

        # Load from Inbox tasks
        inbox_file = self.vault.config.inbox_dir / "Tasks.md"
        if inbox_file.exists():
            content = self.vault.read_file(inbox_file)
            lines = content.split('\n')

            for line in lines:
                stripped = line.strip()

                if stripped.startswith('- [') and ']' in stripped:
                    done = stripped.startswith('- [x]') or stripped.startswith('- [X]')
                    task_text = stripped[5:].strip() if len(stripped) > 5 else ""

                    if task_text:
                        data.active_tasks.append(TaskItem(
                            done=done,
                            title=task_text,
                            project="Inbox",
                        ))

        # Sort: incomplete first
        data.active_tasks.sort(key=lambda t: (t.done, t.project))


def create_today_provider(app_services) -> TodayDashboardProvider:
    """Factory function to create TodayDashboardProvider from AppServices."""
    return TodayDashboardProvider(
        vault=app_services.vault,
        processor=app_services.processor,
        config=app_services.config,
    )