"""Append canonical M1 task/project mutations to the PostgreSQL sync feed.

The caller owns the SQLAlchemy transaction. Entity revisions, snapshots,
change-feed rows, and deletion tombstones therefore commit or roll back with
the operational mutation.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.db.base import utcnow
from personal_os.db.models import Project, Task
from personal_os.sync.contracts import EntitySnapshot, EntityType, Tombstone
from personal_os.sync.feed_lock import lock_owner_feed
from personal_os.sync.postgres_store import (
    SyncChangeEvent,
    SyncEntityRevision,
    SyncTombstone,
)

_FIELDS = {
    Project: (EntityType.PROJECT, ("name", "description", "status")),
    Task: (
        EntityType.TASK,
        ("title", "description", "status", "priority", "due_date", "project_id", "capture_id"),
    ),
}


async def append_entity_change(session: AsyncSession, entity: Project | Task) -> None:
    """Record one task/project revision and its feed item in the caller's UoW."""
    entity_type, fields = _FIELDS[type(entity)]
    deleted = entity.deleted_at is not None
    data = None if deleted else {field: getattr(entity, field) for field in fields}
    snapshot = EntitySnapshot(
        entity_type=entity_type,
        entity_id=entity.id,
        revision=entity.revision,
        data=data,
        deleted=deleted,
    )
    snapshot_json = snapshot.model_dump(mode="json")
    session.add(
        SyncEntityRevision(
            owner_id=entity.owner_id,
            entity_type=entity_type.value,
            entity_id=entity.id,
            revision=entity.revision,
            snapshot=snapshot_json,
        )
    )

    await lock_owner_feed(session, entity.owner_id)
    event_id = uuid4()
    event = SyncChangeEvent(
        event_id=event_id,
        owner_id=entity.owner_id,
        entity_type=entity_type.value,
        entity_id=entity.id,
        revision=entity.revision,
        snapshot=None if deleted else snapshot_json,
        tombstone=None,
    )
    session.add(event)
    await session.flush()

    if deleted:
        tombstone = Tombstone(
            entity_type=entity_type,
            entity_id=entity.id,
            revision=entity.revision,
            sequence=event.sequence,
            deleted_at=utcnow(),
        )
        event.tombstone = tombstone.model_dump(mode="json")
        session.add(
            SyncTombstone(
                owner_id=entity.owner_id,
                entity_type=entity_type.value,
                entity_id=entity.id,
                revision=entity.revision,
                sequence=event.sequence,
                event_id=event_id,
                deleted_at=tombstone.deleted_at,
            )
        )
        await session.flush()
