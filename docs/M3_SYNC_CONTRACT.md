# M3 Offline Sync Contract (v1)

This is a standalone contract for the M3 sync engine, queues, Markdown
reconciliation, and simulator. It does not add or promise a production sync
endpoint. PostgreSQL remains canonical for cloud tasks, projects, and
operational state. Markdown files remain portable content artifacts.

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
  document, not a JSON patch. Delete carries no payload. This contract does
  not imply field-level merge semantics.
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

The server issues cursors tied to a stable feed position. Pages are ordered and
bounded, and provide `next_cursor` plus `has_more`. The client stores applied
entities/tombstones and the next cursor in one local transaction (or an
equivalent recoverable journal). It advances only after every item in that
page is safely applied. Duplicate events are harmless. An expired cursor
returns an explicit `cursor_expired` error and requires snapshot reconciliation
before a new cursor is saved; the client must not skip directly to the current
head.

## Versioning and compatibility

Protocol version 1 is required on every operation and successful response. A
client first negotiates supported versions; a batch-level mismatch is returned
using the server's supported error-envelope version (v1 here) and is a
permanent, inspectable error that leaves queued operations intact. Successful
upload responses match the negotiated request version. Unknown operation
types or required fields are rejected rather than guessed. Additive optional
fields may be ignored only when the negotiated version defines that behavior.

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

The current M1 API exposes revisioned task/project operations but does not
implement the sync upload, acknowledgement, conflict resolution, or change-feed
contract above. M2 device registration/authentication is also not a dependency
of these local components. Production integration requires a separately
reviewed API implementation and persistence design; this contract and its
simulator do not demonstrate live cloud/device synchronization.
