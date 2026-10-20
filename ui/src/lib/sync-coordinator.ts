import {
  type OfflineOperationQueue,
  type OperationAcknowledgement,
  type QueueDiagnostics,
  type QueueEntry,
  type SyncOperationEnvelope,
} from "./offline-queue";
import {
  SyncApiError,
  type SyncApi,
  type SyncChangeItem,
  type SyncConflict,
  type SyncChangePage,
} from "./sync-api";

export type SyncStatus = "pending" | "syncing" | "synced" | "conflict" | "failed";
export type PersistedSyncState = { key: "cursor"; value: string };

export interface SyncPersistence {
  getCursor(): Promise<string | null>;
  clearCursor(): Promise<void>;
  commitPage(items: SyncChangeItem[], nextCursor: string): Promise<void>;
  listAppliedChanges(): Promise<SyncChangeItem[]>;
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

/** Stores applied feed records and their resume cursor in one IndexedDB transaction. */
export class IndexedDbSyncPersistence implements SyncPersistence {
  private databasePromise: Promise<IDBDatabase> | null = null;

  constructor(
    private readonly databaseName: string,
    private readonly factory: IDBFactory = globalThis.indexedDB,
  ) {
    if (!factory) throw new Error("IndexedDB is unavailable in this environment");
  }

  async getCursor(): Promise<string | null> {
    const db = await this.open();
    const tx = db.transaction("sync_metadata", "readonly");
    const record = (await requestResult(tx.objectStore("sync_metadata").get("cursor"))) as
      PersistedSyncState | undefined;
    return record?.value ?? null;
  }

  async clearCursor(): Promise<void> {
    const db = await this.open();
    const tx = db.transaction("sync_metadata", "readwrite");
    tx.objectStore("sync_metadata").delete("cursor");
    await this.transactionDone(tx);
  }

  async commitPage(items: SyncChangeItem[], nextCursor: string): Promise<void> {
    const db = await this.open();
    const tx = db.transaction(["sync_metadata", "applied_changes"], "readwrite");
    const changes = tx.objectStore("applied_changes");
    for (const item of items) changes.put(item);
    tx.objectStore("sync_metadata").put({
      key: "cursor",
      value: nextCursor,
    } satisfies PersistedSyncState);
    await this.transactionDone(tx);
  }

  async listAppliedChanges(): Promise<SyncChangeItem[]> {
    const db = await this.open();
    const tx = db.transaction("applied_changes", "readonly");
    const rows = (await requestResult(
      tx.objectStore("applied_changes").getAll(),
    )) as SyncChangeItem[];
    return rows.sort((a, b) => a.sequence - b.sequence);
  }

  close(): void {
    void this.databasePromise?.then((db) => db.close());
    this.databasePromise = null;
  }

  private open(): Promise<IDBDatabase> {
    if (this.databasePromise) return this.databasePromise;
    this.databasePromise = new Promise<IDBDatabase>((resolve, reject) => {
      const request = this.factory.open(this.databaseName, 2);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("operations")) {
          db.createObjectStore("operations", { keyPath: "operation_id" });
        }
        if (!db.objectStoreNames.contains("sync_metadata")) {
          db.createObjectStore("sync_metadata", { keyPath: "key" });
        }
        if (!db.objectStoreNames.contains("applied_changes")) {
          db.createObjectStore("applied_changes", { keyPath: "event_id" });
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

  private transactionDone(tx: IDBTransaction): Promise<void> {
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
      tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    });
  }
}

export class SyncCoordinator {
  private running = false;
  private statusValue: SyncStatus = "synced";
  private errorValue: string | null = null;

  constructor(
    readonly queue: OfflineOperationQueue,
    readonly persistence: SyncPersistence,
    private readonly api: SyncApi,
    private readonly onState?: (state: SyncCoordinatorState) => void,
    private readonly feedLimit = 100,
    private readonly onChangesApplied?: (items: SyncChangeItem[]) => void,
    private readonly onOperationAcknowledged?: (
      entry: QueueEntry,
      ack: OperationAcknowledgement,
    ) => void,
  ) {}

  get status(): SyncStatus {
    return this.statusValue;
  }
  get error(): string | null {
    return this.errorValue;
  }

  async diagnostics(): Promise<QueueDiagnostics> {
    return this.queue.diagnostics();
  }

  async conflicts(): Promise<QueueEntry[]> {
    return this.queue.inspect({ statuses: ["conflicted", "failed"] });
  }

  async inspectConflict(conflictId: string): Promise<SyncConflict> {
    return this.api.getConflict(conflictId);
  }

  async syncNow(): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.setState("syncing", null);
    try {
      await this.flushQueue();
      await this.pullChanges();
      await this.refreshStatus();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.setState(
        error instanceof SyncApiError && [400, 401, 403, 409, 422].includes(error.status)
          ? "failed"
          : "pending",
        message,
      );
      throw error;
    } finally {
      this.running = false;
    }
  }

  async refreshStatus(): Promise<void> {
    const entries = await this.queue.inspect();
    if (entries.some((entry) => entry.status === "conflicted")) {
      this.setState("conflict", null);
    } else if (entries.some((entry) => entry.status === "failed")) {
      this.setState(
        "failed",
        entries.find((entry) => entry.status === "failed")?.last_error ?? null,
      );
    } else if (
      entries.some((entry) => ["pending", "in_flight", "retryable"].includes(entry.status))
    ) {
      this.setState("pending", null);
    } else {
      this.setState("synced", null);
    }
  }

  private async flushQueue(): Promise<void> {
    for (;;) {
      const batch = await this.queue.claimBatch({ limit: 100 });
      if (batch.length === 0) return;
      const operations = batch
        .map((entry) => entry.operation)
        .filter((op): op is SyncOperationEnvelope => op !== null);
      if (operations.length !== batch.length) continue;
      let acknowledgements;
      try {
        const response = await this.api.uploadOperations(operations);
        if (response.protocol_version !== 1 || !Array.isArray(response.acknowledgements)) {
          throw new Error("Synchronization server returned an unsupported or malformed response.");
        }
        acknowledgements = response.acknowledgements;
      } catch (error) {
        if (error instanceof SyncApiError && [400, 409, 422].includes(error.status)) {
          for (const entry of batch) {
            await this.queue.markFailed({
              operation_id: entry.operation_id,
              category:
                error.code === "protocol_mismatch" ? "protocol_mismatch" : "invalid_operation",
              message: error.message,
            });
          }
        } else {
          for (const entry of batch) {
            await this.queue.markRetryable({
              operation_id: entry.operation_id,
              category:
                error instanceof SyncApiError && error.status === 401
                  ? "unauthorized"
                  : "transport",
              message: error instanceof Error ? error.message : String(error),
            });
          }
        }
        if (error instanceof SyncApiError && [400, 401, 403, 409, 422].includes(error.status))
          throw error;
        return;
      }
      const batchIds = new Set(batch.map((entry) => entry.operation_id));
      const seen = new Set<string>();
      for (const acknowledgement of acknowledgements) {
        if (!batchIds.has(acknowledgement.operation_id) || seen.has(acknowledgement.operation_id))
          continue;
        seen.add(acknowledgement.operation_id);
        let entry: QueueEntry;
        try {
          entry = await this.queue.acknowledge(acknowledgement);
        } catch {
          // Treat malformed acknowledgements as missing: preserve the original
          // operation and retry under its stable idempotency key.
          continue;
        }
        if (entry.status === "conflicted" && acknowledgement.conflict_id) {
          // The durable acknowledgement already preserves the conflict ID and
          // local payload; fetching the richer record is best-effort for the UI.
          try {
            await this.api.getConflict(acknowledgement.conflict_id);
          } catch {
            // A later inspection can fetch it. Never downgrade the local conflict.
          }
        }
        this.onOperationAcknowledged?.(entry, acknowledgement);
      }
      for (const entry of batch) {
        if (!seen.has(entry.operation_id)) {
          await this.queue.markRetryable({
            operation_id: entry.operation_id,
            category: "transport",
            message: "server omitted an acknowledgement; retrying with the same idempotency key",
          });
        }
      }
      if (batch.some((entry) => !seen.has(entry.operation_id))) return;
    }
  }

  private async pullChanges(): Promise<void> {
    let cursor = await this.persistence.getCursor();
    let restarted = false;
    for (;;) {
      let page: SyncChangePage;
      try {
        page = await this.api.getChanges(cursor, this.feedLimit);
      } catch (error) {
        const recoverableCursorError =
          error instanceof SyncApiError &&
          ((error.status === 410 && (error.code === "cursor_expired" || error.code === null)) ||
            (error.status === 400 && (error.code === "invalid_cursor" || error.code === null)));
        if (recoverableCursorError && cursor !== null && !restarted) {
          await this.persistence.clearCursor();
          cursor = null;
          restarted = true;
          continue;
        }
        throw error;
      }
      this.validatePage(page);
      // Item writes and cursor movement commit atomically. A crash before the
      // transaction commits replays this page; event_id keys make that safe.
      await this.persistence.commitPage(page.items, page.next_cursor);
      if (page.items.length > 0) this.onChangesApplied?.(page.items);
      cursor = page.next_cursor;
      if (!page.has_more) return;
    }
  }

  private validatePage(page: SyncChangePage): void {
    if (
      page.protocol_version !== 1 ||
      !Array.isArray(page.items) ||
      typeof page.next_cursor !== "string" ||
      !page.next_cursor
    ) {
      throw new Error("Synchronization server returned a malformed change page.");
    }
    let previous = -1;
    for (const item of page.items) {
      if (!Number.isSafeInteger(item.sequence) || item.sequence <= previous || !item.event_id) {
        throw new Error(
          "Synchronization feed items are not in strictly increasing sequence order.",
        );
      }
      previous = item.sequence;
      if ((item.snapshot === null) === (item.tombstone === null)) {
        throw new Error(
          "Synchronization feed item must contain exactly one snapshot or tombstone.",
        );
      }
    }
  }

  private setState(status: SyncStatus, error: string | null): void {
    this.statusValue = status;
    this.errorValue = error;
    this.onState?.({ status, error });
  }
}

export type SyncCoordinatorState = { status: SyncStatus; error: string | null };

export async function databaseNameForAccessToken(token: string): Promise<string> {
  let sub: string;
  if (token.startsWith("test:") || token.startsWith("user:")) {
    sub = token.split(":", 2)[1] || token;
  } else {
    const parts = token.split(".");
    if (parts.length !== 3 || !parts[1])
      throw new Error("Authenticated sync requires a valid Cognito access token.");
    let claims: { sub?: unknown };
    try {
      const base64 = parts[1].replaceAll("-", "+").replaceAll("_", "/");
      claims = JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "="))) as {
        sub?: unknown;
      };
    } catch {
      throw new Error("Authenticated sync could not identify the signed-in account.");
    }
    if (typeof claims.sub !== "string" || !claims.sub)
      throw new Error("Authenticated sync token has no subject claim.");
    sub = claims.sub;
  }
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(sub));
  const suffix = [...new Uint8Array(digest)]
    .slice(0, 12)
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `personal-os-offline-sync-v1-${suffix}`;
}
