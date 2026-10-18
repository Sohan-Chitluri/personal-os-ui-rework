import { describe, expect, it } from "vitest";
import {
  OfflineOperationQueue,
  QueueIdempotencyError,
  QueueStateError,
  QueueStatus,
  QueueStorageError,
  QueueValidationError,
  type QueueEntry,
  type QueueStore,
  type QueueStoreTransaction,
  type SyncOperationEnvelope,
} from "@/lib/offline-queue";

const NOW = new Date("2026-10-19T12:00:00.000Z");

function uuid(number: number): string {
  return `00000000-0000-4000-8000-${number.toString().padStart(12, "0")}`;
}

function operation(overrides: Partial<SyncOperationEnvelope> = {}): SyncOperationEnvelope {
  return {
    protocol_version: 1,
    operation_id: uuid(1),
    idempotency_key: "stable-key-1",
    entity_type: "task",
    entity_id: "task-1",
    operation_type: "update",
    base_revision: 2,
    payload: { title: "Full resulting task document", status: "open" },
    created_at: "2026-10-19T12:00:00Z",
    depends_on: [],
    resolves_conflict_id: null,
    ...overrides,
  };
}

function ack(
  operationId: string,
  status: "applied" | "duplicate" | "conflicted" | "retryable" | "permanent" = "applied",
  overrides: Partial<{
    revision: number | null;
    conflict_id: string | null;
    error_category: "transport" | "temporary_service" | "unsupported" | null;
    message: string | null;
    retry_after_seconds: number | null;
  }> = {},
) {
  return {
    operation_id: operationId,
    status,
    revision: status === "applied" || status === "duplicate" ? 3 : null,
    conflict_id: status === "conflicted" ? uuid(999) : null,
    error_category: status === "retryable" ? "temporary_service" : null,
    message: null,
    retry_after_seconds: null,
    ...overrides,
  };
}

/** Isolated test store with serializable transactions and simulated failures. */
class MemoryQueueStore implements QueueStore {
  private entries = new Map<string, QueueEntry>();
  private transactionTail: Promise<void> = Promise.resolve();
  failNextCommit = false;

  async transaction<T>(
    mode: "readonly" | "readwrite",
    work: (transaction: QueueStoreTransaction) => Promise<T>,
  ): Promise<T> {
    let release!: () => void;
    const previous = this.transactionTail;
    this.transactionTail = new Promise<void>((resolve) => {
      release = resolve;
    });
    await previous;
    const working = new Map(this.entries);
    const adapter: QueueStoreTransaction = {
      getAll: async () => structuredClone([...working.values()]),
      get: async (operationId) => {
        const entry = working.get(operationId);
        return entry ? structuredClone(entry) : undefined;
      },
      add: async (entry) => {
        if (working.has(entry.operation_id)) throw new QueueStorageError("duplicate key");
        working.set(entry.operation_id, structuredClone(entry));
      },
      put: async (entry) => {
        working.set(entry.operation_id, structuredClone(entry));
      },
    };
    try {
      const result = await work(adapter);
      if (mode === "readwrite") {
        if (this.failNextCommit) {
          this.failNextCommit = false;
          throw new QueueStorageError("simulated write failure");
        }
        this.entries = working;
      }
      return result;
    } finally {
      release();
    }
  }

  corrupt(operationId: string, raw: unknown): void {
    this.entries.set(operationId, raw as QueueEntry);
  }
}

function makeQueue(
  store = new MemoryQueueStore(),
  overrides: ConstructorParameters<typeof OfflineOperationQueue>[1] = {},
) {
  return {
    store,
    queue: new OfflineOperationQueue(store, {
      now: () => NOW,
      jitter_fn: (cap) => cap,
      ...overrides,
    }),
  };
}

describe("offline operation queue", () => {
  it("durably enqueues once and rejects identifier reuse with another body", async () => {
    const { queue, store } = makeQueue();
    const op = operation();
    expect((await queue.enqueue(op)).status).toBe("pending");
    expect(await queue.enqueue(op)).toMatchObject({
      operation_id: op.operation_id,
      status: "pending",
    });
    expect(await queue.inspect()).toHaveLength(1);
    await expect(
      queue.enqueue(operation({ payload: { title: "different full document" } })),
    ).rejects.toBeInstanceOf(QueueIdempotencyError);
    expect(await queue.inspect()).toHaveLength(1);
    expect(store).toBeDefined();
  });

  it("validates operation shape, version, and timezone before persistence", async () => {
    const { queue } = makeQueue();
    await expect(queue.enqueue({ ...operation(), protocol_version: 2 })).rejects.toBeInstanceOf(
      QueueValidationError,
    );
    await expect(
      queue.enqueue(
        operation({ operation_type: "delete", payload: { title: "not valid for delete" } }),
      ),
    ).rejects.toBeInstanceOf(QueueValidationError);
    await expect(
      queue.enqueue(operation({ created_at: "2026-10-19T12:00:00" })),
    ).rejects.toBeInstanceOf(QueueValidationError);
    await expect(
      queue.enqueue(
        operation({ payload: { created_at: new Date() } as unknown as Record<string, unknown> }),
      ),
    ).rejects.toBeInstanceOf(QueueValidationError);
    expect(await queue.inspect()).toEqual([]);
  });

  it("normalizes UUIDs and UTC timestamps without losing microsecond precision", async () => {
    const { queue } = makeQueue();
    const op = operation({
      operation_id: "ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF",
      created_at: "2026-10-19T17:30:00.123456+05:30",
    });
    const queued = await queue.enqueue(op);
    expect(queued.operation_id).toBe("abcdefab-cdef-4abc-8abc-abcdefabcdef");
    expect(queued.operation?.created_at).toBe("2026-10-19T12:00:00.123456Z");
  });

  it("recovers expired in-flight operations and retries the same idempotency key", async () => {
    const store = new MemoryQueueStore();
    const first = new OfflineOperationQueue(store, {
      now: () => NOW,
      in_flight_timeout_ms: 0,
      jitter_fn: (cap) => cap,
    });
    const op = operation();
    await first.enqueue(op);
    const firstClaim = (await first.claimBatch({ now: NOW }))[0];
    expect(firstClaim?.status).toBe("in_flight");
    expect(firstClaim?.attempts).toBe(1);

    const restarted = new OfflineOperationQueue(store, {
      now: () => NOW,
      in_flight_timeout_ms: 0,
      jitter_fn: (cap) => cap,
    });
    const recovered = (await restarted.claimBatch({ now: new Date(NOW.getTime() + 1) }))[0];
    expect(recovered).toMatchObject({
      operation_id: op.operation_id,
      idempotency_key: op.idempotency_key,
      status: "in_flight",
      attempts: 2,
    });
    expect(recovered?.operation).toEqual(op);
    expect(recovered?.last_error).toContain("outcome is unknown");
  });

  it("marks a timeout retryable with bounded exponential backoff", async () => {
    const { queue } = makeQueue(undefined, { base_backoff_ms: 2_000, max_backoff_ms: 5_000 });
    const op = operation();
    await queue.enqueue(op);
    const claimed = (await queue.claimBatch({ now: NOW }))[0];
    const retry = await queue.markRetryable(
      {
        operation_id: op.operation_id,
        category: "transport",
        message: "timeout; server outcome unknown",
      },
      NOW,
    );
    expect(claimed?.idempotency_key).toBe(op.idempotency_key);
    expect(retry.status).toBe("retryable");
    expect(retry.next_attempt_at).toBe("2026-10-19T12:00:02.000Z");
    expect(await queue.claimBatch({ now: new Date(NOW.getTime() + 1_999) })).toEqual([]);
    expect(
      (await queue.claimBatch({ now: new Date(NOW.getTime() + 2_000) }))[0]?.operation,
    ).toEqual(op);
  });

  it("records duplicate acknowledgements safely and rejects contradictory terminal acks", async () => {
    const { queue } = makeQueue();
    const op = operation();
    await queue.enqueue(op);
    await queue.claimBatch({ now: NOW });
    const applied = ack(op.operation_id);
    expect((await queue.acknowledge(applied, NOW)).status).toBe("acknowledged");
    expect((await queue.acknowledge(applied, new Date(NOW.getTime() + 1))).status).toBe(
      "acknowledged",
    );
    expect(
      (await queue.acknowledge(ack(op.operation_id, "duplicate", { revision: 3 }), NOW)).status,
    ).toBe("acknowledged");
    await expect(
      queue.acknowledge(ack(op.operation_id, "duplicate", { revision: 4 })),
    ).rejects.toBeInstanceOf(QueueStateError);
    expect(await queue.claimBatch({ now: new Date(NOW.getTime() + 86_400_000) })).toEqual([]);
  });

  it("allows a retryable server acknowledgement to be superseded by the retry result", async () => {
    const { queue } = makeQueue(undefined, { base_backoff_ms: 1_000 });
    const op = operation();
    await queue.enqueue(op);
    await queue.claimBatch({ now: NOW });
    expect(
      (
        await queue.acknowledge(
          ack(op.operation_id, "retryable", { error_category: "temporary_service" }),
          NOW,
        )
      ).status,
    ).toBe("retryable");
    await queue.claimBatch({ now: new Date(NOW.getTime() + 1_000) });
    expect((await queue.acknowledge(ack(op.operation_id), NOW)).status).toBe("acknowledged");
  });

  it("retains permanently failed and retry-exhausted operations for diagnostics", async () => {
    const { queue } = makeQueue(undefined, { max_attempts: 1 });
    const op = operation();
    await queue.enqueue(op);
    await queue.claimBatch({ now: NOW });
    const failed = await queue.markRetryable(
      { operation_id: op.operation_id, category: "transport", message: "timed out" },
      NOW,
    );
    expect(failed.status).toBe("failed");
    expect(failed.operation).toEqual(op);
    expect(failed.last_error).toContain("exhausted");
    expect((await queue.diagnostics()).by_status.failed).toBe(1);
    expect(await queue.claimBatch({ now: new Date(NOW.getTime() + 86_400_000) })).toEqual([]);
  });

  it("orders dependent operations and fails them when a prerequisite fails", async () => {
    const store = new MemoryQueueStore();
    const { queue } = makeQueue(store);
    const parent = operation();
    const child = operation({
      operation_id: uuid(2),
      idempotency_key: "child-key",
      entity_id: "task-2",
      depends_on: [parent.operation_id],
    });
    await queue.enqueue(parent);
    await queue.enqueue(child);
    expect((await queue.claimBatch({ now: NOW })).map((entry) => entry.operation_id)).toEqual([
      parent.operation_id,
    ]);
    await queue.acknowledge(ack(parent.operation_id), NOW);
    expect((await queue.claimBatch({ now: NOW })).map((entry) => entry.operation_id)).toEqual([
      child.operation_id,
    ]);

    const failedStore = new MemoryQueueStore();
    const second = new OfflineOperationQueue(failedStore, { now: () => NOW });
    await second.enqueue(parent);
    await second.enqueue(child);
    await second.markFailed(
      { operation_id: parent.operation_id, category: "unsupported", message: "unsupported" },
      NOW,
    );
    expect(await second.claimBatch({ now: NOW })).toEqual([]);
    expect(
      (await second.inspect()).find((entry) => entry.operation_id === child.operation_id),
    ).toMatchObject({
      status: "failed",
      error_category: "dependency_failed",
    });
  });

  it("bounds batches and diagnoses dependencies that have not arrived", async () => {
    const { queue } = makeQueue(undefined, { max_batch_size: 1 });
    const parentId = uuid(9);
    await queue.enqueue(
      operation({
        operation_id: uuid(2),
        idempotency_key: "child-only-key",
        depends_on: [parentId],
      }),
    );
    await queue.enqueue(
      operation({
        operation_id: uuid(3),
        idempotency_key: "second-key",
        entity_id: "task-3",
      }),
    );
    expect(await queue.claimBatch({ now: NOW, limit: 50 })).toHaveLength(1);
    expect((await queue.inspect())[0]).toMatchObject({
      error_category: "dependency_pending",
      last_error: expect.stringContaining("missing dependency"),
    });
    await expect(queue.claimBatch({ now: NOW, limit: 0 })).rejects.toThrow(RangeError);
  });

  it("preserves malformed persisted rows and marks them failed", async () => {
    const store = new MemoryQueueStore();
    const { queue } = makeQueue(store);
    store.corrupt(uuid(1), {
      operation_id: uuid(1),
      idempotency_key: "broken",
      operation: { no: "valid envelope" },
      status: "pending",
      attempts: 0,
      next_attempt_at: null,
      in_flight_until: null,
      acknowledgement: null,
      error_category: null,
      last_error: null,
      enqueued_at: NOW.toISOString(),
      updated_at: NOW.toISOString(),
    });
    expect((await queue.inspect())[0]?.malformed).toBe(true);
    expect(await queue.claimBatch({ now: NOW })).toEqual([]);
    expect((await queue.inspect())[0]).toMatchObject({
      status: "failed",
      error_category: "malformed",
      malformed: true,
    });
    expect((await queue.diagnostics()).malformed).toBe(1);
  });

  it("serializes concurrent enqueues, keeping one durable operation", async () => {
    const { queue } = makeQueue();
    const op = operation();
    const entries = await Promise.all(Array.from({ length: 12 }, () => queue.enqueue(op)));
    expect(new Set(entries.map((entry) => entry.operation_id))).toEqual(new Set([op.operation_id]));
    expect(await queue.inspect()).toHaveLength(1);
  });

  it("does not report an enqueue success if durable storage fails", async () => {
    const store = new MemoryQueueStore();
    const { queue } = makeQueue(store);
    store.failNextCommit = true;
    await expect(queue.enqueue(operation())).rejects.toBeInstanceOf(QueueStorageError);
    expect(await queue.inspect()).toEqual([]);
  });

  it("cancels only unsent operations without unresolved dependents", async () => {
    const { queue } = makeQueue();
    const parent = operation();
    const child = operation({
      operation_id: uuid(2),
      idempotency_key: "child-key",
      entity_id: "task-2",
      depends_on: [parent.operation_id],
    });
    await queue.enqueue(parent);
    await queue.enqueue(child);
    await expect(queue.cancel(parent.operation_id, NOW)).rejects.toBeInstanceOf(QueueStateError);
    expect((await queue.cancel(child.operation_id, NOW)).status).toBe("cancelled");
    expect((await queue.cancel(parent.operation_id, NOW)).status).toBe("cancelled");

    const sent = operation({ operation_id: uuid(3), idempotency_key: "sent-key" });
    await queue.enqueue(sent);
    await queue.claimBatch({ now: NOW });
    await expect(queue.cancel(sent.operation_id, NOW)).rejects.toBeInstanceOf(QueueStateError);
  });
});
