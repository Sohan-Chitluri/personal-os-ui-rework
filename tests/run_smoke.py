#!/usr/bin/env python3
"""Dependency-free smoke runner for Personal OS.

Fallback for environments without pytest. Creates an isolated temp vault
(redirecting HOME + PERSONAL_OS_VAULT), resets singletons, and runs each
smoke scenario, printing PASS/FAIL with a final count.

Run:  PYTHONPATH=src python tests/run_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

RESULTS: list[tuple[str, bool, str]] = []


def _reset_singletons():
    import personal_os.core.config as config
    import personal_os.core.vault as vault
    config._config = None
    vault._vault = None
    try:
        import personal_os.core.links as links
        links._default_graph = None
    except Exception:
        pass
    try:
        import personal_os.core.weekly_review as wr
        wr._default_generator = None
    except Exception:
        pass


def setup_isolation(base: Path) -> Path:
    vault_root = base / "vault"
    fake_home = base / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(fake_home)
    os.environ["PERSONAL_OS_VAULT"] = str(vault_root)
    os.environ.setdefault("GIT_AUTHOR_NAME", "Smoke Test")
    os.environ.setdefault("GIT_AUTHOR_EMAIL", "smoke@test.local")
    os.environ.setdefault("GIT_COMMITTER_NAME", "Smoke Test")
    os.environ.setdefault("GIT_COMMITTER_EMAIL", "smoke@test.local")
    _reset_singletons()
    from personal_os.core.config import get_config
    resolved = get_config().vault.root
    print(f"[isolation] resolved vault root = {resolved}")
    assert str(resolved) == str(vault_root), "vault not isolated!"
    return vault_root


def check(name: str, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"PASS  {name}")
    except Exception:
        tb = traceback.format_exc()
        RESULTS.append((name, False, tb))
        print(f"FAIL  {name}\n{tb}")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        vault_root = setup_isolation(Path(td))
        from personal_os.core.config import VaultConfig
        from personal_os.core.vault import VaultManager
        from personal_os.core.processor import process_entry, ProcessingResult
        from personal_os.core.search import search, SearchResult
        from personal_os.core import links, weekly_review

        def t_cli():
            env = {**os.environ, "PYTHONPATH": str(SRC)}
            p = subprocess.run(
                [sys.executable, "-m", "personal_os.cli.main", "--help"],
                capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
            )
            out = p.stdout + p.stderr
            assert p.returncode == 0, out
            assert "sage" in out, out  # Usage/usage

        def t_vault():
            vm = VaultManager(VaultConfig(root=vault_root))
            for d in ["00-Inbox", "01-Academic", "02-Projects", "03-Skills",
                      "04-Personal/Journal", "05-Resources", "06-Archive", "Templates"]:
                assert (vault_root / d).is_dir(), d
            assert (vault_root / ".git").is_dir()
            assert vm._repo is not None

        def t_project():
            vm = VaultManager(VaultConfig(root=vault_root))
            pd = vm.create_project("smoke-test", "desc")
            for f in ["README.md", "Progress.md", "Tasks.md", "Notes.md",
                      "Architecture.md", "Decisions.md", "Research.md", "Timeline.md"]:
                assert (pd / f).is_file(), f

        def t_note():
            vm = VaultManager(VaultConfig(root=vault_root))
            path = vm.append_journal_entry("Smoke test journal entry", source="Text")
            assert path.name == f"{datetime.now():%Y-%m-%d}.md"
            assert "Smoke test journal entry" in path.read_text()

        def t_process():
            VaultManager(VaultConfig(root=vault_root))
            r = asyncio.run(process_entry("Spent 2 hours on Maestro fixing the router bug", "Text"))
            assert isinstance(r, ProcessingResult)
            assert not r.errors, r.errors
            assert r.extractions or r.changes_made

        def t_search():
            vm = VaultManager(VaultConfig(root=vault_root))
            vm.write_file(vault_root / "05-Resources" / "note.md",
                          "# Note\n\nkeyword zebrafish here\n")
            res = asyncio.run(search("zebrafish", limit=10))
            assert isinstance(res, list) and len(res) >= 1
            assert all(isinstance(x, SearchResult) for x in res)

        def t_links():
            vm = VaultManager(VaultConfig(root=vault_root))
            vm.write_file(vault_root / "05-Resources" / "a.md", "# A\n\nwidgets detail\n")
            vm.write_file(vault_root / "05-Resources" / "b.md", "# B\n\nwidgets and gadgets\n")
            assert isinstance(links.find_related("widgets"), list)
            assert len(links.find_related("widgets")) >= 1
            assert isinstance(links.suggest_links("widgets"), list)
            assert isinstance(links.find_orphans(), list)

        def t_review():
            VaultManager(VaultConfig(root=vault_root))
            out = weekly_review.generate_review()
            assert isinstance(out, Path) and out.is_file() and out.suffix == ".md"
            assert (vault_root / "04-Personal" / "Reviews" / "weekly") in out.parents

        check("cli_help", t_cli)
        check("vault_init", t_vault)
        check("create_project", t_project)
        check("note_capture", t_note)
        check("process_entry", t_process)
        check("search", t_search)
        check("links", t_links)
        check("weekly_review", t_review)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n==== SMOKE SUMMARY: {passed}/{total} passed ====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
