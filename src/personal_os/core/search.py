"""Search module - fast vault-wide search with ripgrep fallback."""
from __future__ import annotations
import subprocess
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re

from personal_os.core.vault import get_vault, VaultManager

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    file: str
    line: int
    content: str
    context: str = ""
    match_type: str = "text"


@dataclass
class SearchFilters:
    terms: List[str] = None
    project: Optional[str] = None
    file_type: Optional[str] = None
    tags: List[str] = None
    date_range: Optional[str] = None
    exclude: List[str] = None
    phrase: Optional[str] = None
    
    def __post_init__(self):
        if self.terms is None:
            self.terms = []
        if self.tags is None:
            self.tags = []
        if self.exclude is None:
            self.exclude = []


class SearchEngine:
    """Fast search across vault using ripgrep with Python fallback."""
    
    TYPE_MAP = {
        "progress": "*Progress.md",
        "task": "*Tasks.md",
        "tasks": "*Tasks.md",
        "notes": "*Notes.md",
        "readme": "*README.md",
        "journal": "04-Personal/Journal/*.md",
        "architecture": "*Architecture.md",
        "decisions": "*Decisions.md",
        "research": "*Research.md",
        "timeline": "*Timeline.md",
    }
    
    def __init__(self, vault: Optional[VaultManager] = None):
        self.vault = vault or get_vault()
        self._has_rg = None
    
    def _check_rg(self) -> bool:
        """Check if ripgrep is available."""
        if self._has_rg is None:
            try:
                subprocess.run(["rg", "--version"], capture_output=True, check=True)
                self._has_rg = True
            except Exception:
                self._has_rg = False
        return self._has_rg
    
    def parse_query(self, query: str) -> SearchFilters:
        """Parse query string into structured filters."""
        filters = SearchFilters()
        parts = query.split()
        i = 0
        
        while i < len(parts):
            part = parts[i]
            
            if part.startswith("project:"):
                filters.project = part[8:]
            elif part.startswith("type:"):
                filters.file_type = part[5:]
            elif part.startswith("tag:") or part.startswith("#"):
                tag = part[4:] if part.startswith("tag:") else part[1:]
                filters.tags.append(tag)
            elif part.startswith("date:"):
                filters.date_range = part[5:]
            elif part.startswith("-"):
                filters.exclude.append(part[1:])
            elif part.upper() in ["AND", "OR"]:
                pass  # Ignore boolean operators for now
            elif part.startswith('"') and part.endswith('"'):
                filters.phrase = part[1:-1]
            else:
                filters.terms.append(part)
            
            i += 1
        
        return filters
    
    def _build_rg_cmd(self, filters: SearchFilters) -> List[str]:
        """Build ripgrep command from filters."""
        cmd = ["rg", "--no-heading", "--line-number", "--color=never", "-i"]

        # SearchEngine semantics (matching the Python fallback):
        # all positive terms/phrases/tags must appear in a matched line
        # (AND), and any excluded term must NOT appear in the line.
        # ripgrep ORs multiple -e patterns, so AND/NOT semantics require
        # a single PCRE2 pattern built from lookaheads.
        positive = [re.escape(t) for t in filters.terms]
        if filters.phrase:
            positive.append(re.escape(filters.phrase))
        positive.extend("#" + re.escape(tag) for tag in filters.tags)

        pattern = "^" + "".join(f"(?=.*{p})" for p in positive)
        pattern += "".join(
            f"(?!.*{re.escape(excl)})" for excl in filters.exclude
        )
        cmd.extend(["-P", "-e", pattern])

        # Exclusions are handled in the pattern above (line-level NOT).

        # Project filter — scope to files under a matching project
        # directory (rg globs without leading ** only match basenames).
        if filters.project:
            cmd.extend(["-g", f"**/*{filters.project}*/**", "-g", f"**/*{filters.project}*"])
        
        # File type filter
        if filters.file_type and filters.file_type in self.TYPE_MAP:
            cmd.extend(["-g", self.TYPE_MAP[filters.file_type]])
        
        # Date filter (grep doesn't do dates well, handled in post)
        # Tag filter (also post-processed)
        
        cmd.append(str(self.vault.config.root))
        return cmd
    
    async def search_rg(self, filters: SearchFilters, limit: int = 50) -> List[SearchResult]:
        """Search using ripgrep."""
        cmd = self._build_rg_cmd(filters)
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            
            if proc.returncode not in (0, 1):  # 1 = no matches
                raise RuntimeError(f"rg failed: {stderr.decode()}")
            
            return self._parse_rg_output(stdout.decode(), filters)
        
        except Exception as e:
            raise RuntimeError(f"Ripgrep search failed: {e}")
    
    def _parse_rg_output(self, output: str, filters: SearchFilters) -> List[SearchResult]:
        """Parse ripgrep output into SearchResult objects."""
        results = []
        
        for line in output.strip().split('\n'):
            if not line:
                continue
            
            parts = line.split(':', 2)
            if len(parts) < 3:
                continue
            
            file_path, line_num, content = parts
            
            try:
                rel_path = Path(file_path).relative_to(self.vault.config.root)
            except ValueError:
                rel_path = Path(file_path)
            
            # Post-filter tags
            if filters.tags:
                content_lower = content.lower()
                if not all(f"#{tag.lower()}" in content_lower for tag in filters.tags):
                    continue
            
            # Post-filter date range
            if filters.date_range and not self._matches_date_range(rel_path, filters.date_range):
                continue
            
            results.append(SearchResult(
                file=str(rel_path),
                line=int(line_num),
                content=content.strip(),
                match_type="rg"
            ))
        
        return results
    
    def _matches_date_range(self, path: Path, date_range: str) -> bool:
        """Check if file matches date range."""
        # Check journal date format
        if "Journal" in str(path):
            match = re.search(r'(\d{4}-\d{2}-\d{2})', str(path))
            if match:
                file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                return self._check_date_range(file_date, date_range)
        
        # Fallback to mtime
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).date()
            return self._check_date_range(mtime, date_range)
        except Exception:
            return True
    
    def _check_date_range(self, file_date: datetime.date, date_range: str) -> bool:
        """Check if date matches range."""
        today = datetime.now().date()
        
        if date_range == "today":
            return file_date == today
        elif date_range == "yesterday":
            return file_date == today - timedelta(days=1)
        elif date_range == "week":
            return file_date >= today - timedelta(days=7)
        elif date_range == "month":
            return file_date >= today - timedelta(days=30)
        elif date_range == "year":
            return file_date >= today - timedelta(days=365)
        
        # Try parse specific date
        try:
            target = datetime.strptime(date_range, "%Y-%m-%d").date()
            return file_date == target
        except ValueError:
            pass
        
        return True
    
    async def search_python(self, filters: SearchFilters, limit: int = 50) -> List[SearchResult]:
        """Fallback Python-based search."""
        results = []
        search_terms = [t.lower() for t in filters.terms]
        exclude_terms = [t.lower() for t in filters.exclude]
        
        for md_file in self.vault.config.root.rglob("*.md"):
            # Skip excluded patterns early
            file_str = str(md_file).lower()
            if any(excl in file_str for excl in filters.exclude):
                continue
            
            # Project filter
            if filters.project and filters.project.lower() not in file_str:
                continue
            
            # File type filter
            if filters.file_type and filters.file_type in self.TYPE_MAP:
                pattern = self.TYPE_MAP[filters.file_type].replace("*", "")
                if pattern not in str(md_file):
                    continue
            
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            
            lines = content.split('\n')
            for i, line in enumerate(lines):
                line_lower = line.lower()
                
                # Check all terms present
                if not all(term in line_lower for term in search_terms):
                    continue
                
                # Check exclusions
                if any(excl in line_lower for excl in exclude_terms):
                    continue
                
                # Check tags
                if filters.tags:
                    if not all(f"#{tag.lower()}" in line_lower for tag in filters.tags):
                        continue
                
                # Check phrase
                if filters.phrase and filters.phrase.lower() not in line_lower:
                    continue
                
                # Context
                start = max(0, i - 1)
                end = min(len(lines), i + 2)
                context = '\n'.join(lines[start:end])
                
                try:
                    rel_path = md_file.relative_to(self.vault.config.root)
                except ValueError:
                    rel_path = md_file
                
                results.append(SearchResult(
                    file=str(rel_path),
                    line=i + 1,
                    content=line.strip(),
                    context=context,
                    match_type="python"
                ))
                
                if len(results) >= limit:
                    break
            
            if len(results) >= limit:
                break
        
        return results
    
    async def search(self, query: str, limit: int = 50) -> List[SearchResult]:
        """Main search entry point."""
        filters = self.parse_query(query)
        
        if self._check_rg():
            try:
                return await self.search_rg(filters, limit)
            except Exception as e:
                logger.warning("Ripgrep failed, falling back: %s", e)
        
        return await self.search_python(filters, limit)
    
    async def search_files(self, pattern: str, limit: int = 100) -> List[Path]:
        """Find files matching pattern."""
        if self._check_rg():
            try:
                cmd = ["rg", "--files", "--glob", pattern, str(self.vault.config.root)]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                files = [Path(f).relative_to(self.vault.config.root) for f in stdout.decode().strip().split('\n') if f]
                return files[:limit]
            except Exception:
                pass
        
        # Fallback
        results = []
        for f in self.vault.config.root.rglob("*.md"):
            if pattern.replace("*", "") in str(f):
                results.append(f.relative_to(self.vault.config.root))
                if len(results) >= limit:
                    break
        return results


# Convenience
async def search(query: str, limit: int = 50) -> List[SearchResult]:
    engine = SearchEngine()
    return await engine.search(query, limit)