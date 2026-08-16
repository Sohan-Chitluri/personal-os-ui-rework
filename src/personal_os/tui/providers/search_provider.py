"""SearchProvider — data layer for the Search workspace.

Wraps the existing SearchEngine in core/search.py and provides a structured
data model for the SearchView and SearchResults component.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncio

from personal_os.core.search import SearchEngine, SearchResult as CoreSearchResult
from personal_os.core.vault import VaultManager
from personal_os.core.config import get_config


@dataclass
class SearchResultItem:
    """A search result item with display-friendly fields."""
    file: str
    line: int
    content: str
    context: str = ""
    match_type: str = "text"
    project: Optional[str] = None
    file_type: Optional[str] = None

    @classmethod
    def from_core_result(cls, result: CoreSearchResult, vault: VaultManager) -> "SearchResultItem":
        """Create from core SearchResult."""
        # Try to extract project from file path
        project = None
        parts = Path(result.file).parts
        if len(parts) > 1 and parts[0] == "02-Projects":
            project = parts[1]
        
        # Try to determine file type
        file_type = None
        fname = Path(result.file).name
        if fname.endswith("Tasks.md"):
            file_type = "task"
        elif fname.endswith("Progress.md"):
            file_type = "progress"
        elif fname.endswith("Notes.md"):
            file_type = "notes"
        elif fname.endswith("Decisions.md"):
            file_type = "decision"
        elif fname.endswith("README.md"):
            file_type = "readme"
        elif "Journal" in result.file:
            file_type = "journal"
        
        return cls(
            file=result.file,
            line=result.line,
            content=result.content,
            context=result.context,
            match_type=result.match_type,
            project=project,
            file_type=file_type
        )


@dataclass
class SearchResultsData:
    """Complete data bundle for the Search workspace."""
    query: str = ""
    results: List[SearchResultItem] = field(default_factory=list)
    grouped_by_file: Dict[str, List[SearchResultItem]] = field(default_factory=dict)
    is_searching: bool = False
    error: Optional[str] = None
    
    def group_by_file(self) -> Dict[str, List[SearchResultItem]]:
        """Group results by file for display."""
        grouped: Dict[str, List[SearchResultItem]] = {}
        for item in self.results:
            grouped.setdefault(item.file, []).append(item)
        return grouped


class SearchProvider:
    """Prepares and serves search data for the Search workspace."""
    
    def __init__(
        self,
        vault: VaultManager,
        config: Any = None,
    ):
        self.vault = vault
        self.config = config or get_config()
        self._engine = SearchEngine(vault)
        self._cached_data: SearchResultsData = SearchResultsData()
        self._search_task: Optional[asyncio.Task] = None
    
    def get_data(self) -> SearchResultsData:
        """Get current search data."""
        return self._cached_data
    
    async def search(self, query: str, limit: int = 100) -> SearchResultsData:
        """Perform a search and update cached data."""
        self._cached_data = SearchResultsData(query=query, is_searching=True)
        
        try:
            core_results = await self._engine.search(query, limit=limit)
            
            items = [
                SearchResultItem.from_core_result(r, self.vault)
                for r in core_results
            ]
            
            self._cached_data = SearchResultsData(
                query=query,
                results=items,
                is_searching=False
            )
        except Exception as e:
            self._cached_data = SearchResultsData(
                query=query,
                is_searching=False,
                error=str(e)
            )
        
        return self._cached_data
    
    def clear(self) -> SearchResultsData:
        """Clear search results."""
        self._cached_data = SearchResultsData()
        return self._cached_data


def create_search_provider(app_services: Any) -> SearchProvider:
    """Factory function to create SearchProvider from AppServices."""
    return SearchProvider(
        vault=app_services.vault,
        config=app_services.config
    )