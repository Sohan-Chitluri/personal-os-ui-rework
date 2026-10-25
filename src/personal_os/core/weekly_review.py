"""Weekly review service for Personal OS.

A reusable review engine with separated responsibilities:
  1. Collect  - read raw data from the vault
  2. Analyze  - turn raw data into structured ReviewData
  3. Render   - produce the Markdown review document

Designed to support future Daily Briefs, Monthly Reviews, dashboards, and AI
summaries. Driven entirely by the canonical VaultManager (no hardcoded paths).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from personal_os.core.config import get_config
from personal_os.core.vault import VaultManager, get_vault

logger = logging.getLogger(__name__)


@dataclass
class ReviewData:
    """Structured review data produced by the analyzer."""

    week_start: str
    week_end: str
    date_range_label: str
    journal_entries: List[Dict[str, str]] = field(default_factory=list)
    projects_touched: List[str] = field(default_factory=list)
    project_progress: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    completed_tasks: List[Dict[str, str]] = field(default_factory=list)
    new_tasks: List[Dict[str, str]] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    learning: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "week_start": self.week_start,
            "week_end": self.week_end,
            "date_range_label": self.date_range_label,
            "journal_entries": self.journal_entries,
            "projects_touched": self.projects_touched,
            "project_progress": self.project_progress,
            "completed_tasks": self.completed_tasks,
            "new_tasks": self.new_tasks,
            "decisions": self.decisions,
            "blockers": self.blockers,
            "learning": self.learning,
            "metrics": self.metrics,
        }


class WeeklyReviewGenerator:
    """Generate a weekly review from the vault."""

    def __init__(self, vault: Optional[VaultManager] = None):
        self.vault = vault or get_vault()
        self.root = self.vault.config.root
        self.projects_dir = self.vault.config.projects_dir
        self.skills_dir = self.vault.config.skills_dir
        self.journal_dir = self.vault.config.journal_dir
        self.reviews_dir = self.root / "04-Personal" / "Reviews" / "weekly"

    # ---- COLLECT ----

    def get_week_range(self, date: Optional[datetime] = None) -> "tuple[datetime, datetime]":
        """Monday-Sunday range. Default: the most recently completed week."""
        if date is None:
            date = datetime.now() - timedelta(days=datetime.now().weekday() + 7)
        else:
            date = date - timedelta(days=date.weekday())
        monday = date
        sunday = monday + timedelta(days=6)
        return monday, sunday

    def collect_journal_entries(self, monday: datetime, sunday: datetime) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []
        current = monday
        while current <= sunday:
            journal_file = self.journal_dir / current.strftime("%Y-%m-%d.md")
            if journal_file.exists():
                entries.append({
                    "date": current.strftime("%Y-%m-%d"),
                    "day": current.strftime("%A"),
                    "content": journal_file.read_text(encoding="utf-8"),
                })
            current += timedelta(days=1)
        return entries

    def collect_project_progress(self, monday: datetime, sunday: datetime) -> Dict[str, List[Dict[str, str]]]:
        progress: Dict[str, List[Dict[str, str]]] = {}
        if not self.projects_dir.exists():
            return progress
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            progress_file = project_dir / "Progress.md"
            if not progress_file.exists():
                continue
            for line in progress_file.read_text(encoding="utf-8").split("\n"):
                m = re.match(r"^-\s*\*\*(\d{4}-\d{2}-\d{2})\*\*:\s*(.+)", line.strip())
                if not m:
                    continue
                try:
                    entry_date = datetime.strptime(m.group(1), "%Y-%m-%d")
                except ValueError:
                    continue
                if monday <= entry_date <= sunday:
                    progress.setdefault(project_dir.name, []).append({
                        "date": m.group(1),
                        "description": m.group(2),
                    })
        return progress

    @staticmethod
    def _extract_task_date(line: str) -> Optional[datetime.date]:
        """Extract completion or task date from a markdown task line."""
        # 1. Check for **YYYY-MM-DD**
        m = re.search(r'\*\*(\d{4}-\d{2}-\d{2})\*\*', line)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                pass
        # 2. Check for [completed: YYYY-MM-DD] or (done: YYYY-MM-DD) or (closed: YYYY-MM-DD)
        m = re.search(r'\[?(?:completed|done|closed):\s*(\d{4}-\d{2}-\d{2})\]?', line, re.IGNORECASE)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                pass
        # 3. Check for 📅 YYYY-MM-DD
        m = re.search(r'📅\s*(\d{4}-\d{2}-\d{2})', line)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                pass
        return None

    def collect_completed_tasks(
        self, monday: datetime, sunday: datetime, include_undated: bool = True
    ) -> List[Dict[str, str]]:
        """Collect completed tasks, filtering by the [monday, sunday] date bounds when dates exist.

        Tasks with explicit dates (e.g. **YYYY-MM-DD**, [completed: YYYY-MM-DD], 📅 YYYY-MM-DD)
        are filtered strictly against the requested week.
        Historical tasks without completion timestamps are preserved if include_undated=True.
        """
        completed: List[Dict[str, str]] = []
        if not self.projects_dir.exists():
            return completed

        monday_date = monday.date() if isinstance(monday, datetime) else monday
        sunday_date = sunday.date() if isinstance(sunday, datetime) else sunday

        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            tasks_file = project_dir / "Tasks.md"
            if not tasks_file.exists():
                continue
            for line in tasks_file.read_text(encoding="utf-8").split("\n"):
                stripped = line.strip()
                if stripped.startswith(("- [x]", "- [X]")):
                    task_date = self._extract_task_date(stripped)
                    if task_date is not None:
                        if monday_date <= task_date <= sunday_date:
                            completed.append({"project": project_dir.name, "task": stripped})
                    elif include_undated:
                        completed.append({"project": project_dir.name, "task": stripped})
        return completed

    def collect_new_tasks(self, monday: datetime, sunday: datetime) -> List[Dict[str, str]]:
        """Tasks created during the week (parse '**YYYY-MM-DD**:' in Active Tasks)."""
        new_tasks: List[Dict[str, str]] = []
        if not self.projects_dir.exists():
            return new_tasks
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            tasks_file = project_dir / "Tasks.md"
            if not tasks_file.exists():
                continue
            content = tasks_file.read_text(encoding="utf-8")
            # Only look inside the Active Tasks section
            section = ""
            in_active = False
            for line in content.split("\n"):
                if line.strip().startswith("## Active Tasks"):
                    in_active = True
                    continue
                if in_active and line.strip().startswith("## "):
                    break
                if in_active:
                    section += line + "\n"
            for line in section.split("\n"):
                m = re.match(r"^- \[ \] \*\*(\d{4}-\d{2}-\d{2})\*\*:\s*(.+)", line.strip())
                if not m:
                    continue
                try:
                    entry_date = datetime.strptime(m.group(1), "%Y-%m-%d")
                except ValueError:
                    continue
                if monday <= entry_date <= sunday:
                    new_tasks.append({"project": project_dir.name, "task": m.group(2)})
        return new_tasks

    def collect_learning(self, monday: datetime, sunday: datetime) -> Dict[str, List[Dict[str, str]]]:
        learning: Dict[str, List[Dict[str, str]]] = {}
        if not self.skills_dir.exists():
            return learning
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            progress_file = skill_dir / "Progress.md"
            if not progress_file.exists():
                continue
            for line in progress_file.read_text(encoding="utf-8").split("\n"):
                m = re.match(r"^-\s*\*\*(\d{4}-\d{2}-\d{2})\*\*:\s*(.+)", line.strip())
                if not m:
                    continue
                try:
                    entry_date = datetime.strptime(m.group(1), "%Y-%m-%d")
                except ValueError:
                    continue
                if monday <= entry_date <= sunday:
                    learning.setdefault(skill_dir.name, []).append({
                        "date": m.group(1),
                        "description": m.group(2),
                    })
        return learning

    # ---- ANALYZE & METRICS ----

    @staticmethod
    def _extract_hours(text: str) -> float:
        """Extract hours from duration markers: @2h, 2h, (2h), timestamps (10:00 - 12:30), etc."""
        total = 0.0
        s = text

        # 1. Timestamps (e.g. 10:00 - 12:30 or 10:00 to 12:30)
        for m in re.finditer(
            r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(?:-|–|to)\s*([01]?\d|2[0-3]):([0-5]\d)\b",
            s,
            re.IGNORECASE,
        ):
            h1, m1, h2, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            diff = (h2 * 60 + m2) - (h1 * 60 + m1)
            if diff > 0:
                total += diff / 60.0
        s = re.sub(
            r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(?:-|–|to)\s*([01]?\d|2[0-3]):([0-5]\d)\b",
            " ",
            s,
            flags=re.IGNORECASE,
        )

        # 2. @ markers: @2h, @2.5h, @30m, @2
        for m in re.finditer(
            r"@(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h|minutes?|mins?|min|m)?\b",
            s,
            re.IGNORECASE,
        ):
            val = float(m.group(1))
            unit = (m.group(2) or "h").lower()
            if unit.startswith("m"):
                total += val / 60.0
            else:
                total += val
        s = re.sub(
            r"@(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h|minutes?|mins?|min|m)?\b",
            " ",
            s,
            flags=re.IGNORECASE,
        )

        # 3. Parenthesized: (2h), (2.5 hrs), (30 mins)
        for m in re.finditer(
            r"\((?:@\s*)?(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h|minutes?|mins?|min)\)",
            s,
            re.IGNORECASE,
        ):
            val = float(m.group(1))
            unit = m.group(2).lower()
            if unit.startswith("m"):
                total += val / 60.0
            else:
                total += val
        s = re.sub(
            r"\((?:@\s*)?(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h|minutes?|mins?|min)\)",
            " ",
            s,
            flags=re.IGNORECASE,
        )

        # 4. Explicit hours: 2h, 2.5h, 3 hours, 1.5 hrs
        for m in re.finditer(
            r"\b(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h)\b",
            s,
            re.IGNORECASE,
        ):
            total += float(m.group(1))
        s = re.sub(
            r"\b(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h)\b",
            " ",
            s,
            flags=re.IGNORECASE,
        )

        # 5. Explicit minutes: 30 mins, 45 minutes
        for m in re.finditer(
            r"\b(\d+(?:\.\d+)?)\s*(minutes?|mins?|min)\b",
            s,
            re.IGNORECASE,
        ):
            total += float(m.group(1)) / 60.0

        return total

    def _calculate_metrics(
        self,
        entries: List[Dict[str, str]],
        project_progress: Optional[Dict[str, List[Dict[str, str]]]] = None,
    ) -> Dict[str, Any]:
        """Inspect journal entries and project progress to calculate metrics including project_hours."""
        project_hours: Dict[str, float] = {}
        project_mentions: Dict[str, int] = {}
        decisions: List[str] = []
        blockers: List[str] = []
        unassigned_hours = 0.0

        if self.projects_dir.exists():
            project_names = [d.name for d in self.projects_dir.iterdir() if d.is_dir()]
        else:
            project_names = []

        if project_progress:
            for p in project_progress.keys():
                if p not in project_names:
                    project_names.append(p)

        # 1. Parse hours from project progress entries
        if project_progress:
            for proj, prog_list in project_progress.items():
                for item in prog_list:
                    desc = item.get("description", "")
                    h = self._extract_hours(desc)
                    if h > 0:
                        project_hours[proj] = project_hours.get(proj, 0.0) + h

        # 2. Parse hours, mentions, decisions, blockers from journal entries
        for entry in entries:
            content = entry.get("content", "")
            sections = re.split(r"(?m)^##\s+", content)
            for sec in sections:
                if not sec.strip():
                    continue
                matched_projects: List[str] = []
                for name in project_names:
                    if name.lower() in sec.lower():
                        matched_projects.append(name)
                        project_mentions[name] = project_mentions.get(name, 0) + 1

                if not matched_projects:
                    for wikilink in re.findall(r"\[\[([a-zA-Z0-9_\- ]+)\]\]", sec):
                        if wikilink not in matched_projects:
                            matched_projects.append(wikilink)
                            project_mentions[wikilink] = project_mentions.get(wikilink, 0) + 1
                    for tag in re.findall(r"#([a-zA-Z0-9_\-]+)", sec):
                        if (
                            tag.lower() not in ("todo", "done", "task", "note", "journal")
                            and tag not in matched_projects
                        ):
                            matched_projects.append(tag)
                            project_mentions[tag] = project_mentions.get(tag, 0) + 1

                sec_hours = self._extract_hours(sec)
                if sec_hours > 0:
                    if matched_projects:
                        split_h = sec_hours / len(matched_projects)
                        for proj in matched_projects:
                            project_hours[proj] = project_hours.get(proj, 0.0) + split_h
                    else:
                        unassigned_hours += sec_hours

            if re.search(r"(?:we|i|team)\s+(?:decided|decided to|chose to|switched to)", content, re.IGNORECASE):
                decisions.append(entry.get("date", ""))
            if re.search(r"(?:blocked|stuck|issue|problem)", content, re.IGNORECASE):
                blockers.append(entry.get("date", ""))

        project_hours = {p: round(h, 1) for p, h in project_hours.items() if h > 0}
        total_hours = round(sum(project_hours.values()) + unassigned_hours, 1)

        return {
            "total_hours": total_hours,
            "project_hours": project_hours,
            "project_mentions": project_mentions,
            "decision_count": len(decisions),
            "blocker_days": len(set(blockers)),
            "entries_count": len(entries),
        }

    def analyze(
        self,
        entries: List[Dict[str, str]],
        project_progress: Optional[Dict[str, List[Dict[str, str]]]] = None,
    ) -> Dict[str, object]:
        return self._calculate_metrics(entries, project_progress)

    def build_review_data(self, date: Optional[datetime] = None) -> ReviewData:
        """Collect + analyze into a structured ReviewData."""
        monday, sunday = self.get_week_range(date)
        week_str = monday.strftime("%Y-W%V")
        label = f"{monday.strftime('%Y-%m-%d')} to {sunday.strftime('%Y-%m-%d')}"

        journal_entries = self.collect_journal_entries(monday, sunday)
        project_progress = self.collect_project_progress(monday, sunday)
        completed_tasks = self.collect_completed_tasks(monday, sunday)
        new_tasks = self.collect_new_tasks(monday, sunday)
        learning = self.collect_learning(monday, sunday)
        analysis = self._calculate_metrics(journal_entries, project_progress)

        decisions = [
            e["date"]
            for e in journal_entries
            if re.search(
                r"(?:we|i|team)\s+(?:decided|decided to|chose to|switched to)",
                e.get("content", ""),
                re.IGNORECASE,
            )
        ]
        blockers = [
            e["date"]
            for e in journal_entries
            if re.search(r"(?:blocked|stuck|issue|problem)", e.get("content", ""), re.IGNORECASE)
        ]

        projects_touched = sorted(
            set(
                list(project_progress.keys())
                + list(analysis["project_mentions"].keys())
                + list(analysis["project_hours"].keys())
            )
        )

        metrics = dict(analysis)  # type: ignore[arg-type]
        metrics["projects_active"] = len(project_progress)
        metrics["tasks_completed"] = len(completed_tasks)
        metrics["tasks_created"] = len(new_tasks)
        metrics["skills_studied"] = len(learning)

        return ReviewData(
            week_start=monday.strftime("%Y-%m-%d"),
            week_end=sunday.strftime("%Y-%m-%d"),
            date_range_label=label,
            journal_entries=journal_entries,
            projects_touched=projects_touched,
            project_progress=project_progress,
            completed_tasks=completed_tasks,
            new_tasks=new_tasks,
            decisions=decisions,
            blockers=blockers,
            learning=learning,
            metrics=metrics,
        )

    # ---- RENDER ----

    def render(self, data: ReviewData) -> str:
        """Render ReviewData into the Markdown review document."""
        m = data.metrics
        review = (
            f"# Weekly Review: {data.date_range_label}\n\n"
            f"## 📊 Metrics Dashboard\n"
            f"| Metric | Value |\n|--------|-------|\n"
            f"| Total Hours Logged | {m.get('total_hours', 0)}h |\n"
            f"| Days with Entries | {m.get('entries_count', 0)}/7 |\n"
            f"| Decisions Made | {m.get('decision_count', 0)} |\n"
            f"| Blocker Days | {m.get('blocker_days', 0)} |\n"
            f"| Projects Active | {m.get('projects_active', 0)} |\n"
            f"| Tasks Completed | {m.get('tasks_completed', 0)} |\n"
            f"| Tasks Created | {m.get('tasks_created', 0)} |\n"
            f"| Skills Studied | {m.get('skills_studied', 0)} |\n\n"
            f"## 🏆 Wins This Week\n"
        )

        if data.project_progress:
            review += "\n### Project Advances\n"
            for project, prog in data.project_progress.items():
                for p in prog:
                    review += f"- **{project}**: {p['description'][:100]}\n"

        if data.completed_tasks:
            review += "\n### Tasks Completed\n"
            for task in data.completed_tasks[:10]:
                review += f"- {task['task']} ({task['project']})\n"

        if data.learning:
            review += "\n### Learning Breakthroughs\n"
            for skill, entries in data.learning.items():
                for e in entries:
                    review += f"- **{skill}**: {e['description'][:100]}\n"

        review += "\n## 📈 Project Progress\n"
        for project, prog in data.project_progress.items():
            total = len(prog)
            review += f"\n### {project} ({total} entries)\n"
            for p in prog:
                review += f"- **{p['date']}**: {p['description']}\n"

        review += "\n## ✅ Completed Tasks\n"
        if data.completed_tasks:
            for task in data.completed_tasks:
                review += f"- {task['task']} ({task['project']})\n"
        else:
            review += "No tasks marked complete this week.\n"

        review += "\n## 🆕 New Tasks\n"
        if data.new_tasks:
            for task in data.new_tasks:
                review += f"- {task['task']} ({task['project']})\n"
        else:
            review += "No new tasks created this week.\n"

        review += "\n## 📚 Learning & Growth\n"
        if data.learning:
            for skill, entries in data.learning.items():
                review += f"- **{skill}**: {len(entries)} session(s)\n"
                for e in entries:
                    review += f"  - {e['date']}: {e['description'][:80]}\n"
        else:
            review += "No learning entries this week.\n"

        review += "\n## ⚠️ Challenges & Blockers\n"
        if data.metrics.get("blocker_days", 0):
            review += f"- {data.metrics['blocker_days']} day(s) with reported blockers\n"
        else:
            review += "No explicit blockers reported.\n"

        review += "\n## 💡 Insights & Patterns\n"
        mentions: Dict[str, int] = data.metrics.get("project_mentions", {})
        if mentions:
            top = max(mentions, key=lambda k: mentions[k])
            review += f"- Most mentioned project: **{top}** ({mentions[top]} mentions)\n"
        total_h = float(data.metrics.get("total_hours", 0))
        if total_h > 20:
            review += "- High intensity week (>20h logged)\n"
        elif total_h < 5:
            review += "- Light week (<5h logged) - consider if intentional\n"

        review += (
            "\n## 🎯 Next Week's Priorities\n\n"
            "### MITs (Most Important Tasks)\n"
            "1. [ ] [Define based on project progress and deadlines]\n"
            "2. [ ] [Define based on project progress and deadlines]\n"
            "3. [ ] [Define based on project progress and deadlines]\n\n"
            "### Study Blocks\n- [ ] [Topic] - Xh - [Specific goal]\n\n"
            "### Decisions Needed\n- [Decision to make] - [Context]\n\n"
            "## 🔧 Process Improvements\n- [ ] [One thing to try differently]\n"
            "- [ ] [One habit to reinforce]\n\n"
            f"---\n*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"
            f"*Review Period: {data.week_start} to {data.week_end}*\n"
        )
        return review

    # ---- SAVE & GENERATE ----

    def save_review(self, review_file: Path, content: str) -> Path:
        """Save review content using VaultManager to ensure atomic writes and git tracking."""
        self.vault.write_file(review_file, content, message=f"Weekly review: {review_file.stem}")
        return review_file

    def generate(self, date: Optional[datetime] = None) -> Path:
        """Build structured data, render, and write the review file.

        Overwrite policy: the review filename is derived deterministically from
        the ISO week (YYYY-Wnn), so repeated runs for the same period overwrite
        the same file - never creating duplicates.
        """
        data = self.build_review_data(date)
        document = self.render(data)
        self.reviews_dir.mkdir(parents=True, exist_ok=True)
        monday, _ = self.get_week_range(date)
        week_str = monday.strftime("%Y-W%V")
        output_file = self.reviews_dir / f"{week_str}.md"
        self.save_review(output_file, document)
        return output_file


# Module-level convenience (matches core.links / core.search pattern)
_default_generator: Optional[WeeklyReviewGenerator] = None


def get_review_generator() -> WeeklyReviewGenerator:
    global _default_generator
    if _default_generator is None:
        _default_generator = WeeklyReviewGenerator(get_vault())
    return _default_generator


def generate_review(date: Optional[datetime] = None) -> Path:
    return get_review_generator().generate(date)


def save_review(review_file: Path, content: str) -> Path:
    return get_review_generator().save_review(review_file, content)


def build_review_data(date: Optional[datetime] = None) -> ReviewData:
    return get_review_generator().build_review_data(date)


def _calculate_metrics(
    entries: List[Dict[str, str]],
    project_progress: Optional[Dict[str, List[Dict[str, str]]]] = None,
    vault: Optional[VaultManager] = None,
) -> Dict[str, Any]:
    generator = WeeklyReviewGenerator(vault) if vault else get_review_generator()
    return generator._calculate_metrics(entries, project_progress)

