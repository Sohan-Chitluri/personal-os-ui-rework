# M3 Offline Sync Contract (v1)

This describes the M3 sync engine, queues, Markdown reconciliation, simulator,
and authenticated v1 synchronization API. PostgreSQL remains canonical for
cloud tasks, projects, and operational state. Markdown files remain portable
content artifacts.

## Storage placement and implementation status

- The browser offline operation queue uses IndexedDB.
- The local companion has no local operational database or durable offline
  queue. It may submit operations through the authenticated cloud protocol
  when connected. Durable disconnected companion edits are not supported yet.
- Server receipts, entity revision snapshots, conflicts, change-feed events,
  and tombstones belong in PostgreSQL. The additive `0005_sync_state`
  migration and `PostgresSyncStore` provide persistence for canonical tasks
  and projects. M1 task/project repository mutations now append revision
  snapshots and feed events in the same PostgreSQL transaction. Authenticated
  v1 upload and change-feed routes use this store.
- PostgreSQL-backed tests require the local `personal_os_test` database and
  fail with an explicit setup message when it is unavailable. They do not
  switch to SQLite.

## Shared types

- **Entity identity:** `(entity_type, entity_id)`. Current types are `task`,
  `project`, and `project_document`; IDs are stable strings (UUID/ULID as
  accepted by the owning repository).
- **Revision:** the owning cloud entity's monotonically increasing integer
  `revision`. `base_revision` is null only for create. Update, delete, and
  conflict resolution require the exact revision observed by the client.
- **Operation:** protocol version, stable operation ID, stable idempotency key,
  entity identity, operation type, base revision, JSON payload where required,
  UTC creation time, and optional dependency operation IDs. Creation time is
  diagnostic; it does not establish ordering or causality.
- **Payload semantics:** create carries the complete initial entity document;
  update and conflict resolution carry the complete intended resulting
  document, not a JSON patch. Delete carries no payload. The isolated
  PostgreSQL adapter currently synchronizes the M1 task fields
  (`title`, `description`, `status`, `priority`, `due_date`, `project_id`, and
  `capture_id`) and project fields (`name`, `description`, `status`). Server
  identity, ownership, revision, timestamps, and derived task metadata are not
  accepted from clients. `project_document` remains unsupported by that
  adapter. This contract does not imply field-level merge semantics.
- **Acknowledgement:** operation ID, status (`applied`, `duplicate`,
  `conflicted`, `retryable`, or `permanent`), resulting/current revision when
  known, conflict ID when conflicted, and a typed error when unsuccessful.
- **Conflict:** durable ID, original operation/local payload, base revision,
  remote snapshot including revision or tombstone, reason, and resolution
  state. A resolution is a new idempotent operation with an exact current
  `base_revision`; repeating the same resolution returns its original result.
- **Change-feed item:** stable event ID, monotonically ordered server sequence,
  entity identity and revision, plus either the complete current snapshot or a
  tombstone.
- **Cursor:** opaque server-issued token with an expiry. A client applies all
  items from a page durably before persisting that page's next cursor. On a
  partial local apply or disconnect, it resumes from the previous cursor and
  safely replays items by event ID/revision.
- **Error categories:** retryable transport/temporary service/storage failure
  and `dependency_pending`/`missing_base` while an explicit prerequisite is
  undelivered; permanent malformed, unsupported, unauthorized, invalid
  operation, and `dependency_failed` after a prerequisite's terminal failure.
  Revision conflict is a separate durable outcome, not a retryable error.
- **Tombstone:** entity identity, deletion revision, server sequence, and
  deletion time. Tombstones remain in the feed long enough to cover supported
  cursor lifetimes; expired cursors require a fresh snapshot/reconciliation.

## Upload and idempotency semantics

Uploads contain a bounded ordered batch of operation envelopes. Results are
per operation, so a batch may partially succeed. A successful operation is
committed with its idempotency record atomically. Repeating an operation ID
and key with the same canonical request body returns the stored acknowledgement
without repeating side effects. Reusing either identifier with a different
body is a permanent idempotency-key mismatch.

The HTTP endpoint is `POST /api/v1/sync/operations` with
`{"protocol_version":1,"operations":[...]}`. A batch contains 1–100
operation envelopes. The server validates every envelope before applying any
item, then processes validated operations in request order. Each operation
uses its own PostgreSQL transaction and returns one acknowledgement in the
same order. A conflict, retryable dependency result, or permanent operation
failure does not prevent later items from being attempted. The batch is not an
all-or-nothing transaction; after a timeout, clients retry the entire batch
with the same operation IDs and idempotency keys. Replayed items return their
stored result. A malformed envelope returns `422 invalid_operation` before
the server applies any item. An unsupported protocol returns
`409 protocol_mismatch` before applying the batch.

The server checks the base revision before applying update, delete, or
resolution. A missing entity, missing/expired base snapshot, stale revision,
unsupported operation, and incompatible concurrent change have explicit
outcomes; no timestamp-based fallback applies. A create succeeds only when the
entity identity does not already exist. Duplicate creates with matching
idempotency records replay the original acknowledgement; collisions without
that record conflict.

When an operation names a base revision newer than the server's current
revision, it is out of order: return a retryable `missing_base` result without
recording a terminal idempotency receipt, so the client can deliver its
prerequisite and retry. If the named historical base is older than current but
no longer available, return a durable conflict marked `expired_base`; never
guess what changed. An available stale base still does not authorize overwrite:
it is preserved as a conflict unless a separately specified domain rule can
prove an automatic merge safe.

For an absent entity, an update/delete that names an explicit prerequisite is
also retryable without a receipt because its create may be reordered in
delivery. Without such a prerequisite, a genuinely absent entity is a
permanent missing-entity error. Version 1 does not resurrect a deleted entity:
a tombstone blocks later creates and updates for the same identity, including
operations that claim a future revision.

Clients preserve the full operation until its acknowledgement is durably
recorded. A timeout is an unknown outcome, so the client retries with the same
operation ID and idempotency key. Retryable failures use bounded backoff;
permanent failures and conflicts remain inspectable and recoverable.

## Change feed and resume

The HTTP endpoint is `GET /api/v1/sync/changes?cursor=<token>&limit=<n>`.
`limit` defaults to 100 and must be 1–500. Pages are ordered by increasing
PostgreSQL feed sequence and contain `items`, `next_cursor`,
`cursor_expires_at`, and `has_more`. A cursor records the last sequence in the
page, so a client applies every item and tombstone durably before saving
`next_cursor`; interruption retries from the prior token and safely replays
items. Owner-scoped PostgreSQL advisory transaction locks serialize feed
sequence allocation through commit, preventing a later transaction from
committing an earlier sequence after a client has advanced past it.

Cursor tokens are random, opaque, owner-bound, and valid for 24 hours. Only a
SHA-256 token hash, owner, sequence position, and expiry are persisted in
PostgreSQL. Omitting the cursor starts replay from sequence zero. An unknown,
malformed, or other-owner token returns `400 invalid_cursor`; a known expired
token returns `410 cursor_expired`. For either error, clients discard their
cursor and restart without one, replaying the entire retained feed in order.
This recovery works because feed events and tombstones are currently retained
without compaction. If retention or compaction is introduced, a snapshot
recovery endpoint must be implemented before deleting feed history. The client
must not jump to the current feed head after a cursor error.
Expired cursor records are retained so the API can distinguish an expired
token from an unknown token; cursor-record cleanup policy is deferred.

## Versioning and compatibility

Protocol version 1 is required on every operation and successful response.
These routes currently support v1 only and do not expose a separate negotiation
endpoint. Clients send version 1; a mismatch returns `409 protocol_mismatch`
and leaves queued operations intact. Unknown operation types or required fields
are rejected rather than guessed. Additive optional fields may be ignored only
when the negotiated version defines that behavior.

## Conflict rules and older design notes

Revision comparison is the primary concurrency mechanism. No blanket
last-write-wins policy is defined. An operation may be automatically merged
only when its domain semantics are explicitly safe and the expected revision
has been checked. Otherwise both sides and the base (if available) are kept in
a durable conflict record. Resolution is revision-checked and idempotent.

This rule narrows older architecture prose that proposed a five-minute,
timestamp-based LWW fallback. That fallback is not part of M3 and must not be
implemented: timestamps cannot prove causality, and a stale writer must not
overwrite a newer revision.

## Integration boundary

The API authenticates every route with the existing user bearer-token
dependency and derives owner identity only from the authenticated user. Entity
loads, project/capture associations, dependency receipts, idempotency receipts,
conflicts, cursors, and feed reads are owner-scoped. Cross-owner entity reads
are reported as missing/invalid without disclosing the other owner's data;
cross-owner cursors are rejected as invalid. Missing or invalid bearer
credentials return `401`.

Revision conflicts are returned as per-operation `conflicted` acknowledgements
with a durable conflict ID. Clients inspect the owner-scoped record through
`GET /api/v1/sync/conflicts/{conflict_id}`. A revision conflict does not change
the canonical entity. Missing conflicts, including conflicts owned by another
user, return `404 conflict_not_found`. The API does not imply that the browser
or companion client is integrated. M2 authenticated outbound connectivity is
not yet connected to these routes, and the local simulator does not demonstrate
live cloud/device synchronization.
