"""Pytest fixtures for Personal OS smoke tests.

CRITICAL ISOLATION: every test runs against a throwaway vault created under a
pytest tmp_path. We set PERSONAL_OS_VAULT *and* redirect HOME so that config
(~/.config/personal-os) and caches never touch the real user environment. The
module-level singletons in core.config / core.vault / core.links /
core.weekly_review are reset before and after each test so no state leaks
between the real vault and the temp vault.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


def _reset_singletons():
    """Reset all module-level singletons so a fresh config/vault is built."""
    import personal_os.core.config as config
    import personal_os.core.vault as vault
    config._config = None
    vault._vault = None
    # links / weekly_review cache their own graph/generator built from get_vault()
    try:
        import personal_os.core.links as links
        links._default_graph = None
    except Exception:
        pass
    try:
        import personal_os.core.weekly_review as weekly_review
        weekly_review._default_generator = None
    except Exception:
        pass


@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    """Provide an isolated, git-initialized vault rooted in tmp_path.

    Yields the resolved vault root Path. Prints it so isolation is provable in
    the captured test output (`pytest -s`).
    """
    vault_root = tmp_path / "vault"
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)

    # Redirect HOME so ~/.config/personal-os lands in temp too.
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("PERSONAL_OS_VAULT", str(vault_root))
    # Give git a committer identity inside the sandbox (fresh HOME has none).
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Smoke Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "smoke@test.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Smoke Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "smoke@test.local")

    _reset_singletons()

    from personal_os.core.config import get_config

    cfg = get_config()
    # Prove isolation: resolved root must be the temp path, never ~/projects.
    resolved = cfg.vault.root
    print(f"\n[isolation] resolved vault root = {resolved}")
    assert str(resolved) == str(vault_root), (
        f"Vault not isolated! got {resolved}, expected {vault_root}"
    )
    assert "projects/personal-os" not in str(resolved) or str(tmp_path) in str(resolved)

    yield vault_root

    _reset_singletons()
