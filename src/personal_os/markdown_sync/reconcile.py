"""Three-way Markdown reconciliation with explicit, recoverable conflicts.

This module works only on versions supplied by its caller. It does not scan a
workspace or know about a transport or cloud API. ``MarkdownReconciler`` can
combine line-disjoint edits, while ``MarkdownWorkspace`` applies a reviewed
result under one configured root and writes conflict artifacts atomically.
"""

from __future__ import annotations

import base64
import contextlib
import difflib
import errno
import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath


class WorkspacePathError(ValueError):
    """A supplied path is not a safe Markdown path inside the workspace."""


class ReconciliationStatus(StrEnum):
    MERGED = "merged"
    CONFLICT = "conflict"


class ConflictReason(StrEnum):
    OVERLAPPING_EDITS = "overlapping_edits"
    RENAME_CONFLICT = "rename_conflict"
    DELETE_EDIT_CONFLICT = "delete_edit_conflict"
    MISSING_BASE = "missing_base"
    UNSUPPORTED_CONTENT = "unsupported_content"
    INVALID_DELETION_REVISION = "invalid_deletion_revision"


class ApplyStatus(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"


@dataclass(frozen=True, slots=True)
class MarkdownVersion:
    """One observed version of a Markdown document.

    ``path`` is workspace-relative and uses POSIX separators. A deletion is an
    explicit tombstone retaining the last known path and observed revision;
    absence alone is never interpreted as a deletion. For a local deletion,
    ``revision`` is the revision the delete was based on. A server tombstone
    carries its deletion revision.
    """

    path: str
    content: bytes | None
    revision: int | None
    deleted: bool = False

    def __post_init__(self) -> None:
        _validate_markdown_path(self.path)
        if self.revision is not None and self.revision < 0:
            raise ValueError("revision must be nonnegative")
        if self.deleted:
            if self.content is not None:
                raise ValueError("deleted version cannot contain content")
            if self.revision is None:
                raise ValueError("explicit deletion requires a revision")
        elif self.content is None:
            raise ValueError("live version requires content")


@dataclass(frozen=True, slots=True)
class MarkdownConflict:
    """A deterministic record retaining the exact base and both input sides."""

    conflict_id: str
    reason: ConflictReason
    base: MarkdownVersion | None
    local: MarkdownVersion
    remote: MarkdownVersion


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """Result of reconciling one explicitly identified document."""

    status: ReconciliationStatus
    base: MarkdownVersion | None
    local: MarkdownVersion
    remote: MarkdownVersion
    merged: MarkdownVersion | None = None
    expected_remote_revision: int | None = None
    deletion_revision_checked: bool = False
    conflict: MarkdownConflict | None = None


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    replacement: tuple[str, ...]


class MarkdownReconciler:
    """Pure deterministic three-way merge for a single Markdown document."""

    def reconcile(
        self,
        base: MarkdownVersion | None,
        local: MarkdownVersion,
        remote: MarkdownVersion,
    ) -> Reconciliation:
        """Merge only provably disjoint text edits; otherwise preserve a conflict.

        Revisions are not used as clocks or to order edits. The remote revision
        is returned as the precondition a later adapter must check when it
        submits or applies the merged version.
        """

        for version in (base, local, remote):
            if version is not None:
                _validate_markdown_path(version.path)

        if base is None:
            if _same_state(local, remote) and not local.deleted:
                return self._merged_result(None, local, remote, remote)
            return self._conflict(None, local, remote, ConflictReason.MISSING_BASE)

        if local.deleted or remote.deleted:
            return self._reconcile_deletion(base, local, remote)

        local_renamed = local.path != base.path
        remote_renamed = remote.path != base.path
        if local_renamed and remote_renamed and local.path != remote.path:
            return self._conflict(base, local, remote, ConflictReason.RENAME_CONFLICT)

        target_path = local.path if local_renamed else remote.path

        try:
            base_text = _decode_markdown(base.content)
            local_text = _decode_markdown(local.content)
            remote_text = _decode_markdown(remote.content)
        except (UnicodeDecodeError, ValueError):
            return self._conflict(base, local, remote, ConflictReason.UNSUPPORTED_CONTENT)

        if local_text == remote_text:
            merged_text = local_text
        elif local_text == base_text:
            merged_text = remote_text
        elif remote_text == base_text:
            merged_text = local_text
        else:
            combined_text = _merge_disjoint_lines(base_text, local_text, remote_text)
            if combined_text is None:
                return self._conflict(base, local, remote, ConflictReason.OVERLAPPING_EDITS)
            merged_text = combined_text

        merged = MarkdownVersion(
            path=target_path,
            content=merged_text.encode("utf-8"),
            revision=None,
        )
        return self._merged_result(base, local, remote, merged)

    def _reconcile_deletion(
        self,
        base: MarkdownVersion,
        local: MarkdownVersion,
        remote: MarkdownVersion,
    ) -> Reconciliation:
        if local.deleted and remote.deleted:
            if local.path != base.path or remote.path != base.path:
                return self._conflict(base, local, remote, ConflictReason.RENAME_CONFLICT)
            if not (
                base.revision is not None
                and local.revision == base.revision
                and remote.revision is not None
                and remote.revision > base.revision
            ):
                return self._conflict(
                    base,
                    local,
                    remote,
                    ConflictReason.INVALID_DELETION_REVISION,
                )
            # Both sides explicitly tombstoned this identity. No edit is lost.
            merged = MarkdownVersion(
                path=base.path,
                content=None,
                revision=remote.revision,
                deleted=True,
            )
            return self._merged_result(
                base,
                local,
                remote,
                merged,
                deletion_revision_checked=True,
            )

        deleting = local if local.deleted else remote
        surviving = remote if local.deleted else local
        if deleting.path != base.path:
            return self._conflict(base, local, remote, ConflictReason.RENAME_CONFLICT)
        if not _same_state(surviving, base) or surviving.revision != base.revision:
            return self._conflict(base, local, remote, ConflictReason.DELETE_EDIT_CONFLICT)

        # A local delete is a request against the exact base revision. A remote
        # tombstone must advance the base revision. Missing or stale revision
        # evidence is retained as a conflict instead of authorizing deletion.
        if local.deleted:
            revision_valid = local.revision == base.revision
        else:
            revision_valid = (
                base.revision is not None
                and remote.revision is not None
                and remote.revision > base.revision
            )
        if not revision_valid:
            return self._conflict(
                base,
                local,
                remote,
                ConflictReason.INVALID_DELETION_REVISION,
            )

        merged = MarkdownVersion(
            path=base.path,
            content=None,
            revision=remote.revision,
            deleted=True,
        )
        return self._merged_result(
            base,
            local,
            remote,
            merged,
            deletion_revision_checked=True,
        )

    def _merged_result(
        self,
        base: MarkdownVersion | None,
        local: MarkdownVersion,
        remote: MarkdownVersion,
        merged: MarkdownVersion,
        *,
        deletion_revision_checked: bool = False,
    ) -> Reconciliation:
        return Reconciliation(
            status=ReconciliationStatus.MERGED,
            base=base,
            local=local,
            remote=remote,
            merged=merged,
            expected_remote_revision=remote.revision,
            deletion_revision_checked=deletion_revision_checked,
        )

    def _conflict(
        self,
        base: MarkdownVersion | None,
        local: MarkdownVersion,
        remote: MarkdownVersion,
        reason: ConflictReason,
    ) -> Reconciliation:
        conflict_id = _conflict_id(reason, base, local, remote)
        conflict = MarkdownConflict(conflict_id, reason, base, local, remote)
        return Reconciliation(
            status=ReconciliationStatus.CONFLICT,
            base=base,
            local=local,
            remote=remote,
            expected_remote_revision=remote.revision,
            conflict=conflict,
        )


class MarkdownWorkspace:
    """Explicit-root filesystem adapter for reconciliation results.

    It never walks or scans the workspace. Every read, apply, or conflict
    artifact write names a specific relative path. Symlinks in any traversed
    component are rejected.
    """

    CONFLICT_DIRECTORY = ".personal-os-conflicts"

    def __init__(self, root: str | os.PathLike[str]):
        root_path = Path(root).expanduser().resolve(strict=True)
        if not root_path.is_dir():
            raise NotADirectoryError(root_path)
        self.root = root_path

    def read(self, relative_path: str) -> bytes | None:
        return self._read_relative(relative_path, markdown_required=True)

    def _read_relative(self, relative_path: str, *, markdown_required: bool) -> bytes | None:
        opened = self._open_parent_fd(
            relative_path,
            create_parents=False,
            markdown_required=markdown_required,
        )
        if opened is None:
            return None
        parent_fd, name, _ = opened
        try:
            try:
                info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(info.st_mode):
                raise WorkspacePathError("symlink targets are not read")
            if not stat.S_ISREG(info.st_mode):
                raise WorkspacePathError("Markdown path must be a regular file")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(name, flags, dir_fd=parent_fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise WorkspacePathError("symlink targets are not read") from exc
                raise
            with os.fdopen(fd, "rb") as source:
                opened_info = os.fstat(source.fileno())
                if not stat.S_ISREG(opened_info.st_mode):
                    raise WorkspacePathError("Markdown path must be a regular file")
                return source.read()
        finally:
            os.close(parent_fd)

    def apply(self, result: Reconciliation) -> ApplyStatus:
        """Apply a safe result only if the workspace still matches its local input.

        Deletion is allowed only when reconciliation checked an explicit
        revision precondition. A repeated application returns
        ``ALREADY_APPLIED`` when the desired content or absence is present.
        """

        if result.status is not ReconciliationStatus.MERGED or result.merged is None:
            raise ValueError("only a conflict-free reconciliation can be applied")
        merged = result.merged
        local = result.local

        if merged.deleted:
            if not result.deletion_revision_checked:
                raise ValueError("deletion lacks a checked revision precondition")
            if local.deleted:
                if self.read(local.path) is not None:
                    raise RuntimeError("workspace changed since local deletion")
                return ApplyStatus.ALREADY_APPLIED
            observed = self.read(local.path)
            if observed is None:
                return ApplyStatus.ALREADY_APPLIED
            if observed != local.content:
                raise RuntimeError("workspace changed since reconciliation")
            self._unlink_relative(local.path, expected_content=observed)
            return ApplyStatus.APPLIED

        assert merged.content is not None
        current_target = self.read(merged.path)
        if current_target == merged.content:
            if merged.path != local.path:
                current_local = self.read(local.path)
                if current_local is not None:
                    # The target may have existed before this attempt. Without
                    # a durable rename journal, removing the old path would
                    # risk deleting one of two distinct documents.
                    raise FileExistsError(f"rename destination already exists: {merged.path}")
            return ApplyStatus.ALREADY_APPLIED

        current_local = self.read(local.path)
        if local.deleted:
            if current_local is not None:
                raise RuntimeError("workspace changed since local deletion")
        elif current_local != local.content:
            raise RuntimeError("workspace changed since reconciliation")

        if merged.path != local.path and current_target is not None:
            raise FileExistsError(f"rename destination already exists: {merged.path}")

        self._atomic_write_relative(merged.path, merged.content)
        if merged.path != local.path and current_local is not None:
            # Keep data if interrupted here: the new complete version exists
            # and a repeated apply can safely finish removing this old copy.
            if local.content is None or self.read(local.path) != local.content:
                raise RuntimeError("workspace changed during rename apply")
            self._unlink_relative(local.path, expected_content=local.content)
        return ApplyStatus.APPLIED

    def write_conflict(self, result: Reconciliation) -> Path:
        """Atomically persist both versions and the base for manual recovery."""

        conflict = result.conflict
        if result.status is not ReconciliationStatus.CONFLICT or conflict is None:
            raise ValueError("result does not contain a conflict")
        artifact_rel = f"{self.CONFLICT_DIRECTORY}/{conflict.conflict_id}.json"
        artifact = self.root.joinpath(
            *_validate_relative_path(artifact_rel, markdown_required=False)
        )
        data = json.dumps(
            _conflict_to_dict(conflict),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        existing = self._read_relative(artifact_rel, markdown_required=False)
        if existing is not None:
            if existing != data:
                raise FileExistsError("conflict artifact ID already has different data")
            return artifact
        self._atomic_write_relative(artifact_rel, data, markdown_required=False)
        return artifact

    def _open_parent_fd(
        self,
        relative_path: str,
        *,
        create_parents: bool,
        markdown_required: bool,
    ) -> tuple[int, str, tuple[str, ...]] | None:
        parts = _validate_relative_path(relative_path, markdown_required=markdown_required)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            parent_fd = os.open(self.root, flags)
        except OSError as exc:
            raise WorkspacePathError("workspace root could not be opened safely") from exc
        try:
            for part in parts[:-1]:
                try:
                    child_fd = os.open(part, flags, dir_fd=parent_fd)
                except FileNotFoundError:
                    if not create_parents:
                        os.close(parent_fd)
                        return None
                    os.mkdir(part, dir_fd=parent_fd)
                    child_fd = os.open(part, flags, dir_fd=parent_fd)
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise WorkspacePathError("symlink path components are rejected") from exc
                    raise
                os.close(parent_fd)
                parent_fd = child_fd
            return parent_fd, parts[-1], parts
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(parent_fd)
            raise

    def _atomic_write_relative(
        self,
        relative_path: str,
        content: bytes,
        *,
        markdown_required: bool = True,
    ) -> None:
        opened = self._open_parent_fd(
            relative_path,
            create_parents=True,
            markdown_required=markdown_required,
        )
        assert opened is not None
        parent_fd, name, parts = opened
        temp_name: str | None = None
        try:
            old_mode: int | None = None
            try:
                info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None:
                if stat.S_ISLNK(info.st_mode):
                    raise WorkspacePathError("symlink destination is rejected")
                if not stat.S_ISREG(info.st_mode):
                    raise WorkspacePathError("destination must be a regular file")
                old_mode = stat.S_IMODE(info.st_mode)

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            for _ in range(8):
                candidate = f".{name}.{secrets.token_hex(8)}.tmp"
                try:
                    fd = os.open(candidate, flags, 0o600, dir_fd=parent_fd)
                    temp_name = candidate
                    break
                except FileExistsError:
                    continue
            else:
                raise FileExistsError("could not allocate atomic-write temporary file")

            with os.fdopen(fd, "wb") as output:
                if old_mode is not None:
                    os.fchmod(output.fileno(), old_mode)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())

            # Re-check the target immediately before replacement. Replacing
            # an entry never follows it, so a symlink can never redirect the
            # write to an outside file.
            try:
                final_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                final_info = None
            if final_info is not None and stat.S_ISLNK(final_info.st_mode):
                raise WorkspacePathError("symlink destination is rejected")
            self._verify_parent_identity(parts, parent_fd)
            assert temp_name is not None
            os.replace(
                temp_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            temp_name = None
            os.fsync(parent_fd)
            self._verify_parent_identity(parts, parent_fd)
        finally:
            if temp_name is not None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temp_name, dir_fd=parent_fd)
            os.close(parent_fd)

    def _verify_parent_identity(self, parts: tuple[str, ...], expected_parent_fd: int) -> None:
        if len(parts) == 1:
            path_parent = self.root
            info = path_parent.stat()
            expected = os.fstat(expected_parent_fd)
            if (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
                raise WorkspacePathError("workspace root changed during write")
            return
        relative_parent = "/".join(parts[:-1]) + "/placeholder.md"
        try:
            opened = self._open_parent_fd(
                relative_parent,
                create_parents=False,
                markdown_required=True,
            )
        except WorkspacePathError as exc:
            raise WorkspacePathError("workspace parent changed during write") from exc
        if opened is None:
            raise WorkspacePathError("workspace parent changed during write")
        parent_fd, _, _ = opened
        try:
            actual = os.fstat(parent_fd)
            expected = os.fstat(expected_parent_fd)
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise WorkspacePathError("workspace parent changed during write")
        finally:
            os.close(parent_fd)

    def _unlink_relative(self, relative_path: str, *, expected_content: bytes) -> None:
        opened = self._open_parent_fd(
            relative_path,
            create_parents=False,
            markdown_required=True,
        )
        if opened is None:
            return
        parent_fd, name, parts = opened
        try:
            current = self._read_from_parent(parent_fd, name)
            if current is None:
                return
            if current != expected_content:
                raise RuntimeError("workspace changed before explicit deletion")
            self._verify_parent_identity(parts, parent_fd)
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    @staticmethod
    def _read_from_parent(parent_fd: int, name: str) -> bytes | None:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise WorkspacePathError("symlink targets are not read")
        if not stat.S_ISREG(info.st_mode):
            raise WorkspacePathError("Markdown path must be a regular file")
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise WorkspacePathError("Markdown path must be a regular file")
            return source.read()


def _validate_markdown_path(path: str) -> tuple[str, ...]:
    return _validate_relative_path(path, markdown_required=True)


def _validate_relative_path(path: str, *, markdown_required: bool) -> tuple[str, ...]:
    if not isinstance(path, str) or not path or "\x00" in path:
        raise WorkspacePathError("path must be a nonempty relative Markdown path")
    if "\\" in path or ":" in path:
        raise WorkspacePathError("path must use POSIX separators")
    pure_path = PurePosixPath(path)
    if (
        pure_path.is_absolute()
        or path != pure_path.as_posix()
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise WorkspacePathError("path must stay inside the configured workspace")
    if markdown_required and (
        not pure_path.parts or pure_path.suffix.lower() not in {".md", ".markdown"}
    ):
        raise WorkspacePathError("only .md and .markdown files are supported")
    return pure_path.parts


def _decode_markdown(content: bytes | None) -> str:
    if content is None:
        raise ValueError("live Markdown version has no content")
    if b"\x00" in content:
        raise ValueError("NUL bytes are unsupported in Markdown")
    return content.decode("utf-8", errors="strict")


def _same_state(left: MarkdownVersion, right: MarkdownVersion) -> bool:
    return (
        left.path == right.path and left.deleted == right.deleted and left.content == right.content
    )


def _edits(base_lines: list[str], changed_lines: list[str]) -> list[_Edit]:
    matcher = difflib.SequenceMatcher(a=base_lines, b=changed_lines, autojunk=False)
    return [
        _Edit(i1, i2, tuple(changed_lines[j1:j2]))
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def _merge_disjoint_lines(base: str, local: str, remote: str) -> str | None:
    base_lines = base.splitlines(keepends=True)
    local_edits = _edits(base_lines, local.splitlines(keepends=True))
    remote_edits = _edits(base_lines, remote.splitlines(keepends=True))

    for local_edit in local_edits:
        for remote_edit in remote_edits:
            if _same_edit(local_edit, remote_edit):
                continue
            if _edits_overlap(local_edit, remote_edit):
                return None

    edits: list[_Edit] = []
    for edit in (*local_edits, *remote_edits):
        if not any(_same_edit(edit, prior) for prior in edits):
            edits.append(edit)
    edits.sort(key=lambda item: (item.start, item.end))

    merged: list[str] = []
    cursor = 0
    for edit in edits:
        if edit.start < cursor:
            return None
        merged.extend(base_lines[cursor : edit.start])
        merged.extend(edit.replacement)
        cursor = edit.end
    merged.extend(base_lines[cursor:])
    return "".join(merged)


def _same_edit(left: _Edit, right: _Edit) -> bool:
    return (
        left.start == right.start
        and left.end == right.end
        and left.replacement == right.replacement
    )


def _edits_overlap(left: _Edit, right: _Edit) -> bool:
    left_insert = left.start == left.end
    right_insert = right.start == right.end
    if left_insert and right_insert:
        return left.start == right.start
    if left_insert:
        return right.start <= left.start <= right.end
    if right_insert:
        return left.start <= right.start <= left.end
    return max(left.start, right.start) < min(left.end, right.end)


def _version_to_dict(version: MarkdownVersion | None) -> dict[str, object] | None:
    if version is None:
        return None
    return {
        "path": version.path,
        "revision": version.revision,
        "deleted": version.deleted,
        "content_base64": (
            base64.b64encode(version.content).decode("ascii")
            if version.content is not None
            else None
        ),
    }


def _conflict_to_dict(conflict: MarkdownConflict) -> dict[str, object]:
    return {
        "schema_version": 1,
        "conflict_id": conflict.conflict_id,
        "reason": conflict.reason.value,
        "base": _version_to_dict(conflict.base),
        "local": _version_to_dict(conflict.local),
        "remote": _version_to_dict(conflict.remote),
    }


def _conflict_id(
    reason: ConflictReason,
    base: MarkdownVersion | None,
    local: MarkdownVersion,
    remote: MarkdownVersion,
) -> str:
    payload = {
        "reason": reason.value,
        "base": _version_to_dict(base),
        "local": _version_to_dict(local),
        "remote": _version_to_dict(remote),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "mdc_" + hashlib.sha256(canonical).hexdigest()
