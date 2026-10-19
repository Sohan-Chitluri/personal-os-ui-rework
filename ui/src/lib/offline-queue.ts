/**
 * Durable browser-side operation queue. The queue logic depends on a small
 * storage transaction interface; the production adapter below uses IndexedDB.
 * The local companion does not use this browser adapter or a local queue.
 */

export const SYNC_PROTOCOL_VERSION = 1 as const;

export type EntityType = "task" | "project" | "project_document";
export type OperationType = "create" | "update" | "delete" | "resolve_conflict";
export type QueueStatus =
  "pending" | "in_flight" | "acknowledged" | "retryable" | "failed" | "conflicted" | "cancelled";
export type AckStatus = "applied" | "duplicate" | "conflicted" | "retryable" | "permanent";
export type ErrorCategory =
  | "transport"
  | "temporary_service"
  | "storage"
  | "malformed"
  | "unsupported"
  | "unauthorized"
  | "idempotency_mismatch"
  | "missing_entity"
  | "missing_base"
  | "expired_base"
  | "stale_revision"
  | "invalid_operation"
  | "dependency_pending"
  | "dependency_failed"
  | "protocol_mismatch"
  | "cursor_expired";

/**
 * JSON envelope matching SyncOperation in docs/M3_SYNC_CONTRACT.md. For
 * create/update/resolve_conflict, payload is the complete intended document,
 * not a patch. Updates and resolutions carry the exact observed revision.
 */
export interface SyncOperationEnvelope {
  protocol_version: typeof SYNC_PROTOCOL_VERSION;
  operation_id: string;
  idempotency_key: string;
  entity_type: EntityType;
  entity_id: string;
  operation_type: OperationType;
  base_revision: number | null;
  payload: Record<string, unknown> | null;
  created_at: string;
  depends_on: string[];
  resolves_conflict_id: string | null;
}

/** Acknowledgement fields mirror the version 1 shared Python contract. */
export interface OperationAcknowledgement {
  operation_id: string;
  status: AckStatus;
  revision: number | null;
  conflict_id: string | null;
  error_category: ErrorCategory | null;
  message: string | null;
  retry_after_seconds: number | null;
}

export interface QueueEntry {
  operation_id: string;
  idempotency_key: string;
  operation: SyncOperationEnvelope | null;
  status: QueueStatus;
  attempts: number;
  next_attempt_at: string | null;
  in_flight_until: string | null;
  acknowledgement: OperationAcknowledgement | null;
  error_category: ErrorCategory | null;
  last_error: string | null;
  enqueued_at: string;
  updated_at: string;
  malformed: boolean;
}

export interface QueueDiagnostics {
  total: number;
  by_status: Record<QueueStatus, number>;
  malformed: number;
  oldest_unresolved_at: string | null;
  recent_errors: Array<{ operation_id: string; message: string }>;
}

export interface QueueStoreTransaction {
  getAll(): Promise<unknown[]>;
  get(operationId: string): Promise<unknown | undefined>;
  add(entry: QueueEntry): Promise<void>;
  put(entry: QueueEntry): Promise<void>;
}

/** Implementations must serialize readwrite transactions durably. */
export interface QueueStore {
  transaction<T>(
    mode: "readonly" | "readwrite",
    work: (transaction: QueueStoreTransaction) => Promise<T>,
  ): Promise<T>;
  close?(): void;
}

export class OfflineQueueError extends Error {}
export class QueueStorageError extends OfflineQueueError {}
export class QueueValidationError extends OfflineQueueError {}
export class QueueIdempotencyError extends OfflineQueueError {}
export class QueueStateError extends OfflineQueueError {}

export interface OfflineQueueOptions {
  max_batch_size?: number;
  max_attempts?: number;
  in_flight_timeout_ms?: number;
  base_backoff_ms?: number;
  max_backoff_ms?: number;
  jitter_fn?: (delayCapMs: number) => number;
  now?: () => Date;
}

export interface RetryableFailure {
  operation_id: string;
  category: ErrorCategory;
  message: string;
  retry_after_seconds?: number | null;
}

export interface PermanentFailure {
  operation_id: string;
  category: ErrorCategory;
  message: string;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const OPERATION_TYPES: OperationType[] = ["create", "update", "delete", "resolve_conflict"];
const ENTITY_TYPES: EntityType[] = ["task", "project", "project_document"];
const ACK_STATUSES: AckStatus[] = ["applied", "duplicate", "conflicted", "retryable", "permanent"];
const ERROR_CATEGORIES: ErrorCategory[] = [
  "transport",
  "temporary_service",
  "storage",
  "malformed",
  "unsupported",
  "unauthorized",
  "idempotency_mismatch",
  "missing_entity",
  "missing_base",
  "expired_base",
  "stale_revision",
  "invalid_operation",
  "dependency_pending",
  "dependency_failed",
  "protocol_mismatch",
  "cursor_expired",
];
const QUEUE_STATUSES: QueueStatus[] = [
  "pending",
  "in_flight",
  "acknowledged",
  "retryable",
  "failed",
  "conflicted",
  "cancelled",
];

interface UnknownRecord extends Record<string, unknown> {
  protocol_version?: unknown;
  operation_id?: unknown;
  idempotency_key?: unknown;
  entity_type?: unknown;
  entity_id?: unknown;
  operation_type?: unknown;
  base_revision?: unknown;
  payload?: unknown;
  created_at?: unknown;
  depends_on?: unknown;
  resolves_conflict_id?: unknown;
  status?: unknown;
  revision?: unknown;
  conflict_id?: unknown;
  error_category?: unknown;
  message?: unknown;
  retry_after_seconds?: unknown;
  operation?: unknown;
  acknowledgement?: unknown;
  attempts?: unknown;
  next_attempt_at?: unknown;
  in_flight_until?: unknown;
  last_error?: unknown;
  enqueued_at?: unknown;
  updated_at?: unknown;
}

function isObject(value: unknown): value is UnknownRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (isObject(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

function assertJsonValue(value: unknown, path: string, active: Set<object> = new Set()): void {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return;
  }
  if (Array.isArray(value)) {
    if (active.has(value)) throw new QueueValidationError(`${path} cannot contain a cycle`);
    active.add(value);
    value.forEach((item, index) => assertJsonValue(item, `${path}[${index}]`, active));
    active.delete(value);
    return;
  }
  if (
    isObject(value) &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)
  ) {
    if (active.has(value)) throw new QueueValidationError(`${path} cannot contain a cycle`);
    active.add(value);
    for (const [key, item] of Object.entries(value)) {
      assertJsonValue(item, `${path}.${key}`, active);
    }
    active.delete(value);
    return;
  }
  throw new QueueValidationError(`${path} must contain JSON-compatible values`);
}

export function validateSyncOperation(value: unknown): SyncOperationEnvelope {
  const fields = [
    "protocol_version",
    "operation_id",
    "idempotency_key",
    "entity_type",
    "entity_id",
    "operation_type",
    "base_revision",
    "payload",
    "created_at",
    "depends_on",
    "resolves_conflict_id",
  ];
  if (!isObject(value) || !hasExactKeys(value, fields)) {
    throw new QueueValidationError("operation must match the complete version 1 envelope");
  }
  if (value.protocol_version !== SYNC_PROTOCOL_VERSION) {
    throw new QueueValidationError("unsupported sync protocol version");
  }
  if (typeof value.operation_id !== "string" || !UUID_PATTERN.test(value.operation_id)) {
    throw new QueueValidationError("operation_id must be a UUID");
  }
  if (
    typeof value.idempotency_key !== "string" ||
    value.idempotency_key.length < 1 ||
    value.idempotency_key.length > 200
  ) {
    throw new QueueValidationError("idempotency_key must contain 1 to 200 characters");
  }
  if (!ENTITY_TYPES.includes(value.entity_type as EntityType)) {
    throw new QueueValidationError("unsupported entity_type");
  }
  if (
    typeof value.entity_id !== "string" ||
    value.entity_id.length < 1 ||
    value.entity_id.length > 200
  ) {
    throw new QueueValidationError("entity_id must contain 1 to 200 characters");
  }
  if (!OPERATION_TYPES.includes(value.operation_type as OperationType)) {
    throw new QueueValidationError("unsupported operation_type");
  }
  const operationType = value.operation_type as OperationType;
  const revision = value.base_revision;
  if (revision !== null && (!Number.isSafeInteger(revision) || (revision as number) < 0)) {
    throw new QueueValidationError("base_revision must be a nonnegative integer or null");
  }
  const payload = value.payload;
  if (payload !== null && !isObject(payload)) {
    throw new QueueValidationError("payload must be a complete JSON object or null");
  }
  if (payload !== null) assertJsonValue(payload, "payload");
  if (
    (operationType === "create" && (revision !== null || payload === null)) ||
    (operationType === "update" && (revision === null || payload === null)) ||
    (operationType === "delete" && (revision === null || payload !== null)) ||
    (operationType === "resolve_conflict" &&
      (revision === null || payload === null || value.resolves_conflict_id === null))
  ) {
    throw new QueueValidationError("operation fields do not match operation_type requirements");
  }
  if (operationType !== "resolve_conflict" && value.resolves_conflict_id !== null) {
    throw new QueueValidationError("resolves_conflict_id is only valid for conflict resolution");
  }
  if (
    value.resolves_conflict_id !== null &&
    (typeof value.resolves_conflict_id !== "string" ||
      !UUID_PATTERN.test(value.resolves_conflict_id))
  ) {
    throw new QueueValidationError("resolves_conflict_id must be a UUID or null");
  }
  if (
    typeof value.created_at !== "string" ||
    !/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value.created_at) ||
    !Number.isFinite(Date.parse(value.created_at))
  ) {
    throw new QueueValidationError("created_at must be an ISO timestamp with a timezone");
  }
  if (
    !Array.isArray(value.depends_on) ||
    value.depends_on.some((id) => typeof id !== "string" || !UUID_PATTERN.test(id))
  ) {
    throw new QueueValidationError("depends_on must be a list of UUIDs");
  }
  const dependencies = value.depends_on as string[];
  if (
    new Set(dependencies).size !== dependencies.length ||
    dependencies.includes(value.operation_id)
  ) {
    throw new QueueValidationError("depends_on cannot contain duplicates or the operation itself");
  }
  const normalized = structuredClone(value) as unknown as SyncOperationEnvelope;
  normalized.operation_id = normalized.operation_id.toLowerCase();
  normalized.created_at = normalizeUtcTimestamp(normalized.created_at);
  normalized.depends_on = normalized.depends_on.map((id) => id.toLowerCase());
  normalized.resolves_conflict_id = normalized.resolves_conflict_id?.toLowerCase() ?? null;
  return normalized;
}

function validateAcknowledgement(value: unknown): OperationAcknowledgement {
  const fields = [
    "operation_id",
    "status",
    "revision",
    "conflict_id",
    "error_category",
    "message",
    "retry_after_seconds",
  ];
  if (!isObject(value) || !hasExactKeys(value, fields)) {
    throw new QueueValidationError("acknowledgement must match the version 1 contract");
  }
  if (typeof value.operation_id !== "string" || !UUID_PATTERN.test(value.operation_id)) {
    throw new QueueValidationError("acknowledgement operation_id must be a UUID");
  }
  if (!ACK_STATUSES.includes(value.status as AckStatus)) {
    throw new QueueValidationError("unsupported acknowledgement status");
  }
  if (
    value.revision !== null &&
    (!Number.isSafeInteger(value.revision) || (value.revision as number) < 0)
  ) {
    throw new QueueValidationError("acknowledgement revision must be nonnegative or null");
  }
  if (
    value.conflict_id !== null &&
    (typeof value.conflict_id !== "string" || !UUID_PATTERN.test(value.conflict_id))
  ) {
    throw new QueueValidationError("conflict_id must be a UUID or null");
  }
  if (
    value.error_category !== null &&
    !ERROR_CATEGORIES.includes(value.error_category as ErrorCategory)
  ) {
    throw new QueueValidationError("unsupported acknowledgement error category");
  }
  if (value.message !== null && typeof value.message !== "string") {
    throw new QueueValidationError("acknowledgement message must be a string or null");
  }
  if (
    value.retry_after_seconds !== null &&
    (typeof value.retry_after_seconds !== "number" ||
      !Number.isFinite(value.retry_after_seconds) ||
      value.retry_after_seconds < 0)
  ) {
    throw new QueueValidationError("retry_after_seconds must be nonnegative or null");
  }
  const status = value.status as AckStatus;
  if (
    (status === "conflicted" && value.conflict_id === null) ||
    ((status === "retryable" || status === "permanent") && value.error_category === null)
  ) {
    throw new QueueValidationError("acknowledgement status is missing required details");
  }
  const normalized = structuredClone(value) as unknown as OperationAcknowledgement;
  normalized.operation_id = normalized.operation_id.toLowerCase();
  normalized.conflict_id = normalized.conflict_id?.toLowerCase() ?? null;
  return normalized;
}

function validateNow(now: Date): Date {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) {
    throw new QueueValidationError("queue timestamp must be a valid Date");
  }
  return now;
}

function normalizeUtcTimestamp(value: string): string {
  const fraction = /\.(\d+)(?=(?:Z|[+-]\d{2}:\d{2})$)/i.exec(value)?.[1];
  const instant = new Date(value).toISOString();
  if (!fraction || /^0+$/.test(fraction)) return instant.replace(".000Z", "Z");
  const microseconds = `${fraction}000000`.slice(0, 6);
  return `${instant.slice(0, 19)}.${microseconds}Z`;
}

function entryFromStored(value: unknown): QueueEntry {
  const record = isObject(value) ? value : {};
  const operationId = typeof record.operation_id === "string" ? record.operation_id : "unknown";
  const idempotencyKey = typeof record.idempotency_key === "string" ? record.idempotency_key : "";
  let operation: SyncOperationEnvelope | null = null;
  let acknowledgement: OperationAcknowledgement | null = null;
  let malformed = !isObject(value);
  try {
    if (record.operation !== null) operation = validateSyncOperation(record.operation);
    else malformed = true;
  } catch {
    malformed = true;
  }
  try {
    if (record.acknowledgement !== null && record.acknowledgement !== undefined) {
      acknowledgement = validateAcknowledgement(record.acknowledgement);
    }
  } catch {
    malformed = true;
  }
  const status = QUEUE_STATUSES.includes(record.status as QueueStatus)
    ? (record.status as QueueStatus)
    : "failed";
  if (status === "failed" && !QUEUE_STATUSES.includes(record.status as QueueStatus))
    malformed = true;
  const dateOrNow = (candidate: unknown): string =>
    typeof candidate === "string" && Number.isFinite(Date.parse(candidate))
      ? new Date(candidate).toISOString()
      : new Date(0).toISOString();
  const nullableDate = (candidate: unknown): string | null =>
    candidate === null || candidate === undefined
      ? null
      : typeof candidate === "string" && Number.isFinite(Date.parse(candidate))
        ? new Date(candidate).toISOString()
        : null;
  const category = ERROR_CATEGORIES.includes(record.error_category as ErrorCategory)
    ? (record.error_category as ErrorCategory)
    : null;
  if (record.error_category !== null && record.error_category !== undefined && category === null) {
    malformed = true;
  }
  return {
    operation_id: operationId,
    idempotency_key: idempotencyKey,
    operation,
    status,
    attempts:
      Number.isSafeInteger(record.attempts) && (record.attempts as number) >= 0
        ? (record.attempts as number)
        : 0,
    next_attempt_at: nullableDate(record.next_attempt_at),
    in_flight_until: nullableDate(record.in_flight_until),
    acknowledgement,
    error_category: category,
    last_error: typeof record.last_error === "string" ? record.last_error : null,
    enqueued_at: dateOrNow(record.enqueued_at),
    updated_at: dateOrNow(record.updated_at),
    malformed,
  };
}

export class OfflineOperationQueue {
  private readonly options: Required<OfflineQueueOptions>;

  constructor(
    private readonly store: QueueStore,
    options: OfflineQueueOptions = {},
  ) {
    this.options = {
      max_batch_size: options.max_batch_size ?? 50,
      max_attempts: options.max_attempts ?? 8,
      in_flight_timeout_ms: options.in_flight_timeout_ms ?? 120_000,
      base_backoff_ms: options.base_backoff_ms ?? 1_000,
      max_backoff_ms: options.max_backoff_ms ?? 300_000,
      jitter_fn: options.jitter_fn ?? ((cap) => Math.random() * cap),
      now: options.now ?? (() => new Date()),
    };
    if (this.options.max_batch_size < 1 || this.options.max_attempts < 1) {
      throw new RangeError("batch size and attempt limit must be positive");
    }
    if (
      this.options.in_flight_timeout_ms < 0 ||
      this.options.base_backoff_ms < 0 ||
      this.options.max_backoff_ms < 0
    ) {
      throw new RangeError("lease and backoff durations cannot be negative");
    }
  }

  async enqueue(rawOperation: unknown): Promise<QueueEntry> {
    const operation = validateSyncOperation(rawOperation);
    const now = validateNow(this.options.now()).toISOString();
    return this.store.transaction("readwrite", async (transaction) => {
      const entries = (await transaction.getAll()).map(entryFromStored);
      const duplicate = entries.find(
        (entry) =>
          entry.operation_id === operation.operation_id ||
          entry.idempotency_key === operation.idempotency_key,
      );
      if (duplicate) {
        if (
          duplicate.operation_id !== operation.operation_id ||
          duplicate.idempotency_key !== operation.idempotency_key ||
          !duplicate.operation ||
          canonicalJson(duplicate.operation) !== canonicalJson(operation)
        ) {
          throw new QueueIdempotencyError(
            "operation ID or idempotency key belongs to a different body",
          );
        }
        return duplicate;
      }
      const entry: QueueEntry = {
        operation_id: operation.operation_id,
        idempotency_key: operation.idempotency_key,
        operation,
        status: "pending",
        attempts: 0,
        next_attempt_at: null,
        in_flight_until: null,
        acknowledgement: null,
        error_category: null,
        last_error: null,
        enqueued_at: now,
        updated_at: now,
        malformed: false,
      };
      await transaction.add(entry);
      return entry;
    });
  }

  async claimBatch(options: { now?: Date; limit?: number } = {}): Promise<QueueEntry[]> {
    const now = validateNow(options.now ?? this.options.now());
    const limit = options.limit ?? this.options.max_batch_size;
    if (!Number.isSafeInteger(limit) || limit < 1)
      throw new RangeError("batch limit must be positive");
    const boundedLimit = Math.min(limit, this.options.max_batch_size);
    return this.store.transaction("readwrite", async (transaction) => {
      const entries = (await transaction.getAll()).map(entryFromStored);
      for (let index = 0; index < entries.length; index += 1) {
        const entry = entries[index];
        if (
          entry?.status === "in_flight" &&
          (entry.in_flight_until === null || Date.parse(entry.in_flight_until) <= now.getTime())
        ) {
          const recovered: QueueEntry = {
            ...entry,
            status: "retryable",
            next_attempt_at: now.toISOString(),
            in_flight_until: null,
            error_category: "transport",
            last_error:
              "in-flight lease expired; server outcome is unknown and the same idempotency key will be retried",
            updated_at: now.toISOString(),
          };
          entries[index] = recovered;
          await transaction.put(recovered);
        }
      }
      const byId = new Map(entries.map((entry) => [entry.operation_id, entry]));
      const candidates = entries
        .filter(
          (entry) =>
            (entry.status === "pending" || entry.status === "retryable") &&
            (entry.next_attempt_at === null || Date.parse(entry.next_attempt_at) <= now.getTime()),
        )
        .sort(
          (a, b) =>
            Date.parse(a.enqueued_at) - Date.parse(b.enqueued_at) ||
            a.operation_id.localeCompare(b.operation_id),
        );
      const claimed: QueueEntry[] = [];
      for (const entry of candidates) {
        if (claimed.length >= boundedLimit) break;
        if (!entry.operation || entry.malformed) {
          await transaction.put({
            ...entry,
            status: "failed",
            error_category: "malformed",
            last_error: "stored operation is malformed and was retained for inspection",
            next_attempt_at: null,
            updated_at: now.toISOString(),
          });
          continue;
        }
        const dependencies = entry.operation.depends_on.map((id) => byId.get(id));
        if (
          dependencies.some(
            (dependency) =>
              dependency && ["failed", "conflicted", "cancelled"].includes(dependency.status),
          )
        ) {
          await transaction.put({
            ...entry,
            status: "failed",
            error_category: "dependency_failed",
            last_error: "a required operation failed, conflicted, or was cancelled",
            next_attempt_at: null,
            updated_at: now.toISOString(),
          });
          continue;
        }
        if (dependencies.some((dependency) => dependency?.status !== "acknowledged")) {
          const missing = entry.operation.depends_on.filter((id) => !byId.has(id));
          const waiting = entry.operation.depends_on.filter(
            (id) => byId.get(id)?.status !== "acknowledged",
          );
          await transaction.put({
            ...entry,
            error_category: "dependency_pending",
            last_error:
              missing.length > 0
                ? `waiting for missing dependency ${missing.join(", ")}`
                : `waiting for prerequisite operation ${waiting.join(", ")}`,
            updated_at: now.toISOString(),
          });
          continue;
        }
        if (entry.attempts >= this.options.max_attempts) {
          await transaction.put({
            ...entry,
            status: "failed",
            error_category: "temporary_service",
            last_error: "retry attempts exhausted; operation retained for inspection",
            next_attempt_at: null,
            in_flight_until: null,
            updated_at: now.toISOString(),
          });
          continue;
        }
        const sending: QueueEntry = {
          ...entry,
          status: "in_flight",
          attempts: entry.attempts + 1,
          next_attempt_at: null,
          in_flight_until: new Date(
            now.getTime() + this.options.in_flight_timeout_ms,
          ).toISOString(),
          error_category:
            entry.error_category === "dependency_pending" ? null : entry.error_category,
          last_error: entry.error_category === "dependency_pending" ? null : entry.last_error,
          updated_at: now.toISOString(),
        };
        await transaction.put(sending);
        byId.set(entry.operation_id, sending);
        claimed.push(sending);
      }
      return claimed;
    });
  }

  async acknowledge(rawAcknowledgement: unknown, now?: Date): Promise<QueueEntry> {
    const acknowledgement = validateAcknowledgement(rawAcknowledgement);
    const current = validateNow(now ?? this.options.now());
    return this.store.transaction("readwrite", async (transaction) => {
      const rawEntry = await transaction.get(acknowledgement.operation_id);
      if (rawEntry === undefined)
        throw new QueueStateError("acknowledgement references an unknown operation");
      const entry = entryFromStored(rawEntry);
      if (entry.acknowledgement) {
        if (canonicalJson(entry.acknowledgement) === canonicalJson(acknowledgement)) return entry;
        if (
          ["applied", "duplicate"].includes(entry.acknowledgement.status) &&
          ["applied", "duplicate"].includes(acknowledgement.status) &&
          entry.acknowledgement.revision === acknowledgement.revision
        ) {
          return entry;
        }
        if (entry.acknowledgement.status !== "retryable") {
          throw new QueueStateError("operation already has a different durable acknowledgement");
        }
      }
      if (entry.status === "cancelled")
        throw new QueueStateError("a cancelled operation cannot be acknowledged");
      let status: QueueStatus;
      let nextAttempt: string | null = null;
      let category = acknowledgement.error_category;
      let message = acknowledgement.message;
      if (acknowledgement.status === "applied" || acknowledgement.status === "duplicate") {
        status = "acknowledged";
        category = null;
        message = null;
      } else if (acknowledgement.status === "conflicted") {
        status = "conflicted";
        message ??= "server reported a conflict";
      } else if (acknowledgement.status === "permanent") {
        status = "failed";
        message ??= "server permanently rejected the operation";
      } else if (entry.attempts >= this.options.max_attempts) {
        status = "failed";
        message = `retry attempts exhausted: ${message ?? "server reported a retryable failure"}`;
      } else {
        status = "retryable";
        const delay = this.retryDelay(entry.attempts, acknowledgement.retry_after_seconds);
        nextAttempt = new Date(current.getTime() + delay).toISOString();
        message ??= "server reported a retryable failure";
      }
      const updated: QueueEntry = {
        ...entry,
        status,
        next_attempt_at: nextAttempt,
        in_flight_until: null,
        acknowledgement,
        error_category: category,
        last_error: message,
        updated_at: current.toISOString(),
      };
      await transaction.put(updated);
      return updated;
    });
  }

  async markRetryable(failure: RetryableFailure, now?: Date): Promise<QueueEntry> {
    const current = validateNow(now ?? this.options.now());
    return this.store.transaction("readwrite", async (transaction) => {
      const rawEntry = await transaction.get(failure.operation_id);
      if (rawEntry === undefined) throw new QueueStateError("unknown operation");
      const entry = entryFromStored(rawEntry);
      if (entry.status !== "in_flight")
        throw new QueueStateError("only an in-flight operation can retry");
      const exhausted = entry.attempts >= this.options.max_attempts;
      const delay = exhausted
        ? null
        : this.retryDelay(entry.attempts, failure.retry_after_seconds ?? null);
      const updated: QueueEntry = {
        ...entry,
        status: exhausted ? "failed" : "retryable",
        next_attempt_at: delay === null ? null : new Date(current.getTime() + delay).toISOString(),
        in_flight_until: null,
        error_category: failure.category,
        last_error: exhausted ? `retry attempts exhausted: ${failure.message}` : failure.message,
        updated_at: current.toISOString(),
      };
      await transaction.put(updated);
      return updated;
    });
  }

  async markFailed(failure: PermanentFailure, now?: Date): Promise<QueueEntry> {
    const current = validateNow(now ?? this.options.now());
    return this.store.transaction("readwrite", async (transaction) => {
      const rawEntry = await transaction.get(failure.operation_id);
      if (rawEntry === undefined) throw new QueueStateError("unknown operation");
      const entry = entryFromStored(rawEntry);
      if (!["in_flight", "pending", "retryable"].includes(entry.status)) {
        throw new QueueStateError(`cannot permanently fail an operation in ${entry.status} state`);
      }
      const updated: QueueEntry = {
        ...entry,
        status: "failed",
        next_attempt_at: null,
        in_flight_until: null,
        error_category: failure.category,
        last_error: failure.message,
        updated_at: current.toISOString(),
      };
      await transaction.put(updated);
      return updated;
    });
  }

  async cancel(operationId: string, now?: Date): Promise<QueueEntry> {
    const current = validateNow(now ?? this.options.now());
    return this.store.transaction("readwrite", async (transaction) => {
      const rawEntry = await transaction.get(operationId);
      if (rawEntry === undefined) throw new QueueStateError("unknown operation");
      const entry = entryFromStored(rawEntry);
      if (entry.attempts !== 0) {
        throw new QueueStateError(
          "an operation that may have reached the server cannot be cancelled",
        );
      }
      if (entry.status !== "pending" && entry.status !== "retryable") {
        throw new QueueStateError("only pending or retryable operations can be cancelled");
      }
      const all = (await transaction.getAll()).map(entryFromStored);
      const hasDependent = all.some(
        (candidate) =>
          candidate.operation_id !== operationId &&
          candidate.operation?.depends_on.includes(operationId) === true &&
          !["acknowledged", "failed", "conflicted", "cancelled"].includes(candidate.status),
      );
      if (hasDependent)
        throw new QueueStateError("operation has an unresolved dependent and cannot be cancelled");
      const updated: QueueEntry = {
        ...entry,
        status: "cancelled",
        next_attempt_at: null,
        in_flight_until: null,
        error_category: null,
        last_error: "cancelled before first send",
        updated_at: current.toISOString(),
      };
      await transaction.put(updated);
      return updated;
    });
  }

  async inspect(options: { statuses?: QueueStatus[]; limit?: number } = {}): Promise<QueueEntry[]> {
    return this.store.transaction("readonly", async (transaction) => {
      let entries = (await transaction.getAll())
        .map(entryFromStored)
        .sort((a, b) => Date.parse(a.enqueued_at) - Date.parse(b.enqueued_at));
      if (options.statuses)
        entries = entries.filter((entry) => options.statuses?.includes(entry.status));
      if (options.limit !== undefined) {
        if (!Number.isSafeInteger(options.limit) || options.limit < 0) {
          throw new RangeError("inspection limit cannot be negative");
        }
        entries = entries.slice(0, options.limit);
      }
      return entries;
    });
  }

  async diagnostics(recentErrorLimit = 10): Promise<QueueDiagnostics> {
    const entries = await this.inspect();
    const byStatus = Object.fromEntries(QUEUE_STATUSES.map((status) => [status, 0])) as Record<
      QueueStatus,
      number
    >;
    for (const entry of entries) byStatus[entry.status] += 1;
    const unresolved = entries
      .filter((entry) => ["pending", "in_flight", "retryable"].includes(entry.status))
      .map((entry) => entry.enqueued_at)
      .sort();
    return {
      total: entries.length,
      by_status: byStatus,
      malformed: entries.filter((entry) => entry.malformed).length,
      oldest_unresolved_at: unresolved[0] ?? null,
      recent_errors: entries
        .filter((entry) => entry.last_error !== null)
        .slice(-Math.max(0, recentErrorLimit))
        .reverse()
        .map((entry) => ({ operation_id: entry.operation_id, message: entry.last_error ?? "" })),
    };
  }

  close(): void {
    this.store.close?.();
  }

  private retryDelay(attempts: number, retryAfterSeconds: number | null): number {
    const exponential = this.options.base_backoff_ms * 2 ** Math.max(0, attempts - 1);
    let cap = Math.min(this.options.max_backoff_ms, exponential);
    const retryAfter = retryAfterSeconds === null ? null : Math.max(0, retryAfterSeconds * 1_000);
    if (retryAfter !== null) cap = Math.min(this.options.max_backoff_ms, Math.max(cap, retryAfter));
    const raw = this.options.jitter_fn(cap);
    if (!Number.isFinite(raw))
      throw new QueueStateError("backoff jitter returned a non-finite delay");
    let delay = Math.min(cap, Math.max(0, raw));
    if (retryAfter !== null)
      delay = Math.max(delay, Math.min(this.options.max_backoff_ms, retryAfter));
    return Math.min(this.options.max_backoff_ms, delay);
  }
}

export interface IndexedDbQueueOptions extends OfflineQueueOptions {
  database_name?: string;
  indexed_db_factory?: IDBFactory;
}

class IndexedDbQueueStore implements QueueStore {
  private databasePromise: Promise<IDBDatabase> | null = null;

  constructor(
    private readonly factory: IDBFactory,
    private readonly databaseName: string,
  ) {}

  async transaction<T>(
    mode: "readonly" | "readwrite",
    work: (transaction: QueueStoreTransaction) => Promise<T>,
  ): Promise<T> {
    let database: IDBDatabase;
    try {
      database = await this.open();
    } catch (error) {
      throw error instanceof QueueStorageError
        ? error
        : new QueueStorageError(`cannot open IndexedDB queue: ${String(error)}`);
    }
    let transaction: IDBTransaction;
    try {
      transaction = database.transaction("operations", mode);
    } catch (error) {
      throw new QueueStorageError(`cannot start IndexedDB transaction: ${String(error)}`);
    }
    const done = new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onabort = () =>
        reject(transaction.error ?? new Error("IndexedDB transaction aborted"));
      transaction.onerror = () =>
        reject(transaction.error ?? new Error("IndexedDB transaction failed"));
    });
    const store = transaction.objectStore("operations");
    const adapter: QueueStoreTransaction = {
      getAll: () => this.request(store.getAll()),
      get: (operationId) => this.request(store.get(operationId)),
      add: async (entry) => {
        await this.request(store.add(entry));
      },
      put: async (entry) => {
        await this.request(store.put(entry));
      },
    };
    try {
      const result = await work(adapter);
      await done;
      return result;
    } catch (error) {
      try {
        transaction.abort();
      } catch {
        // The transaction may already be inactive or aborted by IndexedDB.
      }
      await done.catch(() => undefined);
      if (error instanceof OfflineQueueError) throw error;
      throw new QueueStorageError(`IndexedDB queue transaction failed: ${String(error)}`);
    }
  }

  close(): void {
    void this.databasePromise?.then((database) => database.close());
    this.databasePromise = null;
  }

  private open(): Promise<IDBDatabase> {
    if (this.databasePromise) return this.databasePromise;
    this.databasePromise = new Promise<IDBDatabase>((resolve, reject) => {
      const request = this.factory.open(this.databaseName, 1);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains("operations")) {
          database.createObjectStore("operations", { keyPath: "operation_id" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("IndexedDB open failed"));
      request.onblocked = () => reject(new Error("IndexedDB upgrade is blocked by another client"));
    }).catch((error: unknown) => {
      this.databasePromise = null;
      throw error;
    });
    return this.databasePromise;
  }

  private request<T>(request: IDBRequest<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
    });
  }
}

/** Browser adapter. Pass a separate IDBFactory/name in tests or embedded apps. */
export class IndexedDbOfflineOperationQueue extends OfflineOperationQueue {
  constructor(options: IndexedDbQueueOptions = {}) {
    const factory = options.indexed_db_factory ?? globalThis.indexedDB;
    if (!factory) throw new QueueStorageError("IndexedDB is unavailable in this environment");
    const { database_name = "personal-os-offline-sync-v1", ...queueOptions } = options;
    super(new IndexedDbQueueStore(factory, database_name), queueOptions);
  }
}
