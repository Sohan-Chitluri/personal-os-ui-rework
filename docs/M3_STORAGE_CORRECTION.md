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

The store is isolated and is not called by API routes. M1 task/project API
mutations do not yet append to the M3 feed, and `project_document` has no
PostgreSQL sync mapping. Production synchronization must wait for API route
integration, M1 revision coordination, M2 authenticated outbound transport,
and cursor issuance/expiration. No live synchronization claim is made.

## Contract and conflict review

The shared v1 envelope keeps entity identity, revision precondition,
idempotency key, and acknowledgement identity explicit. Existing engine tests
cover stale and future revisions, duplicate IDs/keys, reordered delivery,
tombstones, unsupported entities, and stale/repeated conflict resolution. The
simulator covers timeout replay, server restart, partial batches, expired
cursors, and interrupted page application; Markdown fixtures cover safe
non-overlapping merges and preservation of overlapping, rename, delete/edit,
and missing-base conflicts.

Before API integration, the live implementation still needs a PostgreSQL-backed
opaque cursor with expiry and snapshot recovery. The current store exposes
ordered sequence reads for isolated testing; it does not mint production
cursors. The M1 CRUD routes also need to append their mutations to the same
feed so sync clients cannot miss cloud-side changes.

## Verification boundary

The PostgreSQL store tests use the `db_engine`/`db_session_factory` fixtures,
which require `DATABASE_URL` targeting local PostgreSQL database
`personal_os_test`. The fixture fails with a setup message if PostgreSQL is
missing or the URL targets another database. It has no SQLite fallback.
