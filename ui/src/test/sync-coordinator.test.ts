import { describe, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";

import {
  OfflineOperationQueue,
  IndexedDbOfflineOperationQueue,
  type QueueEntry,
  type QueueStore,
  type QueueStoreTransaction,
  type SyncOperationEnvelope,
} from "@/lib/offline-queue";
import {
  SyncApiClient,
  SyncApiError,
  type SyncApi,
  type SyncChangeItem,
  type SyncChangePage,
  type SyncConflict,
} from "@/lib/sync-api";
import {
  IndexedDbSyncPersistence,
  SyncCoordinator,
  type SyncPersistence,
} from "@/lib/sync-coordinator";
import type { OperationAcknowledgement } from "@/lib/offline-queue";

const NOW = new Date("2026-10-19T12:00:00.000Z");
const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

function op(n: number, depends: string[] = []): SyncOperationEnvelope {
  return {
    protocol_version: 1,
    operation_id: id(n),
    idempotency_key: `idem-${n}`,
    entity_type: "task",
    entity_id: `task-${n}`,
    operation_type: "update",
    base_revision: 1,
    payload: { title: `Task ${n}` },
    created_at: NOW.toISOString(),
    depends_on: depends,
    resolves_conflict_id: null,
  };
}

function ack(
  operationId: string,
  status: "applied" | "retryable" | "permanent" | "conflicted" = "applied",
) {
  return {
    operation_id: operationId,
    status,
    revision: status === "applied" ? 2 : null,
    conflict_id: status === "conflicted" ? id(900) : null,
    error_category:
      status === "retryable"
        ? "temporary_service"
        : status === "permanent"
          ? "invalid_operation"
          : null,
    message: status === "retryable" ? "try later" : status === "permanent" ? "bad operation" : null,
    retry_after_seconds: null,
  } as OperationAcknowledgement;
}

class MemoryQueueStore implements QueueStore {
  private entries = new Map<string, QueueEntry>();
  private tail: Promise<void> = Promise.resolve();

  async transaction<T>(
    mode: "readonly" | "readwrite",
    work: (tx: QueueStoreTransaction) => Promise<T>,
  ): Promise<T> {
    let release!: () => void;
    const previous = this.tail;
    this.tail = new Promise<void>((resolve) => {
      release = resolve;
    });
    await previous;
    const working = new Map(this.entries);
    const tx: QueueStoreTransaction = {
      getAll: async () => structuredClone([...working.values()]),
      get: async (key) => (working.has(key) ? structuredClone(working.get(key)) : undefined),
      add: async (entry) => {
        working.set(entry.operation_id, structuredClone(entry));
      },
      put: async (entry) => {
        working.set(entry.operation_id, structuredClone(entry));
      },
    };
    try {
      const result = await work(tx);
      if (mode === "readwrite") this.entries = working;
      return result;
    } finally {
      release();
    }
  }
}

class MemorySyncPersistence implements SyncPersistence {
  cursor: string | null = null;
  changes = new Map<string, SyncChangeItem>();
  failCommit = false;
  async getCursor() {
    return this.cursor;
  }
  async clearCursor() {
    this.cursor = null;
  }
  async commitPage(items: SyncChangeItem[], nextCursor: string) {
    if (this.failCommit) {
      this.failCommit = false;
      throw new Error("simulated storage failure");
    }
    for (const item of items) this.changes.set(item.event_id, structuredClone(item));
    this.cursor = nextCursor;
  }
  async listAppliedChanges() {
    return [...this.changes.values()].sort((a, b) => a.sequence - b.sequence);
  }
}

function change(sequence: number): SyncChangeItem {
  return {
    event_id: id(100 + sequence),
    sequence,
    entity_type: "task",
    entity_id: `task-${sequence}`,
    revision: sequence,
    snapshot: {
      entity_type: "task",
      entity_id: `task-${sequence}`,
      revision: sequence,
      data: { title: `Task ${sequence}` },
      deleted: false,
    },
    tombstone: null,
  };
}

function page(items: SyncChangeItem[], cursor: string, hasMore = false): SyncChangePage {
  return {
    protocol_version: 1,
    items,
    next_cursor: cursor,
    cursor_expires_at: "2026-10-20T12:00:00Z",
    has_more: hasMore,
  };
}

function setup(api: SyncApi) {
  const store = new MemoryQueueStore();
  const queue = new OfflineOperationQueue(store, {
    now: () => NOW,
    jitter_fn: (cap) => cap,
    max_batch_size: 100,
  });
  const persistence = new MemorySyncPersistence();
  const coordinator = new SyncCoordinator(queue, persistence, api);
  return { queue, persistence, coordinator };
}

describe("authenticated browser sync client", () => {
  it("persists queued operations, feed records, and cursor across IndexedDB adapter reloads", async () => {
    const factory = new IDBFactory();
    const databaseName = "sync-reload-test";
    const firstQueue = new IndexedDbOfflineOperationQueue({
      database_name: databaseName,
      indexed_db_factory: factory,
    });
    await firstQueue.enqueue(op(1));
    firstQueue.close();
    const reopenedQueue = new IndexedDbOfflineOperationQueue({
      database_name: databaseName,
      indexed_db_factory: factory,
    });
    expect(await reopenedQueue.inspect()).toMatchObject([
      { operation_id: id(1), idempotency_key: "idem-1", status: "pending" },
    ]);
    const firstPersistence = new IndexedDbSyncPersistence(databaseName, factory);
    await firstPersistence.commitPage([change(1)], "durable-cursor");
    firstPersistence.close();
    const reopenedPersistence = new IndexedDbSyncPersistence(databaseName, factory);
    expect(await reopenedPersistence.getCursor()).toBe("durable-cursor");
    expect(await reopenedPersistence.listAppliedChanges()).toMatchObject([
      { event_id: id(101), sequence: 1 },
    ]);
    reopenedQueue.close();
    reopenedPersistence.close();
    await new Promise<void>((resolve, reject) => {
      const request = factory.deleteDatabase(databaseName);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  });

  it("uses bearer authentication and the version 1 upload/change/conflict contract", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ protocol_version: 1, acknowledgements: [] }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify(page([], "opaque")), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ conflict_id: id(7) }), { status: 200 }));
    const client = new SyncApiClient("/api/v1", () => "jwt", fetchMock);
    await client.uploadOperations([op(1)]);
    await client.getChanges("cursor value", 25);
    await client.getConflict(id(7));
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/sync/operations",
      "/api/v1/sync/changes?limit=25&cursor=cursor+value",
      `/api/v1/sync/conflicts/${id(7)}`,
    ]);
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe(
      "Bearer jwt",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      protocol_version: 1,
      operations: [op(1)],
    });
  });

  it("rejects unsigned requests and reports unavailable backend as retryable transport failure", async () => {
    await expect(new SyncApiClient("/api/v1", () => null).getChanges(null)).rejects.toMatchObject({
      status: 401,
    });
    const unavailable = new SyncApiClient(
      "/api/v1",
      () => "jwt",
      vi.fn().mockRejectedValue(new TypeError("offline")),
    );
    await expect(unavailable.getChanges(null)).rejects.toMatchObject({
      status: 0,
      code: "transport",
    });
  });

  it("flushes dependency-ordered batches of at most 100 and preserves partial acknowledgements", async () => {
    const api: SyncApi = {
      uploadOperations: vi.fn(async (batch: SyncOperationEnvelope[]) => ({
        protocol_version: 1 as const,
        acknowledgements: batch.map((item) =>
          ack(item.operation_id, item.operation_id === id(2) ? "retryable" : "applied"),
        ),
      })),
      getChanges: vi.fn(async () => page([], "c1")),
      getConflict: vi.fn(),
    };
    const { queue, coordinator } = setup(api);
    await queue.enqueue(op(1));
    await queue.enqueue(op(2, [id(1)]));
    await coordinator.syncNow();
    expect(api.uploadOperations).toHaveBeenCalledTimes(2);
    expect(api.uploadOperations).toHaveBeenNthCalledWith(1, [
      expect.objectContaining({ operation_id: id(1), idempotency_key: "idem-1" }),
    ]);
    expect(api.uploadOperations).toHaveBeenNthCalledWith(2, [
      expect.objectContaining({
        operation_id: id(2),
        idempotency_key: "idem-2",
        depends_on: [id(1)],
      }),
    ]);
    expect(await queue.inspect()).toMatchObject([
      { operation_id: id(1), status: "acknowledged", idempotency_key: "idem-1" },
      { operation_id: id(2), status: "retryable", idempotency_key: "idem-2" },
    ]);
    const sameKeyRetry = await queue.inspect();
    expect(sameKeyRetry[1]?.operation?.idempotency_key).toBe("idem-2");
  });

  it("splits a large queue into server-allowed batches of 100", async () => {
    const api: SyncApi = {
      uploadOperations: vi.fn(async (batch: SyncOperationEnvelope[]) => ({
        protocol_version: 1 as const,
        acknowledgements: batch.map((item) => ack(item.operation_id)),
      })),
      getChanges: vi.fn(async () => page([], "c1")),
      getConflict: vi.fn(),
    };
    const { queue, coordinator } = setup(api);
    for (let n = 1; n <= 101; n += 1) await queue.enqueue(op(n));
    await coordinator.syncNow();
    expect(api.uploadOperations).toHaveBeenCalledTimes(2);
    expect(
      (api.uploadOperations as ReturnType<typeof vi.fn>).mock.calls.map((call) => call[0].length),
    ).toEqual([100, 1]);
  });

  it("marks permanent errors failed and retries transient request failures without changing IDs", async () => {
    const api: SyncApi = {
      uploadOperations: vi
        .fn()
        .mockResolvedValueOnce({ protocol_version: 1, acknowledgements: [ack(id(1), "permanent")] })
        .mockRejectedValueOnce(new SyncApiError(503, "unavailable")),
      getChanges: vi.fn(async () => page([], "c1")),
      getConflict: vi.fn(),
    };
    const { queue, coordinator } = setup(api);
    await queue.enqueue(op(1));
    await queue.enqueue(op(2));
    await coordinator.syncNow();
    expect(await queue.inspect()).toMatchObject([
      { operation_id: id(1), status: "failed", error_category: "invalid_operation" },
      { operation_id: id(2), status: "retryable", idempotency_key: "idem-2" },
    ]);
  });

  it("commits feed records and cursor together, and safely replays after a failed cursor commit", async () => {
    const persistence = new MemorySyncPersistence();
    persistence.failCommit = true;
    const api: SyncApi = {
      uploadOperations: vi.fn(),
      getChanges: vi.fn(async () => page([change(1)], "next")),
      getConflict: vi.fn(),
    };
    const { coordinator } = setup(api);
    const onApplied = vi.fn();
    const runner = new SyncCoordinator(coordinator.queue, persistence, api, undefined, 100, onApplied);
    await expect(runner.syncNow()).rejects.toThrow("simulated storage failure");
    expect(persistence.cursor).toBeNull();
    expect(persistence.changes.size).toBe(0);
    expect(onApplied).not.toHaveBeenCalled();
    await runner.syncNow();
    expect(persistence.cursor).toBe("next");
    expect(persistence.changes.size).toBe(1);
    expect(onApplied).toHaveBeenCalledWith([change(1)]);
    expect(api.getChanges).toHaveBeenCalledTimes(2);
  });

  it("pulls pages in order, stores each cursor after its page, and deduplicates expired-cursor replay", async () => {
    const persistence = new MemorySyncPersistence();
    persistence.cursor = "expired";
    await persistence.commitPage([change(1)], "expired");
    const api: SyncApi = {
      uploadOperations: vi.fn(),
      getChanges: vi
        .fn()
        .mockRejectedValueOnce(new SyncApiError(410, "expired", "cursor_expired"))
        .mockResolvedValueOnce(page([change(1)], "page-1", true))
        .mockResolvedValueOnce(page([change(2)], "page-2")),
      getConflict: vi.fn(),
    };
    const { queue } = setup(api);
    const coordinator = new SyncCoordinator(queue, persistence, api);
    await coordinator.syncNow();
    expect(api.getChanges).toHaveBeenNthCalledWith(1, "expired", 100);
    expect(api.getChanges).toHaveBeenNthCalledWith(2, null, 100);
    expect(api.getChanges).toHaveBeenNthCalledWith(3, "page-1", 100);
    expect(persistence.cursor).toBe("page-2");
    expect(await persistence.listAppliedChanges()).toHaveLength(2);
  });

  it("retains queued operations after authentication failure and reports sync failure", async () => {
    const api: SyncApi = {
      uploadOperations: vi
        .fn()
        .mockRejectedValue(new SyncApiError(401, "expired access token", "unauthorized")),
      getChanges: vi.fn(),
      getConflict: vi.fn(),
    };
    const { queue, coordinator } = setup(api);
    await queue.enqueue(op(1));
    await expect(coordinator.syncNow()).rejects.toMatchObject({ status: 401 });
    expect(coordinator.status).toBe("failed");
    expect(await queue.inspect()).toMatchObject([
      { status: "retryable", idempotency_key: "idem-1", operation: { operation_id: id(1) } },
    ]);
  });

  it("preserves conflict payload and exposes the server conflict record for review", async () => {
    const conflict: SyncConflict = {
      conflict_id: id(900),
      operation: op(1),
      base_revision: 1,
      base_snapshot: null,
      local_payload: { title: "Local" },
      remote_snapshot: {
        entity_type: "task",
        entity_id: "task-1",
        revision: 3,
        data: { title: "Remote" },
        deleted: false,
      },
      reason: "stale_revision",
      status: "unresolved",
      created_at: NOW.toISOString(),
      resolution_operation_id: null,
    };
    const api: SyncApi = {
      uploadOperations: vi.fn(async () => ({
        protocol_version: 1 as const,
        acknowledgements: [ack(id(1), "conflicted")],
      })),
      getChanges: vi.fn(async () => page([], "c1")),
      getConflict: vi.fn(async () => conflict),
    };
    const { queue, coordinator } = setup(api);
    await queue.enqueue(op(1));
    await coordinator.syncNow();
    expect((await queue.inspect())[0]).toMatchObject({
      status: "conflicted",
      operation: { payload: { title: "Task 1" } },
      acknowledgement: { conflict_id: id(900) },
    });
    expect(await coordinator.inspectConflict(id(900))).toEqual(conflict);
    expect(coordinator.status).toBe("conflict");
  });
});
