from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from personal_os.markdown_sync import (
    ApplyStatus,
    ConflictReason,
    MarkdownReconciler,
    MarkdownVersion,
    MarkdownWorkspace,
    ReconciliationStatus,
    WorkspacePathError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def version(
    path: str,
    content: bytes | str | None,
    revision: int | None,
    *,
    deleted: bool = False,
) -> MarkdownVersion:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return MarkdownVersion(path, content, revision, deleted)


def fixture_bytes(group: str, name: str) -> bytes:
    return (FIXTURES / group / f"{name}.md").read_bytes()


def test_merges_non_overlapping_fixture_edits() -> None:
    base = version("notes.md", fixture_bytes("non_overlapping", "base"), 4)
    local = version("notes.md", fixture_bytes("non_overlapping", "local"), 4)
    remote = version("notes.md", fixture_bytes("non_overlapping", "remote"), 5)

    result = MarkdownReconciler().reconcile(base, local, remote)

    assert result.status is ReconciliationStatus.MERGED
    assert result.merged is not None
    assert result.merged.content == (b"# Notes\n\nAlpha local\nBeta\nGamma remote\n")
    assert result.expected_remote_revision == 5
    assert result.conflict is None


def test_overlapping_fixture_edits_keep_all_three_versions() -> None:
    base = version("notes.md", fixture_bytes("overlapping", "base"), 7)
    local = version("notes.md", fixture_bytes("overlapping", "local"), 7)
    remote = version("notes.md", fixture_bytes("overlapping", "remote"), 8)

    result = MarkdownReconciler().reconcile(base, local, remote)

    assert result.status is ReconciliationStatus.CONFLICT
    assert result.conflict is not None
    assert result.conflict.reason is ConflictReason.OVERLAPPING_EDITS
    assert result.conflict.base == base
    assert result.conflict.local == local
    assert result.conflict.remote == remote


def test_same_edit_on_both_sides_is_deduplicated() -> None:
    base = version("notes.md", b"A\nB\n", 1)
    edited = version("notes.md", b"A changed\nB\n", 1)
    remote = version("notes.md", b"A changed\nB\n", 2)

    result = MarkdownReconciler().reconcile(base, edited, remote)

    assert result.status is ReconciliationStatus.MERGED
    assert result.merged == version("notes.md", b"A changed\nB\n", None)


def test_different_renames_conflict_and_preserve_names() -> None:
    base = version("notes.md", b"Original\n", 2)
    local = version("local-name.md", b"Local\n", 2)
    remote = version("remote-name.md", b"Original\n", 3)

    result = MarkdownReconciler().reconcile(base, local, remote)

    assert result.conflict is not None
    assert result.conflict.reason is ConflictReason.RENAME_CONFLICT
    assert result.conflict.local.path == "local-name.md"
    assert result.conflict.remote.path == "remote-name.md"


def test_rename_with_overlapping_edit_is_a_conflict() -> None:
    base = version("notes.md", fixture_bytes("rename_edit", "base"), 2)
    local = version("renamed.md", fixture_bytes("rename_edit", "local"), 2)
    remote = version("notes.md", fixture_bytes("rename_edit", "remote"), 3)

    result = MarkdownReconciler().reconcile(base, local, remote)

    assert result.conflict is not None
    assert result.conflict.reason is ConflictReason.OVERLAPPING_EDITS


def test_non_overlapping_edit_can_follow_one_sided_rename() -> None:
    base = version("notes.md", b"A\nB\nC\n", 2)
    local = version("renamed.md", b"A local\nB\nC\n", 2)
    remote = version("notes.md", b"A\nB\nC remote\n", 3)

    result = MarkdownReconciler().reconcile(base, local, remote)

    assert result.status is ReconciliationStatus.MERGED
    assert result.merged == version("renamed.md", b"A local\nB\nC remote\n", None)


def test_delete_edit_conflict_and_explicit_revision_checked_delete() -> None:
    base = version("notes.md", b"Original\n", 10)
    local_edit = version("notes.md", b"Local edit\n", 10)
    remote_delete = version("notes.md", None, 11, deleted=True)

    conflict = MarkdownReconciler().reconcile(base, local_edit, remote_delete)

    assert conflict.conflict is not None
    assert conflict.conflict.reason is ConflictReason.DELETE_EDIT_CONFLICT
    assert conflict.conflict.local.content == b"Local edit\n"
    assert conflict.conflict.remote.deleted

    local_unchanged = version("notes.md", b"Original\n", 10)
    safe_delete = MarkdownReconciler().reconcile(base, local_unchanged, remote_delete)
    assert safe_delete.status is ReconciliationStatus.MERGED
    assert safe_delete.merged is not None and safe_delete.merged.deleted
    assert safe_delete.deletion_revision_checked


def test_local_delete_requires_exact_base_revision() -> None:
    base = version("notes.md", b"Original\n", 10)
    remote = version("notes.md", b"Original\n", 10)
    stale_delete = version("notes.md", None, 9, deleted=True)

    result = MarkdownReconciler().reconcile(base, stale_delete, remote)

    assert result.conflict is not None
    assert result.conflict.reason is ConflictReason.INVALID_DELETION_REVISION


def test_deletion_does_not_win_when_remote_revision_advanced_without_content_change() -> None:
    base = version("notes.md", b"Original\n", 10)
    remote = version("notes.md", b"Original\n", 11)
    local_delete = version("notes.md", None, 10, deleted=True)

    result = MarkdownReconciler().reconcile(base, local_delete, remote)

    assert result.conflict is not None
    assert result.conflict.reason is ConflictReason.DELETE_EDIT_CONFLICT


def test_missing_base_never_guesses_between_different_versions() -> None:
    local = version("notes.md", b"Local\n", 10)
    remote = version("notes.md", b"Remote\n", 11)

    result = MarkdownReconciler().reconcile(None, local, remote)

    assert result.conflict is not None
    assert result.conflict.reason is ConflictReason.MISSING_BASE
    assert result.conflict.base is None


@pytest.mark.parametrize("payload", [b"\xff\xfe\x00", b"# title\x00binary\n"])
def test_binary_or_unsupported_content_is_preserved_as_conflict(payload: bytes) -> None:
    base = version("notes.md", b"Original\n", 1)
    local = version("notes.md", payload, 1)
    remote = version("notes.md", b"Remote\n", 2)

    result = MarkdownReconciler().reconcile(base, local, remote)

    assert result.conflict is not None
    assert result.conflict.reason is ConflictReason.UNSUPPORTED_CONTENT
    assert result.conflict.local.content == payload


def test_repeated_conflict_is_deterministic_and_artifact_preserves_bytes(tmp_path: Path) -> None:
    base = version("notes.md", b"Base\r\n", 1)
    local = version("notes.md", b"Local \xff\n", 1)
    remote = version("notes.md", b"Remote\n", 2)
    reconciler = MarkdownReconciler()

    first = reconciler.reconcile(base, local, remote)
    second = reconciler.reconcile(base, local, remote)
    assert first.conflict is not None and second.conflict is not None
    assert first.conflict.conflict_id == second.conflict.conflict_id

    workspace = MarkdownWorkspace(tmp_path)
    artifact = workspace.write_conflict(first)
    assert artifact == workspace.write_conflict(second)
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["base"]["content_base64"] == base64.b64encode(base.content).decode()
    assert data["local"]["content_base64"] == base64.b64encode(local.content).decode()
    assert data["remote"]["content_base64"] == base64.b64encode(remote.content).decode()
    assert data["base"]["revision"] == 1
    assert data["remote"]["revision"] == 2


def test_workspace_applies_only_when_local_bytes_match_and_is_repeatable(tmp_path: Path) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    path = tmp_path / "notes.md"
    base_bytes = b"A\nB\n"
    local_bytes = b"Local A\nB\n"
    remote_bytes = b"A\nRemote B\n"
    path.write_bytes(base_bytes)
    base = version("notes.md", base_bytes, 4)
    local = version("notes.md", local_bytes, 4)
    remote = version("notes.md", remote_bytes, 5)
    result = MarkdownReconciler().reconcile(base, local, remote)
    assert result.merged is not None

    # Local filesystem content is checked against the local snapshot. A stale
    # filesystem copy cannot be overwritten merely because a merge was made.
    with pytest.raises(RuntimeError, match="changed since reconciliation"):
        workspace.apply(result)
    assert path.read_bytes() == base_bytes

    path.write_bytes(local_bytes)
    assert workspace.apply(result) is ApplyStatus.APPLIED
    assert path.read_bytes() == result.merged.content
    assert workspace.apply(result) is ApplyStatus.ALREADY_APPLIED


def test_workspace_applies_remote_tombstone_only_after_revision_check(tmp_path: Path) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    path = tmp_path / "notes.md"
    path.write_bytes(b"Base\n")
    base = version("notes.md", b"Base\n", 8)
    local = version("notes.md", b"Base\n", 8)
    remote = version("notes.md", None, 9, deleted=True)
    result = MarkdownReconciler().reconcile(base, local, remote)

    assert result.deletion_revision_checked
    assert workspace.apply(result) is ApplyStatus.APPLIED
    assert not path.exists()
    assert workspace.apply(result) is ApplyStatus.ALREADY_APPLIED


def test_workspace_rename_is_recoverable_after_destination_write(tmp_path: Path) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    (tmp_path / "old.md").write_bytes(b"Base\n")
    base = version("old.md", b"Base\n", 1)
    local = version("old.md", b"Base\n", 1)
    remote = version("new.md", b"Changed remotely\n", 2)
    result = MarkdownReconciler().reconcile(base, local, remote)

    assert workspace.apply(result) is ApplyStatus.APPLIED
    assert not (tmp_path / "old.md").exists()
    assert (tmp_path / "new.md").read_bytes() == b"Changed remotely\n"
    assert workspace.apply(result) is ApplyStatus.ALREADY_APPLIED


def test_local_rename_plus_remote_edit_writes_renamed_file_and_keeps_old_path_absent(
    tmp_path: Path,
) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    (tmp_path / "renamed.md").write_bytes(b"A local\nB\nC\n")
    base = version("old.md", b"A\nB\nC\n", 3)
    local = version("renamed.md", b"A local\nB\nC\n", 3)
    remote = version("old.md", b"A\nB\nC remote\n", 4)
    result = MarkdownReconciler().reconcile(base, local, remote)

    assert result.status is ReconciliationStatus.MERGED
    assert result.merged is not None and result.merged.path == "renamed.md"
    assert workspace.apply(result) is ApplyStatus.APPLIED
    assert (tmp_path / "renamed.md").read_bytes() == b"A local\nB\nC remote\n"
    assert not (tmp_path / "old.md").exists()


def test_rename_delete_conflict_never_overwrites_an_existing_destination(
    tmp_path: Path,
) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    (tmp_path / "renamed.md").write_bytes(b"Existing document\n")
    base = version("old.md", b"Base\n", 4)
    local_rename = version("renamed.md", b"Base\n", 4)
    remote_delete = version("old.md", None, 5, deleted=True)
    result = MarkdownReconciler().reconcile(base, local_rename, remote_delete)

    assert result.conflict is not None
    assert result.conflict.reason is ConflictReason.DELETE_EDIT_CONFLICT
    with pytest.raises(ValueError, match="conflict-free"):
        workspace.apply(result)
    assert (tmp_path / "renamed.md").read_bytes() == b"Existing document\n"
    assert (tmp_path / "old.md").exists() is False


def test_remote_rename_does_not_overwrite_existing_target_even_if_bytes_match(
    tmp_path: Path,
) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    (tmp_path / "old.md").write_bytes(b"Base\n")
    (tmp_path / "new.md").write_bytes(b"Changed remotely\n")
    base = version("old.md", b"Base\n", 1)
    local = version("old.md", b"Base\n", 1)
    remote = version("new.md", b"Changed remotely\n", 2)
    result = MarkdownReconciler().reconcile(base, local, remote)

    with pytest.raises(FileExistsError, match="rename destination"):
        workspace.apply(result)
    assert (tmp_path / "old.md").read_bytes() == b"Base\n"
    assert (tmp_path / "new.md").read_bytes() == b"Changed remotely\n"


def test_atomic_write_failure_keeps_original_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    path = tmp_path / "notes.md"
    base_bytes = b"A\nB\n"
    local_bytes = b"Local A\nB\n"
    path.write_bytes(local_bytes)
    result = MarkdownReconciler().reconcile(
        version("notes.md", base_bytes, 1),
        version("notes.md", local_bytes, 1),
        version("notes.md", b"A\nRemote B\n", 2),
    )

    def interrupted_replace(
        source: os.PathLike[str], destination: os.PathLike[str], **kwargs: object
    ) -> None:
        raise OSError("simulated interruption before atomic replace")

    monkeypatch.setattr("personal_os.markdown_sync.reconcile.os.replace", interrupted_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        workspace.apply(result)

    assert path.read_bytes() == local_bytes
    assert list(tmp_path.glob(".notes.md.*.tmp")) == []


def test_symlink_substitution_during_atomic_replace_never_writes_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    docs = root / "docs"
    outside = tmp_path / "outside"
    docs.mkdir(parents=True)
    outside.mkdir()
    (docs / "notes.md").write_bytes(b"Local\nB\n")
    (outside / "notes.md").write_bytes(b"Outside original\n")
    workspace = MarkdownWorkspace(root)
    result = MarkdownReconciler().reconcile(
        version("docs/notes.md", b"A\nB\n", 1),
        version("docs/notes.md", b"Local\nB\n", 1),
        version("docs/notes.md", b"A\nRemote\n", 2),
    )
    original_replace = os.replace
    moved_docs = root / "docs-moved"

    def swap_parent_before_replace(
        source: os.PathLike[str], destination: os.PathLike[str], **kwargs: object
    ) -> None:
        os.rename(docs, moved_docs)
        docs.symlink_to(outside, target_is_directory=True)
        original_replace(source, destination, **kwargs)

    monkeypatch.setattr(
        "personal_os.markdown_sync.reconcile.os.replace", swap_parent_before_replace
    )
    with pytest.raises(WorkspacePathError, match="parent changed"):
        workspace.apply(result)

    assert (outside / "notes.md").read_bytes() == b"Outside original\n"
    assert docs.is_symlink()
    assert (moved_docs / "notes.md").read_bytes() == b"Local\nRemote\n"


@pytest.mark.parametrize(
    "unsafe_path",
    ["../outside.md", "/outside.md", "a/../outside.md", "C:/outside.md"],
)
def test_workspace_rejects_traversal_and_absolute_paths(tmp_path: Path, unsafe_path: str) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    with pytest.raises(WorkspacePathError):
        workspace.read(unsafe_path)


def test_workspace_rejects_symlink_components(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    (root / "direct.md").symlink_to(outside / "secret.md")
    workspace = MarkdownWorkspace(root)

    with pytest.raises(WorkspacePathError, match="symlink"):
        workspace.read("linked/secret.md")
    with pytest.raises(WorkspacePathError, match="symlink"):
        workspace.read("direct.md")


def test_workspace_rejects_non_markdown_and_never_scans(tmp_path: Path) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    (tmp_path / "unrelated.txt").write_text("leave me", encoding="utf-8")
    with pytest.raises(WorkspacePathError, match="only .md"):
        workspace.read("unrelated.txt")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["unrelated.txt"]


def test_conflict_artifact_interruption_does_not_create_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = MarkdownWorkspace(tmp_path)
    result = MarkdownReconciler().reconcile(
        version("notes.md", b"Base\n", 1),
        version("notes.md", b"Local\n", 1),
        version("notes.md", b"Remote\n", 2),
    )

    def interrupted_replace(
        source: os.PathLike[str], destination: os.PathLike[str], **kwargs: object
    ) -> None:
        raise OSError("simulated artifact interruption")

    monkeypatch.setattr("personal_os.markdown_sync.reconcile.os.replace", interrupted_replace)
    with pytest.raises(OSError, match="artifact interruption"):
        workspace.write_conflict(result)

    artifact_dir = tmp_path / MarkdownWorkspace.CONFLICT_DIRECTORY
    assert list(artifact_dir.glob("*.json")) == []
    assert list(artifact_dir.glob("*.tmp")) == []
