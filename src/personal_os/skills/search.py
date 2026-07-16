"""Search skill - Tantivy-based full-text search for Personal OS."""
from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    import tantivy
    TANTIVY_AVAILABLE = True
except ImportError:
    TANTIVY_AVAILABLE = False
    tantivy = None

from personal_os.core.config import get_config
from personal_os.core.vault import get_vault, VaultManager


@dataclass
class SearchResult:
    """A single search result."""
    file: str
    line: int
    content: str
    context: str = ""
    score: float = 0.0


class SearchIndex:
    """Tantivy-based search index for the vault."""
    
    def __init__(self, vault: VaultManager):
        self.vault = vault
        self.config = get_config().search
        self._index = None
        self._schema = None
        self._index_dir = self.config.index_path
        self._index_dir.mkdir(parents=True, exist_ok=True)
    
    def _create_schema(self):
        """Create Tantivy schema."""
        if not TANTIVY_AVAILABLE:
            return None
        
        schema_builder = tantivy.SchemaBuilder()
        schema_builder.add_text_field("path", stored=True)
        schema_builder.add_text_field("content", stored=True)
        schema_builder.add_unsigned_field("line", stored=True)
        schema_builder.add_text_field("project", stored=True, tokenizer_name="raw")
        schema_builder.add_text_field("file_type", stored=True, tokenizer_name="raw")
        return schema_builder.build()
    
    def build(self):
        """Build or rebuild the search index."""
        if not TANTIVY_AVAILABLE:
            logger.warning("Tantivy not available, using fallback search")
            return
        
        self._schema = self._create_schema()
        self._index = tantivy.Index(self._schema, path=str(self._index_dir))
        
        writer = self._index.writer(heap_size=50_000_000)
        
        for md_file in self.vault.get_all_markdown_files():
            try:
                content = self.vault.read_file(md_file)
                if not content:
                    continue
                
                rel_path = md_file.relative_to(self.vault.config.root)
                project = self._extract_project(str(rel_path))
                file_type = self._classify_file(str(rel_path))
                
                # Index each line for precise location
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if len(line.strip()) < 3:
                        continue
                    
                    doc = tantivy.Document()
                    doc.add_text("path", str(rel_path))
                    doc.add_text("content", line)
                    doc.add_unsigned("line", i + 1)
                    doc.add_text("project", project)
                    doc.add_text("file_type", file_type)
                    writer.add_document(doc)
                    
            except Exception as e:
                logger.error(f"Error indexing {md_file}: {e}")
        
        writer.commit()
        logger.info(f"Search index built at {self._index_dir}")
    
    def _extract_project(self, rel_path: str) -> str:
        """Extract project name from path."""
        parts = Path(rel_path).parts
        if len(parts) >= 2 and parts[0] == "02-Projects":
            return parts[1]
        return "personal"
    
    def _classify_file(self, rel_path: str) -> str:
        """Classify file type from path."""
        name = Path(rel_path).name
        if name == "Progress.md":
            return "progress"
        elif name == "Tasks.md":
            return "task"
        elif name == "Notes.md":
            return "notes"
        elif name == "README.md":
            return "readme"
        elif name == "Architecture.md":
            return "architecture"
        elif name == "Decisions.md":
            return "decisions"
        elif "Journal" in rel_path:
            return "journal"
        return "other"
    
    def search(
        self,
        query: str,
        limit: int = 20,
        project: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> List[SearchResult]:
        """Search the index."""
        if not TANTIVY_AVAILABLE or self._index is None:
            return self._fallback_search(query, limit, project, file_type)
        
        try:
            searcher = self._index.searcher()
            
            # Build query
            query_parser = tantivy.QueryParser.for_index(self._index, ["content", "path"])
            query_parser.set_field_boost("content", 2.0)
            query_parser.set_field_boost("path", 1.0)
            
            # Add filters
            if project:
                query = f"({query}) AND project:{project}"
            if file_type:
                query = f"({query}) AND file_type:{file_type}"
            
            parsed_query = query_parser.parse_query(query)
            
            # Search
            results = searcher.search(parsed_query, limit=limit)
            
            output = []
            for score, doc_address in results.hits:
                doc = searcher.doc(doc_address)
                path = doc.get_first("path")
                content = doc.get_first("content")
                line = doc.get_first("line")
                
                if path and content:
                    output.append(SearchResult(
                        file=str(path),
                        line=int(line) if line else 0,
                        content=str(content),
                        score=float(score),
                    ))
            
            return output
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return self._fallback_search(query, limit, project, file_type)
    
    def _fallback_search(
        self,
        query: str,
        limit: int,
        project: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> List[SearchResult]:
        """Python-based fallback search."""
        terms = query.lower().split()
        results = []
        
        for md_file in self.vault.get_all_markdown_files():
            try:
                rel_path = str(md_file.relative_to(self.vault.config.root))
                
                if project and project not in rel_path:
                    continue
                if file_type:
                    expected = self._type_to_pattern(file_type)
                    if expected not in rel_path:
                        continue
                
                content = self.vault.read_file(md_file)
                if not content:
                    continue
                
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    line_lower = line.lower()
                    if all(term in line_lower for term in terms):
                        # Get context
                        start = max(0, i - 1)
                        end = min(len(lines), i + 2)
                        context = '\n'.join(lines[start:end])
                        
                        results.append(SearchResult(
                            file=rel_path,
                            line=i + 1,
                            content=line.strip(),
                            context=context,
                        ))
                        
                        if len(results) >= limit:
                            return results
                            
            except Exception as e:
                logger.debug(f"Fallback search error for {md_file}: {e}")
        
        return results
    
    def _type_to_pattern(self, file_type: str) -> str:
        """Map file type to path pattern."""
        mapping = {
            "progress": "Progress.md",
            "task": "Tasks.md",
            "notes": "Notes.md",
            "readme": "README.md",
            "journal": "Journal",
        }
        return mapping.get(file_type, file_type)
    
    def auto_index_if_needed(self):
        """Build index if it doesn't exist or is stale."""
        if not TANTIVY_AVAILABLE:
            return
        
        index_file = self._index_dir / "meta.json"
        if not index_file.exists():
            logger.info("Building initial search index...")
            self.build()


# Global index instance
_search_index: Optional[SearchIndex] = None


def get_search_index() -> SearchIndex:
    """Get or create global search index."""
    global _search_index
    if _search_index is None:
        _search_index = SearchIndex(get_vault())
        _search_index.auto_index_if_needed()
    return _search_index


async def search(
    query: str,
    limit: int = 20,
    project: Optional[str] = None,
    file_type: Optional[str] = None,
) -> List[SearchResult]:
    """Search the vault."""
    index = get_search_index()
    
    # Run in thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        index.search,
        query,
        limit,
        project,
        file_type,
    )


async def rebuild_index():
    """Rebuild search index."""
    index = get_search_index()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, index.build)


def parse_query(query: str) -> Dict[str, Any]:
    """Parse query string into structured filters."""
    filters = {
        "terms": [],
        "project": None,
        "file_type": None,
        "tags": [],
        "date_range": None,
        "exclude": [],
    }
    
    parts = query.split()
    for part in parts:
        if part.startswith("project:"):
            filters["project"] = part[8:]
        elif part.startswith("type:"):
            filters["file_type"] = part[5:]
        elif part.startswith("tag:") or part.startswith("#"):
            tag = part[4:] if part.startswith("tag:") else part[1:]
            filters["tags"].append(tag)
        elif part.startswith("date:"):
            filters["date_range"] = part[5:]
        elif part.startswith("-"):
            filters["exclude"].append(part[1:])
        else:
            filters["terms"].append(part)
    
    return filters


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m personal_os.skills.search \"query\"")
        sys.exit(1)
    
    query = " ".join(sys.argv[1:])
    results = asyncio.run(search(query))
    
    if not results:
        print(f"🔍 Search: \"{query}\"\n📭 No matches found.")
        sys.exit(0)
    
    print(f"🔍 Search: \"{query}\"")
    print(f"📄 {len(results)} matches\n")
    
    by_file = {}
    for r in results:
        if r.file not in by_file:
            by_file[r.file] = []
        by_file[r.file].append(r)
    
    for file_path, matches in by_file.items():
        print(f"📄 {file_path}")
        for m in matches[:3]:
            print(f"   L{m.line}: {m.content[:100]}")
        if len(matches) > 3:
            print(f"   ... and {len(matches) - 3} more matches")
        print()