"""Wave 3 polish: SearchEngine fallback uses logging, never print (TUI-safe).

The ripgrep fallback path must not write to stdout — a stray print corrupts a
running Textual app. Verify the fallback logs via `logging` instead.
"""
import builtins
import logging

from personal_os.core.search import SearchEngine


async def test_rg_failure_falls_back_without_print(isolated_vault, caplog, monkeypatch):
    """If rg raises, SearchEngine falls back to Python search using logging."""
    # asyncio_mode = "auto" in pyproject.toml, so plain async def works.
    engine = SearchEngine()

    # Force the rg path to fail so the fallback branch executes.
    async def boom(filters, limit=50):
        raise RuntimeError("simulated rg failure")

    engine.search_rg = boom
    engine._has_rg = True

    printed = []
    monkeypatch.setattr(builtins, "print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    caplog.set_level(logging.WARNING)

    results = await engine.search("anything")

    assert printed == [], "SearchEngine fallback must not print() to stdout"
    assert any("falling back" in r.getMessage() for r in caplog.records), (
        "fallback should be reported through logging, not stdout"
    )
    assert isinstance(results, list)
