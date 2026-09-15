"""Wave 3 polish: SearchEngine fallback uses logging, never print (TUI-safe).

The ripgrep fallback path must not write to stdout — a stray print corrupts a
running Textual app. Verify the fallback logs via `logging` instead.
"""
import builtins
import logging

import pytest

from personal_os.core.search import SearchEngine
from personal_os.core.config import VaultConfig
from personal_os.core.vault import VaultManager


@pytest.mark.asyncio
async def test_rg_failure_falls_back_without_print(isolated_vault, caplog):
    """If rg raises, SearchEngine falls back to Python search using logging."""
    vm = VaultManager(VaultConfig(root=isolated_vault))
    engine = SearchEngine(vault=vm)

    # Force the rg path to fail so the fallback branch executes.
    async def boom(filters, limit):
        raise RuntimeError("simulated rg failure")

    engine.search_rg = boom
    engine._has_rg = True

    printed = []
    orig_print = builtins.print
    builtins.print = lambda *a, **k: printed.append(" ".join(map(str, a)))
    try:
        with caplog.at_level(logging.WARNING, logger="personal_os.core.search"):
            results = await engine.search("anything")
    finally:
        builtins.print = orig_print

    assert printed == [], "SearchEngine fallback must not print() to stdout"
    assert any(
        "falling back" in r.getMessage().lower() for r in caplog.records
    ), "fallback should be reported through logging, not stdout"
    assert isinstance(results, list)
