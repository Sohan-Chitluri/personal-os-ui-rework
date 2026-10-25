"""Knowledge-graph service for Personal OS.

Ports the legacy link_notes behavior into a reusable, editor-agnostic module
driven by the canonical VaultManager. Read-only operations never modify the
vault; only create_hub() writes a file.

Reusable by the processor, TUI, and future AI components without going through
the CLI.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from personal_os.core.config import get_config
from personal_os.core.vault import VaultManager, get_vault

logger = logging.getLogger(__name__)


@dataclass
class NoteInfo:
    path: Path
    rel_path: str
    stem: str
    title: str = ""
    content: str = ""
    mtime: float = 0.0
    wikilinks: list[str] = field(default_factory=list)


def _extract_title_from_content(content: str, fallback: str) -> str:
    """Extract note title from markdown content."""
    if not content:
        return fallback
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        elif line.startswith("## "):
            return line[3:].strip()
    return fallback


def _extract_title_from_file(path: Path, fallback: str) -> str:
    """Extract note title from markdown file."""
    try:
        content = path.read_text(encoding="utf-8")
        return _extract_title_from_content(content, fallback)
    except Exception:
        return fallback


class LinkIndex:
    """Index of vault notes and links for fast lookup."""

    def __init__(self, root: Path):
        self.root = root
        self.notes: Dict[str, NoteInfo] = {}
        self.rebuild()

    def rebuild(self, force: bool = False) -> None:
        """Scan vault and populate note index, caching note content and wikilinks."""
        observed_rel_paths: set[str] = set()
        for md_file in self.root.rglob("*.md"):
            try:
                rel = str(md_file.relative_to(self.root))
            except ValueError:
                rel = str(md_file)
            observed_rel_paths.add(rel)

            try:
                mtime = md_file.stat().st_mtime
            except OSError:
                continue

            # Cache hit: reuse cached content if file has not been modified
            if not force and rel in self.notes and self.notes[rel].mtime == mtime:
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                content = ""

            stem = md_file.stem
            title = _extract_title_from_content(content, stem)
            wikilinks = [
                raw_link.split("|")[0].split("#")[0].strip()
                for raw_link in re.findall(r"\[\[([^\]]+)\]\]", content)
            ]
            self.notes[rel] = NoteInfo(
                path=md_file,
                rel_path=rel,
                stem=stem,
                title=title,
                content=content,
                mtime=mtime,
                wikilinks=wikilinks,
            )

        # Invalidate deleted notes no longer present on filesystem
        deleted_keys = [k for k in self.notes if k not in observed_rel_paths]
        for k in deleted_keys:
            del self.notes[k]

    def invalidate(self, path: Path | str) -> None:
        """Explicitly invalidate or refresh a single note in the index."""
        p = Path(path)
        try:
            rel = str(p.relative_to(self.root)) if p.is_absolute() else str(p)
        except ValueError:
            rel = str(p)

        full_path = p if p.is_absolute() else (self.root / p)
        if not full_path.exists() or not full_path.is_file():
            self.notes.pop(rel, None)
            return

        try:
            mtime = full_path.stat().st_mtime
            content = full_path.read_text(encoding="utf-8")
            stem = full_path.stem
            title = _extract_title_from_content(content, stem)
            wikilinks = [
                raw_link.split("|")[0].split("#")[0].strip()
                for raw_link in re.findall(r"\[\[([^\]]+)\]\]", content)
            ]
            self.notes[rel] = NoteInfo(
                path=full_path,
                rel_path=rel,
                stem=stem,
                title=title,
                content=content,
                mtime=mtime,
                wikilinks=wikilinks,
            )
        except Exception:
            self.notes.pop(rel, None)

    def get_content(self, path: Path | str) -> str:
        """Get note content from cache, verifying mtime to prevent stale reads."""
        p = Path(path)
        try:
            rel = str(p.relative_to(self.root)) if p.is_absolute() else str(p)
        except ValueError:
            rel = str(p)

        cached = self.notes.get(rel)
        full_path = p if p.is_absolute() else (self.root / p)

        try:
            current_mtime = full_path.stat().st_mtime
        except OSError:
            if cached:
                self.notes.pop(rel, None)
            return ""

        if cached is not None and cached.mtime == current_mtime:
            return cached.content

        self.invalidate(full_path)
        refreshed = self.notes.get(rel)
        return refreshed.content if refreshed else ""


class LinkGraph:
    """Discovers and proposes connections between vault notes."""

    def __init__(self, vault: Optional[VaultManager] = None, index: Optional[Any] = None):
        self.vault = vault or get_vault()
        self.root = self.vault.config.root
        self.index = index if index is not None else LinkIndex(self.root)

    def _iter_notes(self):
        """Iterate over (rel_path, Path, NoteInfo or None) from index or filesystem."""
        if hasattr(self, "index") and hasattr(self.index, "notes") and self.index.notes:
            notes = self.index.notes
            if isinstance(notes, dict):
                for key, val in notes.items():
                    if hasattr(val, "path"):
                        yield str(key), val.path, val
                    elif isinstance(val, Path):
                        yield str(key), val, None
                    else:
                        p = self.root / str(key)
                        yield str(key), p, None
            else:
                for item in notes:
                    if hasattr(item, "path"):
                        rel = getattr(item, "rel_path", None) or str(item.path.relative_to(self.root))
                        yield str(rel), item.path, item
                    elif isinstance(item, Path):
                        try:
                            rel = str(item.relative_to(self.root))
                        except ValueError:
                            rel = str(item)
                        yield rel, item, None
                    else:
                        rel = str(item)
                        yield rel, self.root / rel, None
        else:
            for md_file in self.root.rglob("*.md"):
                try:
                    rel = str(md_file.relative_to(self.root))
                except ValueError:
                    rel = str(md_file)
                yield rel, md_file, None

    # ---- entity extraction ----

    def extract_entities(self, content: str) -> List[Tuple[str, str]]:
        """Extract linkable entities: (type, value)."""
        entities: List[Tuple[str, str]] = []

        # Wiki-links
        for link in re.findall(r"\[\[([^\]]+)\]\]", content):
            entities.append(("wikilink", link))
        # Tags
        for tag in re.findall(r"#([a-zA-Z][a-zA-Z0-9_-]*)", content):
            entities.append(("tag", tag))

        # Project names (directory names under 02-Projects)
        projects_dir = self.vault.config.projects_dir
        if projects_dir.exists():
            for project_dir in projects_dir.iterdir():
                if project_dir.is_dir() and project_dir.name.lower() in content.lower():
                    entities.append(("project", project_dir.name))

        # Technology keywords
        tech_keywords = [
            "stm32", "esp32", "fpga", "vivado", "vitis", "verilog", "systemverilog",
            "axi", "uart", "spi", "i2c", "dma", "rtos", "freertos",
            "rust", "python", "c++", "cmake", "git", "linux", "nixos",
        ]
        for kw in tech_keywords:
            if kw.lower() in content.lower():
                entities.append(("tech", kw))

        return entities

    # ---- read-only analysis ----

    def find_related_notes(self, query: str, limit: int = 10) -> List[Dict[str, object]]:
        """Find notes related to a query, scored by mention count/title/links."""
        results: List[Dict[str, object]] = []
        query_lower = query.lower()

        for rel_path, md_file, note_info in self._iter_notes():
            content = getattr(note_info, "content", None)
            if content is None:
                if hasattr(self, "index") and hasattr(self.index, "get_content"):
                    content = self.index.get_content(md_file)
                else:
                    try:
                        content = md_file.read_text(encoding="utf-8")
                    except Exception:
                        continue
            if not content:
                continue

            content_lower = content.lower()
            score = content_lower.count(query_lower)
            if score == 0:
                continue

            if query_lower in md_file.stem.lower():
                score += 5
            if f"[[{query}]]" in content or f"[[{query.lower()}]]" in content_lower:
                score += 3

            context = ""
            for line in content.split("\n"):
                if query_lower in line.lower():
                    context = line.strip()[:150]
                    break
            else:
                context = content[:150]

            results.append({"file": rel_path, "score": score, "context": context})

        results.sort(key=lambda x: x["score"], reverse=True)  # type: ignore[return-value]
        return results[:limit]

    def find_orphans(self, limit: int = 20) -> List[Dict[str, str]]:
        """Find notes with no incoming or outgoing wiki-links.

        Considers every markdown note in the vault, matching incoming links against
        note title, filename stem, and full rel_path consistently with the link index,
        so notes linked via [[Title]] or [[stem]] are not classified as orphans.
        """
        outgoing: Dict[str, set] = defaultdict(set)
        incoming: Dict[str, set] = defaultdict(set)
        notes_metadata: Dict[str, Tuple[str, str]] = {}  # rel_path -> (stem, title)

        for rel_path, md_file, note_info in self._iter_notes():
            stem = Path(rel_path).stem
            title = getattr(note_info, "title", None)
            wikilinks = getattr(note_info, "wikilinks", None)

            if wikilinks is None or title is None:
                content = getattr(note_info, "content", None)
                if content is None:
                    if hasattr(self, "index") and hasattr(self.index, "get_content"):
                        content = self.index.get_content(md_file)
                    else:
                        try:
                            content = md_file.read_text(encoding="utf-8")
                        except Exception:
                            continue
                title = title or _extract_title_from_content(content, stem)
                wikilinks = [
                    raw_link.split("|")[0].split("#")[0].strip()
                    for raw_link in re.findall(r"\[\[([^\]]+)\]\]", content)
                ]

            notes_metadata[rel_path] = (stem, title)

            for target in wikilinks:
                outgoing[rel_path].add(target)
                incoming[target].add(rel_path)
                incoming[target.lower()].add(rel_path)

        orphans: List[Dict[str, str]] = []
        for rel_path, (stem, title) in notes_metadata.items():
            has_outgoing = bool(outgoing.get(rel_path))
            has_incoming = (
                bool(incoming.get(rel_path))
                or bool(incoming.get(rel_path.lower()))
                or bool(incoming.get(stem))
                or bool(incoming.get(stem.lower()))
                or (bool(title) and (bool(incoming.get(title)) or bool(incoming.get(title.lower()))))
            )
            if not has_outgoing and not has_incoming:
                orphans.append({"file": rel_path, "title": stem})

        return orphans[:limit]

    def suggest_links(self, query: str, limit: int = 10) -> List[Dict[str, object]]:
        """Suggest links for a note/topic based on shared entities."""
        related = self.find_related_notes(query, limit=5)
        if not related:
            return [{"note": f"No notes found matching '{query}'", "shared": [], "score": 0}]

        target_file = str(related[0]["file"])
        target_path = Path(target_file) if Path(target_file).is_absolute() else (self.root / target_file)
        try:
            target_content = target_path.read_text(encoding="utf-8")
        except Exception:
            target_content = ""

        if not target_content:
            return [{"note": "Could not read target note", "shared": [], "score": 0}]

        target_entities = self.extract_entities(target_content)

        suggestions: Dict[str, Dict[str, object]] = defaultdict(
            lambda: {"shared": [], "score": 0}
        )

        # Read known candidate files directly from self.index.notes rather than rescanning the full vault
        for rel_path, candidate_path, note_info in self._iter_notes():
            if rel_path == target_file:
                continue
            try:
                content = getattr(note_info, "content", None) or candidate_path.read_text(encoding="utf-8")
            except Exception:
                continue
            content_lower = content.lower()
            for entity_type, entity_name in target_entities:
                if entity_name.lower() in content_lower:
                    suggestions[rel_path]["shared"].append(entity_name)  # type: ignore[union-attr]
                    suggestions[rel_path]["score"] += 1  # type: ignore[operator]

        ranked = sorted(suggestions.items(), key=lambda x: x[1]["score"], reverse=True)  # type: ignore[return-value]
        return [
            {
                "file": file_path,
                "shared_entities": data["shared"][:5],  # type: ignore[index]
                "score": data["score"],  # type: ignore[typeddict-item]
            }
            for file_path, data in ranked[:limit]
        ]

    # ---- write operation ----

    def create_hub(self, topic: str) -> str:
        """Create a hub/index note for a topic. Idempotent on topic name.

        Returns the relative path of the created/overwritten hub file.
        """
        related = self.find_related_notes(topic, limit=30)

        hub_content = (
            f"# {topic.title()} Hub\n\n"
            f"*Auto-generated on {datetime.now().strftime('%Y-%m-%d')}*\n\n"
            f"## Related Notes ({len(related)})\n\n"
        )

        by_dir: Dict[str, List[Dict[str, object]]] = defaultdict(list)
        for r in related:
            by_dir[str(Path(str(r["file"])).parent)].append(r)

        for dir_name, items in sorted(by_dir.items()):
            hub_content += f"## {dir_name}\n"
            for item in items:
                stem = Path(str(item["file"])).stem
                hub_content += f"- [[{stem}]] (score: {item['score']})\n"
            hub_content += "\n"

        hub_content += (
            "\n---\n"
            "*Generated by personal_os LinkGraph*\n"
            f"*Query: {topic}*\n"
        )

        hub_dir = self.root / "05-Resources" / "Hubs"
        hub_dir.mkdir(parents=True, exist_ok=True)
        hub_file = hub_dir / f"{topic.lower().replace(' ', '-')}-hub.md"
        self.vault.write_file(hub_file, hub_content)
        try:
            rel = str(hub_file.relative_to(self.root))
        except ValueError:
            rel = str(hub_file)
        if hasattr(self.index, "rebuild"):
            self.index.rebuild()
        return rel


# Module-level convenience (matches the pattern used by core.search.process_entry)
_default_graph: Optional[LinkGraph] = None


def get_link_graph() -> LinkGraph:
    global _default_graph
    current_vault = get_vault()
    if _default_graph is None or _default_graph.vault is not current_vault:
        _default_graph = LinkGraph(current_vault)
    return _default_graph


def suggest_links(query: str, limit: int = 10) -> List[Dict[str, object]]:
    return get_link_graph().suggest_links(query, limit=limit)


def find_related(query: str, limit: int = 10) -> List[Dict[str, object]]:
    return get_link_graph().find_related_notes(query, limit=limit)


def find_orphans(limit: int = 20) -> List[Dict[str, str]]:
    return get_link_graph().find_orphans(limit=limit)


def create_hub(topic: str) -> str:
    return get_link_graph().create_hub(topic)
