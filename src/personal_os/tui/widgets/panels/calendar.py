"""Calendar panel - week/month view with time blocks."""
from __future__ import annotations
from textual.widgets import Static, Button, Label
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from datetime import datetime, timedelta

from personal_os.tui.widgets.panels.base import SidebarPanel


class CalendarPanel(SidebarPanel):
    """Calendar/week view panel."""
    
    BINDINGS = [
        Binding("left", "prev_week", "Prev"),
        Binding("right", "next_week", "Next"),
        Binding("w", "week_view", "Week"),
        Binding("m", "month_view", "Month"),
        Binding("t", "today", "Today"),
        Binding("n", "new_block", "New Block"),
    ]
    
    def __init__(self, vault, **kwargs):
        super().__init__("Calendar", "calendar", vault=vault, **kwargs)
        self.current_date = datetime.now()
        self.view_mode = "week"  # week or month
    
    def compose(self):
        yield Static("Calendar", classes="panel-title")
        with Horizontal(classes="cal-nav"):
            yield Button("◀", id="prev-btn", variant="default")
            yield Static("", id="cal-period", classes="cal-period")
            yield Button("▶", id="next-btn", variant="default")
        yield Static("", id="cal-view", classes="cal-view")
    
    async def refresh_content(self):
        """Refresh calendar view."""
        period = self.query_one("#cal-period", Static)
        view = self.query_one("#cal-view", Static)
        
        if self.view_mode == "week":
            period_text = self._get_week_range()
            content = self._render_week()
        else:
            period_text = self.current_date.strftime("%B %Y")
            content = self._render_month()
        
        period.update(period_text)
        view.update(content)
    
    def _get_week_range(self) -> str:
        """Get week range string."""
        start = self.current_date - timedelta(days=self.current_date.weekday())
        end = start + timedelta(days=6)
        return f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
    
    def _render_week(self) -> str:
        """Render week view with journal entries."""
        start = self.current_date - timedelta(days=self.current_date.weekday())
        days = []
        
        for i in range(7):
            day = start + timedelta(days=i)
            is_today = day.date() == datetime.now().date()
            
            # Get journal entries for this day
            entries = self.vault.get_journal_entries(day)
            
            day_name = day.strftime("%a %m/%d")
            if is_today:
                day_name = f"► {day_name} ◄"
            
            entry_lines = [day_name]
            if entries:
                for e in entries[:3]:  # Show first 3
                    entry_lines.append(f"  {e['time']} {e['content'][:40]}")
                if len(entries) > 3:
                    entry_lines.append(f"  ... +{len(entries) - 3} more")
            else:
                entry_lines.append("  (empty)")
            
            days.append("\n".join(entry_lines))
        
        return "\n\n".join(days)
    
    def _render_month(self) -> str:
        """Render month view."""
        # Simple month calendar
        first = self.current_date.replace(day=1)
        last_day = (first.replace(month=first.month + 1) - timedelta(days=1)) if first.month < 12 \
            else first.replace(year=first.year + 1, month=1, day=1) - timedelta(days=1)
        
        lines = ["Mo Tu We Th Fr Sa Su"]
        week = []
        
        # Pad first week
        for _ in range(first.weekday()):
            week.append("  ")
        
        day = 1
        while day <= last_day.day:
            current = first.replace(day=day)
            is_today = current.date() == datetime.now().date()
            
            day_str = f"{day:2d}"
            if is_today:
                day_str = f"►{day:2d}◄"
            
            week.append(day_str)
            
            if len(week) == 7:
                lines.append(" ".join(week))
                week = []
            
            day += 1
        
        if week:
            lines.append(" ".join(week))
        
        return "\n".join(lines)
    
    async def on_button_pressed(self, event: Button.Pressed):
        """Handle nav buttons."""
        if event.button.id == "prev-btn":
            await self.action_prev_week()
        elif event.button.id == "next-btn":
            await self.action_next_week()
    
    def action_prev_week(self):
        """Go to previous week/month."""
        if self.view_mode == "week":
            self.current_date -= timedelta(weeks=1)
        else:
            # Previous month
            if self.current_date.month == 1:
                self.current_date = self.current_date.replace(year=self.current_date.year - 1, month=12)
            else:
                self.current_date = self.current_date.replace(month=self.current_date.month - 1)
        self.app.run_worker(self.refresh_content())
    
    def action_next_week(self):
        """Go to next week/month."""
        if self.view_mode == "week":
            self.current_date += timedelta(weeks=1)
        else:
            if self.current_date.month == 12:
                self.current_date = self.current_date.replace(year=self.current_date.year + 1, month=1)
            else:
                self.current_date = self.current_date.replace(month=self.current_date.month + 1)
        self.app.run_worker(self.refresh_content())
    
    def action_week_view(self):
        """Switch to week view."""
        self.view_mode = "week"
        self.app.run_worker(self.refresh_content())
    
    def action_month_view(self):
        """Switch to month view."""
        self.view_mode = "month"
        self.app.run_worker(self.refresh_content())
    
    def action_today(self):
        """Jump to today."""
        self.current_date = datetime.now()
        self.app.run_worker(self.refresh_content())
    
    def action_new_block(self):
        """Create new time block."""
        self.app.post_message(self.app.NewCalendarBlockRequested(self.current_date))