"""Tests for ProjectDiscoveryService and boundary validation (M2-A)."""

import os
from pathlib import Path

import pytest

from personal_os.companion.discovery import (
    ProjectDiscoveryService,
    is_path_within_roots,
    validate_path_safety,
)
from personal_os.companion.protocol import RPCErrorCode, RPCException


def test_discovery_detects_projects_and_types(tmp_path: Path):
    """Detects projects with .git, pyproject.toml, package.json, Cargo.toml."""
    root = tmp_path / "projects"
    root.mkdir()

    # Project 1: Python project
    py_proj = root / "py_proj"
    py_proj.mkdir()
    (py_proj / "pyproject.toml").write_text("[project]\nname='p1'")
    (py_proj / ".git").mkdir()

    # Project 2: Node project
    node_proj = root / "node_proj"
    node_proj.mkdir()
    (node_proj / "package.json").write_text('{"name": "n1"}')

    # Project 3: Rust project
    rust_proj = root / "rust_proj"
    rust_proj.mkdir()
    (rust_proj / "Cargo.toml").write_text('[package]\nname = "r1"')

    # Non-project directory
    plain_dir = root / "plain"
    plain_dir.mkdir()
    (plain_dir / "random.txt").write_text("hello")

    discovery = ProjectDiscoveryService(authorized_roots=[root])
    result = discovery.discover_projects()

    assert result.total == 3
    names = {p.name for p in result.projects}
    assert names == {"py_proj", "node_proj", "rust_proj"}

    p_map = {p.name: p for p in result.projects}
    assert p_map["py_proj"].project_type == "python"
    assert p_map["py_proj"].has_git is True
    assert ".git" in p_map["py_proj"].indicators
    assert "pyproject.toml" in p_map["py_proj"].indicators

    assert p_map["node_proj"].project_type == "node"
    assert p_map["rust_proj"].project_type == "rust"


def test_path_traversal_prevented(tmp_path: Path):
    """Path traversal outside authorized roots raises RPCException FORBIDDEN."""
    root = tmp_path / "authorized_root"
    root.mkdir()
    outside = tmp_path / "outside_root"
    outside.mkdir()
    secret_file = outside / "secret.txt"
    secret_file.write_text("sensitive")

    discovery = ProjectDiscoveryService(authorized_roots=[root])

    # 1. is_path_within_roots check
    assert not is_path_within_roots(outside, [root])
    assert not is_path_within_roots(secret_file, [root])
    assert is_path_within_roots(root / "sub", [root])

    # 2. validate_path_safety raises FORBIDDEN
    with pytest.raises(RPCException) as excinfo:
        validate_path_safety(secret_file, [root])
    assert excinfo.value.code == RPCErrorCode.FORBIDDEN

    # 3. stat_path on outside file raises FORBIDDEN
    with pytest.raises(RPCException) as excinfo:
        discovery.stat_path(str(secret_file))
    assert excinfo.value.code == RPCErrorCode.FORBIDDEN

    # 4. Traversal attempt using relative path raises FORBIDDEN
    with pytest.raises(RPCException) as excinfo:
        discovery.stat_path("../outside_root/secret.txt")
    assert excinfo.value.code == RPCErrorCode.FORBIDDEN


def test_symlink_safety_escaping_symlink_is_ignored(tmp_path: Path):
    """Symlink pointing outside authorized root is not followed and does not leak outside paths."""
    root = tmp_path / "authorized"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_proj = outside / "external_proj"
    outside_proj.mkdir()
    (outside_proj / "pyproject.toml").write_text("[project]\nname='ext'")

    # Create escaping symlink inside root pointing to outside_proj
    escaping_symlink = root / "symlink_to_outside"
    try:
        os.symlink(outside_proj, escaping_symlink)
    except OSError:
        pytest.skip("Symlink creation not supported on platform")

    discovery = ProjectDiscoveryService(authorized_roots=[root])
    result = discovery.discover_projects()

    # The external project must NOT be reported as discovered
    discovered_paths = [p.path for p in result.projects]
    assert str(outside_proj.resolve()) not in discovered_paths
    assert result.total == 0


def test_symlink_within_root_handled_safely(tmp_path: Path):
    """Internal symlinks within authorized root work and avoid recursion loops."""
    root = tmp_path / "authorized"
    root.mkdir()
    real_proj = root / "real_proj"
    real_proj.mkdir()
    (real_proj / "pyproject.toml").write_text("[project]\nname='real'")

    link_to_proj = root / "link_to_proj"
    try:
        os.symlink(real_proj, link_to_proj)
    except OSError:
        pytest.skip("Symlink creation not supported on platform")

    discovery = ProjectDiscoveryService(authorized_roots=[root])
    result = discovery.discover_projects()

    # Discovers real_proj without cycle or crash; deduplicates by canonical path
    assert result.total == 1
    assert result.projects[0].name == "real_proj"


def test_stat_path_authorized_file(tmp_path: Path):
    """stat_path correctly returns metadata for safe files."""
    root = tmp_path / "root"
    root.mkdir()
    file_path = root / "document.txt"
    file_path.write_text("hello world")

    discovery = ProjectDiscoveryService(authorized_roots=[root])
    stat_res = discovery.stat_path(str(file_path))

    assert stat_res.exists is True
    assert stat_res.is_file is True
    assert stat_res.is_dir is False
    assert stat_res.size_bytes == 11
    assert stat_res.modified_at is not None
