"""Vault management - atomic file operations, git integration, path resolution."""
from __future__ import annotations
import os
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import git

from personal_os.core.config import get_config, VaultConfig


class VaultManager:
    """Manages vault operations with atomic writes and git tracking."""
    
    def __init__(self, vault_config: Optional[VaultConfig] = None):
        self.config = vault_config or get_config().vault
        self._repo: Optional[git.Repo] = None
        self._init_git()
    
    def _init_git(self):
        """Initialize git repo if not exists."""
        try:
            self._repo = git.Repo(self.config.root)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            self.config.root.mkdir(parents=True, exist_ok=True)
            self._repo = git.Repo.init(self.config.root)
            # Create initial commit if empty
            try:
                if not list(self._repo.iter_commits()):
                    self._create_initial_structure()
                    self._repo.index.add(["."])
                    self._repo.index.commit("Initial Personal OS vault structure")
            except (ValueError, git.GitCommandError):
                # Repo is empty or has no commits yet - that's fine
                pass
    
    def _create_initial_structure(self):
        """Create standard vault directories."""
        dirs = [
            "00-Inbox",
            "01-Academic",
            "02-Projects",
            "03-Skills",
            "04-Personal/Journal",
            "05-Resources",
            "06-Archive",
            "Templates",
        ]
        for d in dirs:
            (self.config.root / d).mkdir(parents=True, exist_ok=True)
        
        # Create default templates
        self._create_default_templates()
    
    def _create_default_templates(self):
        """Ensure default template files exist.

        The canonical project template ships with the repo at
        Templates/project-template.md (single source of truth, rendered by
        create_project). Daily/meeting templates are retained for future use
        but are not yet wired into the application (TODO: migrate when the
        journal/meeting capture features are integrated).
        """
        templates = {
            "daily-template.md": """# {{date}}

## Focus
{{focus}}

## Journal

## Notes

## Tasks Created
- [ ] 
""",
            "meeting-template.md": """# Meeting: {{title}}
**Date**: {{date}}
**Attendees**: {{attendees}}

## Agenda
{{agenda}}

## Notes
{{notes}}

## Decisions
{{decisions}}

## Action Items
{{action_items}}

---
*Source: {{source}}*
""",
        }

        for name, content in templates.items():
            path = self.config.templates_dir / name
            if not path.exists():
                path.write_text(content)

    def _project_readme_template(self) -> str:
        """Return the canonical project README template.

        Single source of truth is Templates/project-template.md. Falls back to a
        minimal inline template only if the file is missing (e.g. fresh vault
        where Templates/ was not seeded), so create_project never crashes.
        """
        template_path = self.config.templates_dir / "project-template.md"
        if template_path.exists():
            return self.read_file(template_path)
        return (
            "# {{project_name}}\n\n"
            "## Overview\n{{description}}\n\n"
            "## Status\n"
            "- **Phase**: {{phase}}\n"
            "- **Health**: {{health}}\n"
            "- **Last Updated**: {{date}}\n\n"
            "---\n*Created: {{date}}*\n"
        )
    
    # ========== Atomic File Operations ==========
    
    @contextmanager
    def atomic_write(self, path: Path):
        """Context manager for atomic file writes."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='w',
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
            encoding='utf-8'
        ) as tf:
            temp_path = Path(tf.name)
            try:
                yield temp_path
                temp_path.replace(path)  # Atomic on POSIX
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
    
    def write_file(self, path: Path, content: str, commit: bool = True, message: str = None):
        """Write file atomically, optionally commit to git."""
        with self.atomic_write(path) as tmp:
            tmp.write_text(content)
        
        if commit and self._repo:
            rel_path = path.relative_to(self.config.root)
            self._repo.index.add([str(rel_path)])
            msg = message or f"Update {rel_path}"
            self._repo.index.commit(msg)
    
    def append_file(self, path: Path, content: str, commit: bool = True, message: str = None):
        """Append to file atomically."""
        existing = path.read_text(encoding='utf-8') if path.exists() else ""
        self.write_file(path, existing + content, commit=commit, message=message)
    
    def read_file(self, path: Path) -> str:
        """Read file with graceful handling."""
        if path.exists():
            return path.read_text(encoding='utf-8')
        return ""
    
    def delete_file(self, path: Path, commit: bool = True):
        """Delete file and optionally commit."""
        if path.exists():
            path.unlink()
            if commit and self._repo:
                rel_path = path.relative_to(self.config.root)
                self._repo.index.remove([str(rel_path)])
                self._repo.index.commit(f"Delete {rel_path}")
    
    # ========== Journal Operations ==========
    
    def get_journal_path(self, date: Optional[datetime] = None) -> Path:
        """Get journal file path for date."""
        date = date or datetime.now()
        return self.config.journal_dir / f"{date.strftime('%Y-%m-%d')}.md"
    
    def append_journal_entry(self, entry: str, timestamp: Optional[datetime] = None, source: str = "Text") -> Path:
        """Append entry to today's journal."""
        path = self.get_journal_path()
        timestamp = timestamp or datetime.now()
        
        # Ensure header exists
        if not path.exists():
            header = f"# {timestamp.strftime('%Y-%m-%d')}\n\n"
            path.write_text(header)
        
        entry_text = f"## {timestamp.strftime('%H:%M')} - [Source: {source}]\n{entry}\n\n"
        self.append_file(path, entry_text, commit=True, message=f"Journal entry {timestamp.strftime('%H:%M')}")
        
        return path
    
    def get_journal_entries(self, date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Parse journal entries for a date."""
        path = self.get_journal_path(date)
        if not path.exists():
            return []
        
        content = self.read_file(path)
        entries = []
        current_entry = None
        
        for line in content.split('\n'):
            if line.startswith('## '):
                if current_entry:
                    entries.append(current_entry)
                # Parse: ## HH:MM - [Source: X]
                parts = line[3:].split(' - [Source: ')
                time_str = parts[0].strip()
                source = parts[1].rstrip(']') if len(parts) > 1 else 'Unknown'
                current_entry = {
                    'time': time_str,
                    'source': source,
                    'content': '',
                    'raw': line + '\n'
                }
            elif current_entry:
                current_entry['content'] += line + '\n'
                current_entry['raw'] += line + '\n'
        
        if current_entry:
            entries.append(current_entry)
        
        return entries
    
    # ========== Project Operations ==========
    
    def list_projects(self) -> List[Dict[str, Any]]:
        """List all projects with metadata."""
        projects = []
        for project_dir in self.config.projects_dir.iterdir():
            if project_dir.is_dir() and not project_dir.name.startswith('.'):
                meta = self._read_project_meta(project_dir)
                projects.append({
                    'name': project_dir.name,
                    'path': project_dir,
                    **meta
                })
        
        # Sort with None handling
        def sort_key(p):
            activity = p.get('last_activity', '')
            return activity if activity else ''
        
        return sorted(projects, key=sort_key, reverse=True)

    def _read_project_meta(self, project_dir: Path) -> Dict[str, Any]:
        """Extract metadata from project files."""
        meta = {
            'status': 'active',
            'health': 'green',
            'last_activity': None,
            'open_tasks': 0,
            'total_hours': 0.0,
            'next_milestone': None,
        }
        
        # Read Progress.md for hours
        progress_file = project_dir / "Progress.md"
        if progress_file.exists():
            content = self.read_file(progress_file)
            # Parse hours from entries like "- **2026-01-15**: Work (2h)"
            import re
            hours_matches = re.findall(r'\((\d+(?:\.\d+)?)h\)', content)
            meta['total_hours'] = sum(float(h) for h in hours_matches)
            meta['last_activity'] = self._extract_last_date(content)
        
        # Read Tasks.md for open tasks
        tasks_file = project_dir / "Tasks.md"
        if tasks_file.exists():
            content = self.read_file(tasks_file)
            meta['open_tasks'] = content.count('- [ ]')
        
        # Read Architecture.md/Decisions.md for health indicators
        for f in ['Architecture.md', 'Decisions.md', 'README.md']:
            fp = project_dir / f
            if fp.exists():
                content = self.read_file(fp)
                if 'BLOCKED' in content.upper() or 'blocked' in content.lower():
                    meta['health'] = 'red'
                    meta['status'] = 'blocked'
                elif 'TODO' in content.upper() or 'wip' in content.lower():
                    meta['health'] = 'yellow'
        
        return meta
    
    def _extract_last_date(self, content: str) -> Optional[str]:
        """Extract most recent date from content."""
        import re
        dates = re.findall(r'\*\*(\d{4}-\d{2}-\d{2})\*\*', content)
        return dates[-1] if dates else None
    
    def get_project_files(self, project_name: str) -> Dict[str, Path]:
        """Get standard project file paths."""
        project_dir = self.config.projects_dir / project_name
        return {
            'root': project_dir,
            'progress': project_dir / "Progress.md",
            'tasks': project_dir / "Tasks.md",
            'notes': project_dir / "Notes.md",
            'architecture': project_dir / "Architecture.md",
            'decisions': project_dir / "Decisions.md",
            'research': project_dir / "Research.md",
            'timeline': project_dir / "Timeline.md",
            'readme': project_dir / "README.md",
        }
    
    def create_project(self, name: str, description: str = "", phase: str = "planning") -> Path:
        """Create new project from the canonical template."""
        project_dir = self.config.projects_dir / name
        project_dir.mkdir(parents=True, exist_ok=False)

        template = self._project_readme_template()
        content = template.replace("{{project_name}}", name) \
                         .replace("{{description}}", description) \
                         .replace("{{phase}}", phase) \
                         .replace("{{health}}", "green") \
                         .replace("{{date}}", datetime.now().strftime('%Y-%m-%d'))

        self.write_file(project_dir / "README.md", content)
        
        # Create standard files
        for fname, header in [
            ("Progress.md", f"# {name} Progress\n\n## Progress Log\n"),
            ("Tasks.md", f"# {name} Tasks\n\n## Active Tasks\n\n## Completed Tasks\n\n## Backlog\n"),
            ("Notes.md", f"# {name} Notes\n\n"),
            ("Architecture.md", f"# {name} Architecture\n\n## Decision Log\n"),
            ("Decisions.md", f"# {name} Decisions\n\n## Decision Log\n"),
            ("Research.md", f"# {name} Research\n\n"),
            ("Timeline.md", f"# {name} Timeline\n\n"),
        ]:
            self.write_file(project_dir / fname, header)
        
        if self._repo:
            self._repo.index.add([str(p.relative_to(self.config.root)) for p in project_dir.rglob("*")])
            self._repo.index.commit(f"Create project: {name}")
        
        return project_dir
    
    # ========== Search Index Helpers ==========
    
    def get_all_markdown_files(self) -> List[Path]:
        """Get all markdown files for indexing."""
        return list(self.config.root.rglob("*.md"))
    
    def get_file_content(self, path: Path) -> str:
        """Get file content for indexing."""
        try:
            return path.read_text(encoding='utf-8')
        except Exception:
            return ""
    
    def commit_all(self, message: str = "Auto-commit"):
        """Commit all changes."""
        if self._repo:
            self._repo.git.add(A=True)
            if self._repo.index.diff("HEAD"):
                self._repo.index.commit(message)


# Singleton
_vault: Optional[VaultManager] = None


def get_vault() -> VaultManager:
    global _vault
    if _vault is None:
        _vault = VaultManager()
    return _vault