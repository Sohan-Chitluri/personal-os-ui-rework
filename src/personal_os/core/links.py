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
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from personal_os.core.config import get_config
from personal_os.core.vault import VaultManager, get_vault

logger = logging.getLogger(__name__)


class LinkGraph:
    """Discovers and proposes connections between vault notes."""

    def __init__(self, vault: Optional[VaultManager] = None):
        self.vault = vault or get_vault()
        self.root = self.vault.config.root

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

        for md_file in self.root.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
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

            try:
                rel_path = str(md_file.relative_to(self.root))
            except ValueError:
                rel_path = str(md_file)

            results.append({"file": rel_path, "score": score, "context": context})

        results.sort(key=lambda x: x["score"], reverse=True)  # type: ignore[return-value]
        return results[:limit]

    def find_orphans(self, limit: int = 20) -> List[Dict[str, str]]:
        """Find notes with no incoming or outgoing wiki-links.

        Considers every markdown note in the vault, so notes that have no
        links at all are correctly reported as orphans.
        """
        outgoing: Dict[str, set] = defaultdict(set)
        incoming: Dict[str, set] = defaultdict(set)

        for md_file in self.root.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                rel_path = str(md_file.relative_to(self.root))
            except Exception:
                continue
            for link in re.findall(r"\[\[([^\]]+)\]\]", content):
                outgoing[rel_path].add(link)
                incoming[link].add(rel_path)

        # Evaluate every note, not just those already in the link graph.
        orphans: List[Dict[str, str]] = []
        for md_file in self.root.rglob("*.md"):
            try:
                rel_path = str(md_file.relative_to(self.root))
            except ValueError:
                rel_path = str(md_file)
            if not outgoing.get(rel_path) and not incoming.get(rel_path):
                orphans.append({"file": rel_path, "title": Path(rel_path).stem})
        return orphans[:limit]

    def suggest_links(self, query: str, limit: int = 10) -> List[Dict[str, object]]:
        """Suggest links for a note/topic based on shared entities."""
        related = self.find_related_notes(query, limit=5)
        if not related:
            return [{"note": f"No notes found matching '{query}'", "shared": [], "score": 0}]

        target_file = str(related[0]["file"])
        target_content = ""
        for md_file in self.root.rglob("*.md"):
            try:
                if str(md_file.relative_to(self.root)) == target_file:
                    target_content = md_file.read_text(encoding="utf-8")
                    break
            except Exception:
                continue
        if not target_content:
            return [{"note": "Could not read target note", "shared": [], "score": 0}]

        target_entities = self.extract_entities(target_content)
        entity_names = [e[1] for e in target_entities]

        suggestions: Dict[str, Dict[str, object]] = defaultdict(
            lambda: {"shared": [], "score": 0}
        )
        for md_file in self.root.rglob("*.md"):
            try:
                rel_path = str(md_file.relative_to(self.root))
            except ValueError:
                rel_path = str(md_file)
            if rel_path == target_file:
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
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
        hub_file.write_text(hub_content, encoding="utf-8")
        try:
            rel = str(hub_file.relative_to(self.root))
        except ValueError:
            rel = str(hub_file)
        return rel


# Module-level convenience (matches the pattern used by core.search.process_entry)
_default_graph: Optional[LinkGraph] = None


def get_link_graph() -> LinkGraph:
    global _default_graph
    if _default_graph is None:
        _default_graph = LinkGraph(get_vault())
    return _default_graph


def suggest_links(query: str, limit: int = 10) -> List[Dict[str, object]]:
    return get_link_graph().suggest_links(query, limit=limit)


def find_related(query: str, limit: int = 10) -> List[Dict[str, object]]:
    return get_link_graph().find_related_notes(query, limit=limit)


def find_orphans(limit: int = 20) -> List[Dict[str, str]]:
    return get_link_graph().find_orphans(limit=limit)


def create_hub(topic: str) -> str:
    return get_link_graph().create_hub(topic)
