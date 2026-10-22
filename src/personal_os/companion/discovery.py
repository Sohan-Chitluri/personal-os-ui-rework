"""Local project discovery and filesystem safety boundaries (M2-A).

Enforces:
- Scanning strictly bounded to explicitly authorized roots.
- Canonicalization and strict path traversal prevention.
- Symlink safety: symlinks pointing outside authorized roots are skipped/rejected.
- Robust handling of unreadable directories, deleted paths, cycles, and duplicates.
- Returns only high-level metadata (no arbitrary file contents).
"""

from __future__ import annotations

import logging
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from personal_os.companion.protocol import (
    DiscoveredProject,
    DiscoveryResult,
    FileStatResult,
    RPCErrorCode,
    RPCException,
)

log = logging.getLogger("personal_os.companion.discovery")

PROJECT_INDICATORS = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "package.json": "node",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "jvm",
    "build.gradle": "jvm",
    "Makefile": "c/cpp",
    "CMakeLists.txt": "c/cpp",
}


def is_path_within_roots(path: Path, authorized_roots: Sequence[Path]) -> bool:
    """Return True if path resolves strictly within at least one authorized root."""
    try:
        resolved = path.resolve()
        for root in authorized_roots:
            resolved_root = root.resolve()
            if resolved == resolved_root or resolved.is_relative_to(resolved_root):
                return True
        return False
    except (OSError, RuntimeError):
        return False


def validate_path_safety(path: Path, authorized_roots: Sequence[Path]) -> Path:
    """Validate that path is strictly within authorized roots.

    Raises:
        RPCException: with FORBIDDEN if path traverses outside authorized roots.
    """
    if not is_path_within_roots(path, authorized_roots):
        raise RPCException(
            code=RPCErrorCode.FORBIDDEN,
            message="Path traversal forbidden: path is outside authorized roots",
            data={
                "requested_path": str(path),
                "authorized_roots": [str(r.resolve()) for r in authorized_roots],
            },
        )
    return path.resolve()


class ProjectDiscoveryService:
    """Discovers projects safely under configured root directories."""

    def __init__(self, authorized_roots: Sequence[Path]) -> None:
        self.authorized_roots = [r.resolve() for r in authorized_roots]

    def discover_projects(
        self,
        target_root: str | None = None,
        max_depth: int = 4,
    ) -> DiscoveryResult:
        """Scan authorized roots for software projects up to max_depth."""
        roots_to_scan: list[Path] = []
        if target_root:
            target_path = Path(target_root)
            validated = validate_path_safety(target_path, self.authorized_roots)
            roots_to_scan.append(validated)
        else:
            roots_to_scan = list(self.authorized_roots)

        discovered: list[DiscoveredProject] = []
        seen_canonical_paths: set[str] = set()

        for root in roots_to_scan:
            if not root.exists() or not root.is_dir():
                log.warning("Configured root does not exist or is not a directory: %s", root)
                continue
            self._scan_directory(
                current_dir=root,
                current_depth=0,
                max_depth=max_depth,
                discovered=discovered,
                seen_paths=seen_canonical_paths,
            )

        return DiscoveryResult(
            projects=discovered,
            scanned_roots=[str(r) for r in roots_to_scan],
            total=len(discovered),
        )

    def _scan_directory(
        self,
        current_dir: Path,
        current_depth: int,
        max_depth: int,
        discovered: list[DiscoveredProject],
        seen_paths: set[str],
    ) -> None:
        """Recursively scan directory with boundary and symlink checks."""
        try:
            canonical = str(current_dir.resolve())
        except (OSError, RuntimeError) as exc:
            log.debug("Cannot resolve %s: %s", current_dir, exc)
            return

        if canonical in seen_paths:
            return
        seen_paths.add(canonical)

        # Check if current directory represents a project
        proj = self._inspect_as_project(current_dir)
        if proj:
            discovered.append(proj)
            # If it's a project with .git, typically we don't deeply scan internal subdirs
            # unless looking for monorepos, but limit recursion depth.

        if current_depth >= max_depth:
            return

        try:
            entries = list(os.scandir(current_dir))
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError) as exc:
            log.debug("Skipping unreadable directory %s: %s", current_dir, exc)
            return

        for entry in entries:
            # Skip hidden directories like .git, .cache, .venv, node_modules
            if entry.name.startswith(".") or entry.name in (
                "node_modules",
                "__pycache__",
                ".venv",
                "venv",
                "env",
                "dist",
                "build",
                "target",
            ):
                continue

            entry_path = current_dir / entry.name

            # Symlink safety check
            if entry.is_symlink():
                try:
                    resolved_target = entry_path.resolve(strict=True)
                    if not is_path_within_roots(resolved_target, self.authorized_roots):
                        log.debug(
                            "Skipping symlink escaping authorized roots: %s -> %s",
                            entry_path,
                            resolved_target,
                        )
                        continue
                except (OSError, RuntimeError):
                    continue

            if entry.is_dir(follow_symlinks=False):
                self._scan_directory(
                    current_dir=entry_path,
                    current_depth=current_depth + 1,
                    max_depth=max_depth,
                    discovered=discovered,
                    seen_paths=seen_paths,
                )

    def _inspect_as_project(self, dir_path: Path) -> DiscoveredProject | None:
        """Check if directory has project indicators."""
        indicators: list[str] = []
        project_type = "generic"
        has_git = False
        mtime: datetime | None = None

        try:
            st = dir_path.stat()
            mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
        except OSError:
            pass

        # Check for .git
        git_dir = dir_path / ".git"
        if git_dir.exists():
            has_git = True
            indicators.append(".git")
            project_type = "git"

        # Check other indicators
        for filename, ptype in PROJECT_INDICATORS.items():
            candidate = dir_path / filename
            if candidate.exists():
                indicators.append(filename)
                project_type = ptype

        if not indicators:
            return None

        return DiscoveredProject(
            name=dir_path.name,
            path=str(dir_path.resolve()),
            project_type=project_type,
            has_git=has_git,
            indicators=indicators,
            last_modified=mtime,
        )

    def stat_path(self, target_path: str) -> FileStatResult:
        """Inspect file or directory metadata strictly within authorized roots."""
        path = Path(target_path)
        # Even if file does not exist, check if its parent or path lies inside authorized root
        if path.is_absolute():
            validated = validate_path_safety(path, self.authorized_roots)
        else:
            # Relative to first authorized root
            if not self.authorized_roots:
                raise RPCException(
                    code=RPCErrorCode.FORBIDDEN, message="No authorized roots configured"
                )
            candidate = (self.authorized_roots[0] / path).resolve()
            validated = validate_path_safety(candidate, self.authorized_roots)

        exists = validated.exists()
        is_dir = validated.is_dir() if exists else False
        is_file = validated.is_file() if exists else False
        is_symlink = validated.is_symlink()
        size_bytes = 0
        mtime: datetime | None = None

        if exists:
            try:
                st = validated.stat()
                size_bytes = st.st_size
                mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
            except OSError as exc:
                log.debug("Error stating file %s: %s", validated, exc)

        return FileStatResult(
            path=str(validated),
            exists=exists,
            is_dir=is_dir,
            is_file=is_file,
            is_symlink=is_symlink,
            size_bytes=size_bytes,
            modified_at=mtime,
        )
