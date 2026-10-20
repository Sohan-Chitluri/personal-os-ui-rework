# M3 Storage Correction

## Decision

PostgreSQL is the only persistent server-side authority for cloud operational
data and sync state. The browser keeps its offline operation queue in
IndexedDB. The local companion has no SQLite queue and no separate local
operational database.

The companion may send operations using the authenticated cloud sync protocol
when connected. This milestone does not promise durable companion edits while
disconnected. If that product behavior is required later, a candidate design is
a locally managed PostgreSQL instance used only as an append-only operation
outbox. It would send the same idempotent operations through the authenticated
protocol after reconnect and retain each row until its cloud acknowledgement
is durable locally. Cloud PostgreSQL remains canonical. This option needs a
separate decision covering local PostgreSQL packaging, upgrades, disk failure,
and recovery; it is not implemented here.

## Removed component

Commit `6d75039` introduced `src/personal_os/companion/offline_queue.py`, a
SQLite-backed companion queue, and its dedicated tests. Repository history
shows no production callers outside those tests. The adapter and queue tests
were therefore removed in the storage-correction commit. The replacement
companion architecture test asserts that the local queue module is absent.
The browser's independent IndexedDB queue remains available.

## PostgreSQL sync state

The additive Alembic revision `0005_sync_state` adds:

- operation receipts with unique owner-scoped operation IDs and idempotency
  keys;
- canonical task/project revision snapshots;
- durable conflict records with base, local, and remote versions;
- ordered change-feed events and explicit deletion tombstones.

Revision `0007_sync_feed_cursors` adds durable owner-bound feed positions with
expiration. The authenticated API stores only a hash of each random cursor
token.

The shared working tree also contains the pre-existing M2 revision
`0005_device_operations`, which extends the same `0004_devices` parent. A local
merge node joins those two heads for combined migration checks. That merge node
must be committed together with the M2 migration by the owner of that work;
the M3 commit will include only its own sync-state revision.

`PostgresSyncStore` reuses the deterministic sync engine. It locks each
operation, idempotency key, and entity in PostgreSQL, then commits the canonical
entity mutation and all resulting sync records in one transaction. Duplicate
delivery replays the stored acknowledgement. If any write fails, the
transaction rolls back the entity and sync records together.

M1 task/project repositories now append entity revision snapshots, feed events,
and tombstones inside the same PostgreSQL transaction as create, update, and
delete. Task project associations are included in task snapshots. Project sync
snapshots currently include only `name`, `description`, and `status`;
`project_document` has no PostgreSQL sync mapping. The authenticated API
exposes v1 operation upload and change-feed retrieval. Upload batches are
envelope-validated before processing, then handled in request order with one
PostgreSQL transaction per operation. Each result is returned independently;
an interrupted batch can be retried with the same idempotency keys. Feed
cursors expire after 24 hours. Expired or invalid cursors recover by replaying
the full retained feed from sequence zero. Feed history is not compacted; a
snapshot endpoint is required before any retention policy can discard rows.
Conflicts are inspectable through the owner-scoped conflict endpoint. M2
authenticated outbound companion wiring and client integration remain
outstanding. No live cloud/device sync claim is made.

## Contract and conflict review

The shared v1 envelope keeps entity identity, revision precondition,
idempotency key, and acknowledgement identity explicit. Existing engine tests
cover stale and future revisions, duplicate IDs/keys, reordered delivery,
tombstones, unsupported entities, and stale/repeated conflict resolution. The
simulator covers timeout replay, server restart, partial batches, expired
cursors, and interrupted page application; Markdown fixtures cover safe
non-overlapping merges and preservation of overlapping, rename, delete/edit,
and missing-base conflicts.

The API exposes persisted cursors with a full-retained-feed replay recovery
path. PostgreSQL advisory transaction locks serialize per-owner feed sequence
allocation through commit, so pagination cannot advance past a delayed lower
sequence. Cursor expiry or an unknown cursor causes a complete replay rather
than a jump to the feed head.

## Verification boundary

The PostgreSQL store tests use the `db_engine`/`db_session_factory` fixtures,
which require `DATABASE_URL` targeting local PostgreSQL database
`personal_os_test`. The fixture fails with a setup message if PostgreSQL is
missing or the URL targets another database. It has no SQLite fallback.
