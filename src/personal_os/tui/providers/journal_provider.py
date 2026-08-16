"""JournalProvider — data layer for the Journal workspace.

Reads journal entries and semantic blocks from the vault via VaultManager
and provides a structured data model for the JournalView.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
import re

from personal_os.core.vault import VaultManager
from personal_os.core.config import get_config


@dataclass
class SemanticItem:
    """A single semantic interpretation item from a capture."""
    type: str
    description: str
    project: Optional[str] = None
    certainty: float = 0.0
    confidence: float = 0.0
    temporal_context: Optional[str] = None
    source_excerpt: Optional[str] = None
    action: Optional[str] = None


@dataclass
class JournalEntry:
    """A journal entry with optional semantic metadata."""
    time: str
    source: str
    capture_id: Optional[str] = None
    content: str = ""
    raw: str = ""
    semantic_items: List[SemanticItem] = field(default_factory=list)
    has_semantic: bool = False


@dataclass
class JournalDayData:
    """All entries for a single day."""
    date: datetime
    entries: List[JournalEntry] = field(default_factory=list)
    entry_count: int = 0
    
    def __post_init__(self):
        self.entry_count = len(self.entries)


@dataclass
class JournalData:
    """Complete data bundle for the Journal workspace."""
    days: List[JournalDayData] = field(default_factory=list)
    selected_date: Optional[datetime] = None
    expanded_entries: Dict[str, bool] = field(default_factory=dict)  # capture_id -> expanded
    
    def get_day(self, date: datetime) -> Optional[JournalDayData]:
        """Get day data by date."""
        for day in self.days:
            if day.date.date() == date.date():
                return day
        return None
    
    def is_expanded(self, capture_id: str) -> bool:
        """Check if an entry's semantic block is expanded."""
        return self.expanded_entries.get(capture_id, False)
    
    def toggle_expanded(self, capture_id: str) -> bool:
        """Toggle expanded state. Returns new state."""
        current = self.expanded_entries.get(capture_id, False)
        self.expanded_entries[capture_id] = not current
        return not current


class JournalProvider:
    """Prepares and serves journal data for the Journal workspace."""
    
    def __init__(
        self,
        vault: VaultManager,
        config: Any = None,
    ):
        self.vault = vault
        self.config = config or get_config()
        self._cached_data: Optional[JournalData] = None
        self._cache_valid = False
        self._date_range_days = 30  # Last 30 days by default
    
    def invalidate_cache(self):
        """Mark cached data as stale."""
        self._cache_valid = False
    
    def get_data(self, force_refresh: bool = False) -> JournalData:
        """Get complete journal data, using cache if valid."""
        if not self._cache_valid or force_refresh or self._cached_data is None:
            self._cached_data = self._build_data()
            self._cache_valid = True
        return self._cached_data
    
    def _build_data(self) -> JournalData:
        """Build complete journal data from vault."""
        data = JournalData()
        
        # Get last N days of journal entries
        now = datetime.now()
        for i in range(self._date_range_days):
            date = now - timedelta(days=i)
            day_data = self._load_day(date)
            if day_data.entries:
                data.days.append(day_data)
        
        # Select today by default
        if data.days and not data.selected_date:
            data.selected_date = data.days[0].date
        
        return data
    
    def _load_day(self, date: datetime) -> JournalDayData:
        """Load journal entries for a specific date."""
        day_data = JournalDayData(date=date)
        
        # Get raw entries
        raw_entries = self.vault.get_journal_entries(date)
        
        for entry in raw_entries:
            capture_id = entry.get('capture_id')
            journal_entry = JournalEntry(
                time=entry.get('time', ''),
                source=entry.get('source', 'Text'),
                capture_id=capture_id,
                content=entry.get('content', ''),
                raw=entry.get('raw', '')
            )
            
            # Parse semantic block if present
            if capture_id:
                semantic_items = self._parse_semantic_block(capture_id, date)
                if semantic_items:
                    journal_entry.semantic_items = semantic_items
                    journal_entry.has_semantic = True
            
            day_data.entries.append(journal_entry)
        
        return day_data
    
    def _parse_semantic_block(self, capture_id: str, date: datetime) -> List[SemanticItem]:
        """Parse semantic block from journal file for a capture."""
        try:
            path = self.vault.get_journal_path(date)
            if not path.exists():
                return []
            
            content = self.vault.read_file(path)
            
            # Find the semantic block for this capture_id
            pattern = f'<!-- semantic:{re.escape(capture_id)}'
            start_idx = content.find(pattern)
            if start_idx == -1:
                return []
            
            # Find the end of the comment
            end_idx = content.find('-->', start_idx)
            if end_idx == -1:
                return []
            
            # Extract YAML content
            yaml_start = content.find('\n', start_idx) + 1
            yaml_text = content[yaml_start:end_idx].strip()
            
            # Parse YAML
            block_data = yaml.safe_load(yaml_text)
            if not block_data or 'items' not in block_data:
                return []
            
            items = []
            for item_data in block_data['items']:
                items.append(SemanticItem(
                    type=item_data.get('type', ''),
                    description=item_data.get('description', ''),
                    project=item_data.get('project'),
                    certainty=item_data.get('certainty', 0.0),
                    confidence=item_data.get('confidence', 0.0),
                    temporal_context=item_data.get('temporal_context'),
                    source_excerpt=item_data.get('source_excerpt'),
                    action=item_data.get('action')
                ))
            return items
        except Exception:
            return []
    
    def select_date(self, date: datetime) -> bool:
        """Select a date for detail view."""
        data = self.get_data()
        if data.get_day(date):
            data.selected_date = date
            return True
        return False
    
    def toggle_semantic(self, capture_id: str) -> bool:
        """Toggle semantic block expansion for an entry."""
        data = self.get_data()
        return data.toggle_expanded(capture_id)
    
    def is_expanded(self, capture_id: str) -> bool:
        """Check if semantic block is expanded."""
        data = self.get_data()
        return data.is_expanded(capture_id)
    
    def get_day_data(self, date: datetime) -> Optional[JournalDayData]:
        """Get day data for a specific date."""
        data = self.get_data()
        return data.get_day(date)
    
    def refresh(self) -> JournalData:
        """Force refresh and return new data."""
        self.invalidate_cache()
        return self.get_data(force_refresh=True)


def create_journal_provider(app_services: Any) -> JournalProvider:
    """Factory function to create JournalProvider from AppServices."""
    return JournalProvider(
        vault=app_services.vault,
        config=app_services.config
    )