import asyncio
import os
from pathlib import Path
from datetime import datetime, timedelta

import pytest

from personal_os.core.vault import VaultManager, VaultConfig
from personal_os.core.search import SearchEngine, SearchFilters
from personal_os.core.links import LinkGraph, LinkIndex


@pytest.fixture
def temp_vault(tmp_path: Path):
    vault_root = tmp_path / "custom_vault_location"
    vault_root.mkdir()
    config = VaultConfig(root=vault_root)
    return VaultManager(config)


# --- Finding #7: Quoted Multi-word Search ---

def test_parse_query_quoted_phrase():
    engine = SearchEngine()

    # Single quoted multi-word phrase with tag
    filters = engine.parse_query('tag:research "fixed point arithmetic"')
    assert filters.phrase == "fixed point arithmetic"
    assert "research" in filters.tags

    # Multiple terms and quoted phrase
    filters2 = engine.parse_query('project:DSP "cordic pipeline" status:active')
    assert filters2.phrase == "cordic pipeline"
    assert filters2.project == "DSP"

    # Graceful fallback on unbalanced quote
    filters3 = engine.parse_query('tag:notes "unclosed quote test')
    assert "notes" in filters3.tags
    assert any("unclosed" in t for t in filters3.terms)


@pytest.mark.asyncio
async def test_search_python_matches_quoted_phrase(temp_vault: VaultManager):
    engine = SearchEngine(vault=temp_vault)
    notes_dir = temp_vault.config.root / "01-Notes"
    notes_dir.mkdir(parents=True)

    note1 = notes_dir / "note1.md"
    note1.write_text("This section covers fixed point arithmetic in detail.\n")

    note2 = notes_dir / "note2.md"
    note2.write_text("This has fixed and point on different ideas entirely arithmetic.\n")

    filters = engine.parse_query('"fixed point arithmetic"')
    results = await engine.search_python(filters)
    assert len(results) == 1
    assert "fixed point arithmetic" in results[0].content


# --- Finding #8: Wiki-links and Orphan Notes ---

def test_find_orphans_recognizes_title_and_stem_wikilinks(temp_vault: VaultManager):
    notes_dir = temp_vault.config.root / "01-Notes"
    notes_dir.mkdir(parents=True)

    # Note A: Referenced by stem [[NoteA]]
    note_a = notes_dir / "NoteA.md"
    note_a.write_text("# Title of Note A\nContent here.\n")

    # Note B: Referenced by title [[Note B Title]]
    note_b = notes_dir / "NoteB.md"
    note_b.write_text("# Note B Title\nContent here.\n")

    # Referencing note linking both
    linker = notes_dir / "Linker.md"
    linker.write_text("See [[NoteA]] and also [[Note B Title]].\n")

    # Truly orphan note
    orphan = notes_dir / "Orphan.md"
    orphan.write_text("# Lone Wolf\nNo one links to me and I link to no one.\n")

    graph = LinkGraph(vault=temp_vault)
    orphans = graph.find_orphans()
    orphan_files = [o["file"] for o in orphans]

    assert "01-Notes/Orphan.md" in orphan_files
    assert "01-Notes/NoteA.md" not in orphan_files
    assert "01-Notes/NoteB.md" not in orphan_files


# --- Finding #11: Date Filtering with Vault Outside CWD ---

def test_date_filtering_with_external_vault(temp_vault: VaultManager):
    # Ensure CWD is different from vault.config.root
    assert Path.cwd() != temp_vault.config.root

    engine = SearchEngine(vault=temp_vault)
    journal_dir = temp_vault.config.root / "04-Personal" / "Journal"
    journal_dir.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_journal = journal_dir / f"{today_str}.md"
    today_journal.write_text("Today's journal entry.\n")

    # Test relative path check resolves via vault root
    rel_path = Path("04-Personal/Journal") / f"{today_str}.md"
    assert engine._matches_date_range(rel_path, "today") is True
    assert engine._matches_date_range(rel_path, "yesterday") is False


# --- Finding #13 & #15: Link Graph Suggestions and Hub Generation ---

def test_suggest_links_and_create_hub(temp_vault: VaultManager):
    notes_dir = temp_vault.config.root / "01-Notes"
    notes_dir.mkdir(parents=True)

    note1 = notes_dir / "DSP.md"
    note1.write_text("# DSP\nWorking with fpga and verilog implementations.\n")

    note2 = notes_dir / "FPGA_Guide.md"
    note2.write_text("# FPGA Guide\nBest practices for fpga development.\n")

    graph = LinkGraph(vault=temp_vault)
    suggestions = graph.suggest_links("DSP", limit=5)
    assert len(suggestions) > 0

    # Test create_hub uses vault.write_file
    hub_rel = graph.create_hub("DSP")
    hub_path = temp_vault.config.root / hub_rel
    assert hub_path.exists()
    assert "# Dsp Hub" in hub_path.read_text()


def test_link_index_cache_reuse_and_invalidation(temp_vault: VaultManager, monkeypatch):
    """LinkIndex caches note content and wikilinks; repeated queries do not re-read from disk."""
    notes_dir = temp_vault.config.root / "01-Notes"
    notes_dir.mkdir(parents=True)

    n1 = notes_dir / "Alpha.md"
    n1.write_text("# Alpha\nMentions [[Beta]] and FPGA technology.\n", encoding="utf-8")

    n2 = notes_dir / "Beta.md"
    n2.write_text("# Beta\nMentions FPGA architecture.\n", encoding="utf-8")

    index = LinkIndex(temp_vault.config.root)
    graph = LinkGraph(vault=temp_vault, index=index)

    # First query works
    res1 = graph.find_related_notes("FPGA")
    assert len(res1) == 2

    # Instrument Path.read_text to count disk reads
    read_calls = []
    orig_read_text = Path.read_text

    def counted_read_text(self, *args, **kwargs):
        read_calls.append(str(self))
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    # Subsequent queries reuse cached content and wikilinks without re-reading
    res2 = graph.find_related_notes("FPGA")
    assert len(res2) == 2
    orphans = graph.find_orphans()
    orphan_files = [o["file"] for o in orphans]
    assert "01-Notes/Alpha.md" not in orphan_files
    assert "01-Notes/Beta.md" not in orphan_files
    assert len(read_calls) == 0  # Zero disk reads!


def test_link_index_handles_edits_and_deletions(temp_vault: VaultManager):
    """Note edits update cache on mtime change, and deletions are pruned from index."""
    notes_dir = temp_vault.config.root / "01-Notes"
    notes_dir.mkdir(parents=True)

    n1 = notes_dir / "Note1.md"
    n1.write_text("# Note 1\nInitial content.\n", encoding="utf-8")

    index = LinkIndex(temp_vault.config.root)
    graph = LinkGraph(vault=temp_vault, index=index)

    assert len(graph.find_related_notes("UpdatedKeyword")) == 0

    # Edit note and update mtime
    import time
    time.sleep(0.01)
    n1.write_text("# Note 1\nNow contains UpdatedKeyword.\n", encoding="utf-8")

    # Rebuild detects mtime change and refreshes cache
    index.rebuild()
    res = graph.find_related_notes("UpdatedKeyword")
    assert len(res) == 1
    assert "01-Notes/Note1.md" in str(res[0]["file"])

    # Delete note
    n1.unlink()
    index.rebuild()
    assert len(graph.find_related_notes("UpdatedKeyword")) == 0
    assert "01-Notes/Note1.md" not in index.notes
