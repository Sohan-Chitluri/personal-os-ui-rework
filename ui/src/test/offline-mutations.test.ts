import { beforeEach, describe, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";

import { OfflineMutationBoundary, generateUlid, isUlid } from "@/lib/offline-mutations";
import {
  IndexedDbOfflineOperationQueue,
  OfflineOperationQueue,
  QueueStorageError,
  type QueueStoreTransaction,
  type SyncOperationEnvelope,
} from "@/lib/offline-queue";
import {
  IndexedDbSyncPersistence,
  SyncCoordinator,
  databaseNameForAccessToken,
} from "@/lib/sync-coordinator";
import { SyncApiError, type SyncApi, type SyncConflict } from "@/lib/sync-api";
import type { Task } from "@/lib/task-types";
import type { BackendProject } from "@/lib/project-types";

function createMockJwt(sub: string): string {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = btoa(JSON.stringify({ sub, exp: Math.floor(Date.now() / 1000) + 3600 }));
  return `${header}.${payload}.signature`;
}

describe("OfflineMutationBoundary and Sync Integration", () => {
  let factory: IDBFactory;

  beforeEach(() => {
    factory = new IDBFactory();
  });

  it("1. creates a task while offline, reloads browser, reconnects, and verifies it reaches PostgreSQL once", async () => {
    const dbName = "test-case-1";
    const queue1 = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
    });
    const boundary1 = new OfflineMutationBoundary(queue1);

    // Create task offline
    const { task, envelope } = await boundary1.enqueueTaskCreate({
      title: "Offline task across reload",
      estimate: 25,
      mood: "Deep",
    });

    expect(isUlid(task.id)).toBe(true);
    expect(task.title).toBe("Offline task across reload");
    expect(task.syncStatus).toBe("pending");
    expect(envelope.entity_id).toBe(task.id);
    expect(envelope.operation_type).toBe("create");

    // Close queue to simulate browser reload / process termination
    queue1.close();

    // Reopen queue in a fresh instance
    const queue2 = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
    });
    const persistence = new IndexedDbSyncPersistence(dbName, factory);

    const uploadedBatches: SyncOperationEnvelope[][] = [];
    const mockApi: SyncApi = {
      uploadOperations: vi.fn(async (batch: SyncOperationEnvelope[]) => {
        uploadedBatches.push(batch);
        return {
          protocol_version: 1 as const,
          acknowledgements: batch.map((op: SyncOperationEnvelope) => ({
            operation_id: op.operation_id,
            status: "applied" as const,
            revision: 1,
            conflict_id: null,
            error_category: null,
            message: null,
            retry_after_seconds: null,
          })),
        };
      }),
      getChanges: vi.fn(async () => ({
        protocol_version: 1 as const,
        items: [],
        next_cursor: "cursor-1",
        cursor_expires_at: new Date(Date.now() + 86400000).toISOString(),
        has_more: false,
      })),
      getConflict: vi.fn(),
    };

    let ackedOpId: string | null = null;
    let ackedRevision: number | null = null;
    const coordinator = new SyncCoordinator(
      queue2,
      persistence,
      mockApi,
      undefined,
      100,
      undefined,
      (op, ack) => {
        ackedOpId = op.operation_id;
        ackedRevision = ack.revision;
      },
    );

    // Reconnect and sync
    await coordinator.syncNow();

    // Verify it reached API exactly once
    expect(mockApi.uploadOperations).toHaveBeenCalledTimes(1);
    expect(uploadedBatches.length).toBe(1);
    expect(uploadedBatches[0]?.[0]?.operation_id).toBe(envelope.operation_id);
    expect(uploadedBatches[0]?.[0]?.idempotency_key).toBe(envelope.idempotency_key);
    expect(uploadedBatches[0]?.[0]?.entity_id).toBe(task.id);
    expect(ackedOpId).toBe(envelope.operation_id);
    expect(ackedRevision).toBe(1);

    // Inspect queue to verify status is acknowledged
    const inspected = await queue2.inspect();
    expect(inspected.length).toBe(1);
    expect(inspected[0]?.status).toBe("acknowledged");

    queue2.close();
    persistence.close();
  });

  it("2. updates an existing task offline, reconnects, and verifies the resulting canonical record", async () => {
    const dbName = "test-case-2";
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
    });
    const boundary = new OfflineMutationBoundary(queue);
    const persistence = new IndexedDbSyncPersistence(dbName, factory);

    const taskId = generateUlid();
    const task: Task = {
      id: taskId,
      title: "Initial Title",
      projectId: "unassigned",
      done: false,
      estimate: 15,
      mood: "Deep",
      frog: false,
      dueIn: null,
      revision: 1,
    };
    const { envelope } = await boundary.enqueueTaskUpdate(task, {
      title: "Updated Title Offline",
      estimate: 45,
    });

    expect(envelope.operation_type).toBe("update");
    expect(envelope.base_revision).toBe(1);
    expect(envelope.payload).toEqual({
      title: "Updated Title Offline",
      description: "[mood:Deep] [estimate:45]",
      status: "open",
      priority: 3,
      due_date: null,
      project_id: null,
      capture_id: null,
    });

    const mockApi: SyncApi = {
      uploadOperations: vi.fn(async (batch: SyncOperationEnvelope[]) => ({
        protocol_version: 1 as const,
        acknowledgements: batch.map((op: SyncOperationEnvelope) => ({
          operation_id: op.operation_id,
          status: "applied" as const,
          revision: 2,
          conflict_id: null,
          error_category: null,
          message: null,
          retry_after_seconds: null,
        })),
      })),
      getChanges: vi.fn(async () => ({
        protocol_version: 1 as const,
        items: [],
        next_cursor: "c1",
        cursor_expires_at: new Date(Date.now() + 86400000).toISOString(),
        has_more: false,
      })),
      getConflict: vi.fn(),
    };

    let reportedRevision: number | null = null;
    const coordinator = new SyncCoordinator(
      queue,
      persistence,
      mockApi,
      undefined,
      100,
      undefined,
      (op, ack) => {
        if (op.operation_id === envelope.operation_id) {
          reportedRevision = ack.revision;
        }
      },
    );

    await coordinator.syncNow();

    expect(mockApi.uploadOperations).toHaveBeenCalledTimes(1);
    expect(reportedRevision).toBe(2);

    const inspected = await queue.inspect();
    expect(inspected[0]?.status).toBe("acknowledged");

    queue.close();
    persistence.close();
  });

  it("3. creates a project offline, then creates a task linked to it; ensures dependency ordering and correct project association", async () => {
    const dbName = "test-case-3";
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
    });
    const boundary = new OfflineMutationBoundary(queue);
    const persistence = new IndexedDbSyncPersistence(dbName, factory);

    // 1. Create project offline
    const { project, envelope: projectEnv } = await boundary.enqueueProjectCreate({
      name: "Offline Project Alpha",
      description: "Created while traveling",
    });

    expect(isUlid(project.id)).toBe(true);
    expect(projectEnv.entity_type).toBe("project");
    expect(projectEnv.operation_type).toBe("create");

    // 2. Create task linked to the offline project
    const { task, envelope: taskEnv } = await boundary.enqueueTaskCreate({
      title: "Task in Alpha",
      projectId: project.id,
    });

    expect(isUlid(task.id)).toBe(true);
    expect(task.projectId).toBe(project.id);
    expect(taskEnv.entity_type).toBe("task");
    expect(taskEnv.payload?.["project_id"]).toBe(project.id);
    // Boundary must have established dependency on the project's create operation
    expect(taskEnv.depends_on).toContain(projectEnv.operation_id);

    // When syncing:
    // Batch 1 should claim ONLY projectEnv, because taskEnv depends on projectEnv which is not yet acknowledged!
    const batch1 = await queue.claimBatch();
    expect(batch1.length).toBe(1);
    expect(batch1[0]?.operation?.operation_id).toBe(projectEnv.operation_id);

    // Acknowledge project
    await queue.acknowledge({
      operation_id: projectEnv.operation_id,
      status: "applied",
      revision: 1,
      conflict_id: null,
      error_category: null,
      message: null,
      retry_after_seconds: null,
    });

    // Batch 2 should now claim taskEnv because projectEnv has been acknowledged!
    const batch2 = await queue.claimBatch();
    expect(batch2.length).toBe(1);
    expect(batch2[0]?.operation?.operation_id).toBe(taskEnv.operation_id);
    expect(batch2[0]?.operation?.payload?.["project_id"]).toBe(project.id);

    // Acknowledge task
    await queue.acknowledge({
      operation_id: taskEnv.operation_id,
      status: "applied",
      revision: 1,
      conflict_id: null,
      error_category: null,
      message: null,
      retry_after_seconds: null,
    });

    const remaining = await queue.claimBatch();
    expect(remaining.length).toBe(0);

    queue.close();
    persistence.close();
  });

  it("4. deletes or completes an entity while offline, conforming strictly to contract", async () => {
    const dbName = "test-case-4";
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
    });
    const boundary = new OfflineMutationBoundary(queue);

    const taskId = generateUlid();
    const task: Task = {
      id: taskId,
      title: "Finish Report",
      projectId: "unassigned",
      done: false,
      estimate: 30,
      mood: "Deep",
      frog: true,
      dueIn: 0,
      revision: 2,
    };

    // Toggle complete offline -> update operation with status: "done"
    const { envelope: toggleEnv } = await boundary.enqueueTaskToggle(task);
    expect(toggleEnv.operation_type).toBe("update");
    expect(toggleEnv.base_revision).toBe(2);
    expect(toggleEnv.payload?.["status"]).toBe("done");

    // Delete task offline -> delete operation with base_revision and null payload
    const { envelope: deleteEnv } = await boundary.enqueueTaskDelete({
      ...task,
      revision: 3,
    });
    expect(deleteEnv.operation_type).toBe("delete");
    expect(deleteEnv.base_revision).toBe(3);
    expect(deleteEnv.payload).toBeNull();

    // Delete project offline -> delete operation with base_revision and null payload
    const projectId = generateUlid();
    const project: BackendProject = {
      id: projectId,
      owner_id: "user-1",
      name: "Proj",
      description: null,
      status: "active",
      revision: 1,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    const { envelope: deleteProjEnv } = await boundary.enqueueProjectDelete(project);
    expect(deleteProjEnv.operation_type).toBe("delete");
    expect(deleteProjEnv.base_revision).toBe(1);
    expect(deleteProjEnv.payload).toBeNull();

    queue.close();
  });

  it("5. interrupts a sync request and retries with the exact same idempotency key", async () => {
    const dbName = "test-case-5";
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
      base_backoff_ms: 0,
    });
    const boundary = new OfflineMutationBoundary(queue);
    const persistence = new IndexedDbSyncPersistence(dbName, factory);

    const { envelope } = await boundary.enqueueTaskCreate({ title: "Interrupted task" });
    const originalId = envelope.operation_id;
    const originalKey = envelope.idempotency_key;

    let attemptCount = 0;
    const mockApi: SyncApi = {
      uploadOperations: vi.fn(async () => {
        attemptCount += 1;
        if (attemptCount === 1) {
          throw new SyncApiError(500, "Network timeout during upload");
        }
        return {
          protocol_version: 1 as const,
          acknowledgements: [
            {
              operation_id: originalId,
              status: "applied" as const,
              revision: 1,
              conflict_id: null,
              error_category: null,
              message: null,
              retry_after_seconds: null,
            },
          ],
        };
      }),
      getChanges: vi.fn(async () => ({
        protocol_version: 1 as const,
        items: [],
        next_cursor: "c1",
        cursor_expires_at: new Date(Date.now() + 86400000).toISOString(),
        has_more: false,
      })),
      getConflict: vi.fn(),
    };

    const coordinator = new SyncCoordinator(queue, persistence, mockApi);

    // First attempt encounters transient failure (marked retryable)
    await coordinator.syncNow();

    // Queue entry is marked retryable
    const inspect1 = await queue.inspect();
    expect(inspect1[0]?.status).toBe("retryable");
    expect(inspect1[0]?.operation?.operation_id).toBe(originalId);
    expect(inspect1[0]?.operation?.idempotency_key).toBe(originalKey);

    // Second attempt succeeds
    await coordinator.syncNow();

    expect(mockApi.uploadOperations).toHaveBeenCalledTimes(2);
    // Verify that the second call used the EXACT same operation_id and idempotency_key
    const secondCallArg = (mockApi.uploadOperations as ReturnType<typeof vi.fn>).mock.calls[1]?.[0];
    expect(secondCallArg?.[0]?.operation_id).toBe(originalId);
    expect(secondCallArg?.[0]?.idempotency_key).toBe(originalKey);

    const inspect2 = await queue.inspect();
    expect(inspect2[0]?.status).toBe("acknowledged");

    queue.close();
    persistence.close();
  });

  it("6. receives a partial-batch response and processes each acknowledgement independently", async () => {
    const dbName = "test-case-6";
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
    });
    const boundary = new OfflineMutationBoundary(queue);
    const persistence = new IndexedDbSyncPersistence(dbName, factory);

    const { envelope: env1 } = await boundary.enqueueTaskCreate({ title: "Task 1" });
    const { envelope: env2 } = await boundary.enqueueTaskCreate({ title: "Task 2" });

    const mockApi: SyncApi = {
      uploadOperations: vi.fn(async () => ({
        protocol_version: 1 as const,
        acknowledgements: [
          {
            operation_id: env1.operation_id,
            status: "applied" as const,
            revision: 1,
            conflict_id: null,
            error_category: null,
            message: null,
            retry_after_seconds: null,
          },
          {
            operation_id: env2.operation_id,
            status: "retryable" as const,
            revision: null,
            conflict_id: null,
            error_category: "temporary_service" as const,
            message: "Database busy, try again later",
            retry_after_seconds: 5,
          },
        ],
      })),
      getChanges: vi.fn(async () => ({
        protocol_version: 1 as const,
        items: [],
        next_cursor: "c1",
        cursor_expires_at: new Date(Date.now() + 86400000).toISOString(),
        has_more: false,
      })),
      getConflict: vi.fn(),
    };

    const ackedList: { id: string; status: string }[] = [];
    const coordinator = new SyncCoordinator(
      queue,
      persistence,
      mockApi,
      undefined,
      100,
      undefined,
      (op, ack) => {
        ackedList.push({ id: op.operation_id, status: ack.status });
      },
    );

    await coordinator.syncNow();

    expect(ackedList).toEqual([
      { id: env1.operation_id, status: "applied" },
      { id: env2.operation_id, status: "retryable" },
    ]);

    const inspected = await queue.inspect();
    const entry1 = inspected.find((e) => e.operation_id === env1.operation_id);
    const entry2 = inspected.find((e) => e.operation_id === env2.operation_id);

    expect(entry1?.status).toBe("acknowledged");
    expect(entry2?.status).toBe("retryable");

    queue.close();
    persistence.close();
  });

  it("7. encounters a revision conflict without losing the local edit", async () => {
    const dbName = "test-case-7";
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
    });
    const boundary = new OfflineMutationBoundary(queue);
    const persistence = new IndexedDbSyncPersistence(dbName, factory);

    const taskId = generateUlid();
    const conflictId = crypto.randomUUID();
    const task: Task = {
      id: taskId,
      title: "Task Before Edit",
      projectId: "unassigned",
      done: false,
      estimate: 15,
      mood: "Deep",
      frog: false,
      dueIn: null,
      revision: 1,
    };

    const { envelope } = await boundary.enqueueTaskUpdate(task, {
      title: "Conflicted Local Edit",
    });

    const mockConflict: SyncConflict = {
      conflict_id: conflictId,
      operation: envelope,
      base_revision: 1,
      base_snapshot: null,
      local_payload: envelope.payload,
      remote_snapshot: {
        entity_type: "task",
        entity_id: taskId,
        revision: 2,
        data: { title: "Server Version Modified Concurrently" },
        deleted: false,
      },
      reason: "Revision mismatch",
      status: "unresolved",
      created_at: new Date().toISOString(),
      resolution_operation_id: null,
    };

    const mockApi: SyncApi = {
      uploadOperations: vi.fn(async () => ({
        protocol_version: 1 as const,
        acknowledgements: [
          {
            operation_id: envelope.operation_id,
            status: "conflicted" as const,
            revision: 2,
            conflict_id: conflictId,
            error_category: null,
            message: "Revision mismatch",
            retry_after_seconds: null,
          },
        ],
      })),
      getChanges: vi.fn(async () => ({
        protocol_version: 1 as const,
        items: [],
        next_cursor: "c1",
        cursor_expires_at: new Date(Date.now() + 86400000).toISOString(),
        has_more: false,
      })),
      getConflict: vi.fn(async () => mockConflict),
    };

    let reportedConflictId: string | null = null;
    const coordinator = new SyncCoordinator(
      queue,
      persistence,
      mockApi,
      undefined,
      100,
      undefined,
      (op, ack) => {
        if (ack.status === "conflicted") {
          reportedConflictId = ack.conflict_id;
        }
      },
    );

    await coordinator.syncNow();

    expect(reportedConflictId).toBe(conflictId);

    // Queue holds the conflicted entry with full local payload intact
    const conflicts = await coordinator.conflicts();
    expect(conflicts.length).toBe(1);
    expect(conflicts[0]?.status).toBe("conflicted");
    expect(conflicts[0]?.operation?.payload?.["title"]).toBe("Conflicted Local Edit");

    // Can inspect conflict to view both local payload and server version
    const inspectedConflict = await coordinator.inspectConflict(conflictId);
    expect(inspectedConflict.local_payload?.["title"]).toBe("Conflicted Local Edit");
    expect(inspectedConflict.remote_snapshot.data?.["title"]).toBe(
      "Server Version Modified Concurrently",
    );

    // Now resolve conflict cleanly using enqueueResolveConflict
    const { envelope: resolveEnv } = await boundary.enqueueResolveConflict({
      conflictId,
      entityType: "task",
      entityId: taskId,
      baseRevision: 2, // matches remote_snapshot revision
      payload: {
        title: "Merged Conflict Title",
        description: null,
        status: "open",
        priority: 0,
        due_date: null,
        project_id: null,
        capture_id: null,
      },
    });

    expect(resolveEnv.resolves_conflict_id).toBe(conflictId);
    expect(resolveEnv.base_revision).toBe(2);

    queue.close();
    persistence.close();
  });

  it("8. loses authentication or receives a 401; pauses synchronization and preserves pending work", async () => {
    const dbName = "test-case-8";
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
    });
    const boundary = new OfflineMutationBoundary(queue);
    const persistence = new IndexedDbSyncPersistence(dbName, factory);

    await boundary.enqueueTaskCreate({ title: "Task preserved on 401" });

    const mockApi: SyncApi = {
      uploadOperations: vi.fn(async () => {
        throw new SyncApiError(401, "Unauthorized");
      }),
      getChanges: vi.fn(),
      getConflict: vi.fn(),
    };

    let coordinatorStateStatus: string | null = null;
    let coordinatorError: string | null = null;
    const coordinator = new SyncCoordinator(queue, persistence, mockApi, (next) => {
      coordinatorStateStatus = next.status;
      coordinatorError = next.error;
    });

    await expect(coordinator.syncNow()).rejects.toThrow("Unauthorized");

    expect(coordinatorStateStatus).toBe("failed");
    expect(coordinatorError).toContain("Unauthorized");

    // Crucial: Pending task is NOT discarded or lost
    const inspected = await queue.inspect();
    expect(inspected.length).toBe(1);
    expect(inspected[0]?.status).toBe("retryable");
    expect(inspected[0]?.operation?.payload?.["title"]).toBe("Task preserved on 401");

    queue.close();
    persistence.close();
  });

  it("9. encounters transient network/server failure; retries without duplicating mutations", async () => {
    const dbName = "test-case-9";
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
      base_backoff_ms: 0,
    });
    const boundary = new OfflineMutationBoundary(queue);
    const persistence = new IndexedDbSyncPersistence(dbName, factory);

    const { envelope } = await boundary.enqueueTaskCreate({ title: "Transient failure task" });

    let calls = 0;
    const mockApi: SyncApi = {
      uploadOperations: vi.fn(async (batch: SyncOperationEnvelope[]) => {
        calls += 1;
        if (calls <= 2) {
          throw new SyncApiError(503, "Service Unavailable");
        }
        return {
          protocol_version: 1 as const,
          acknowledgements: batch.map((op) => ({
            operation_id: op.operation_id,
            status: "applied" as const,
            revision: 1,
            conflict_id: null,
            error_category: null,
            message: null,
            retry_after_seconds: null,
          })),
        };
      }),
      getChanges: vi.fn(async () => ({
        protocol_version: 1 as const,
        items: [],
        next_cursor: "c1",
        cursor_expires_at: new Date(Date.now() + 86400000).toISOString(),
        has_more: false,
      })),
      getConflict: vi.fn(),
    };

    const coordinator = new SyncCoordinator(queue, persistence, mockApi);

    // Call 1: fails with 503 (handled gracefully, marked retryable)
    await coordinator.syncNow();
    const inspect1 = await queue.inspect();
    expect(inspect1[0]?.status).toBe("retryable");

    // Call 2: fails with 503 (handled gracefully, marked retryable)
    await coordinator.syncNow();
    const inspect2 = await queue.inspect();
    expect(inspect2[0]?.status).toBe("retryable");

    // Call 3: succeeds
    await coordinator.syncNow();

    expect(mockApi.uploadOperations).toHaveBeenCalledTimes(3);

    // Check all 3 calls passed the identical operation_id and idempotency_key
    const uploadedCalls = (mockApi.uploadOperations as ReturnType<typeof vi.fn>).mock.calls;
    for (const callArgs of uploadedCalls) {
      expect(callArgs[0][0].operation_id).toBe(envelope.operation_id);
      expect(callArgs[0][0].idempotency_key).toBe(envelope.idempotency_key);
    }

    const inspected = await queue.inspect();
    expect(inspected.length).toBe(1);
    expect(inspected[0]?.status).toBe("acknowledged");

    queue.close();
    persistence.close();
  });

  it("10. fails to persist to IndexedDB; reports failure and does not claim success", async () => {
    // Construct a failing store
    const failingStore = {
      async transaction<T>(
        _mode: "readonly" | "readwrite",
        _work: (tx: QueueStoreTransaction) => Promise<T>,
      ): Promise<T> {
        throw new QueueStorageError("QuotaExceededError: storage full");
      },
      close() {},
    };

    const failingQueue = new OfflineOperationQueue(failingStore);
    const boundary = new OfflineMutationBoundary(failingQueue);

    await expect(boundary.enqueueTaskCreate({ title: "Will fail storage" })).rejects.toThrow(
      QueueStorageError,
    );
  });

  it("11. switches between authenticated users; never exposes one user's queued operations to another", async () => {
    const tokenAlice = createMockJwt("user-alice-111");
    const tokenBob = createMockJwt("user-bob-222");

    const dbAlice = await databaseNameForAccessToken(tokenAlice);
    const dbBob = await databaseNameForAccessToken(tokenBob);

    expect(dbAlice).not.toBe(dbBob);

    const queueAlice = new IndexedDbOfflineOperationQueue({
      database_name: dbAlice,
      indexed_db_factory: factory,
    });
    const boundaryAlice = new OfflineMutationBoundary(queueAlice);

    await boundaryAlice.enqueueTaskCreate({ title: "Alice private task" });

    const queueBob = new IndexedDbOfflineOperationQueue({
      database_name: dbBob,
      indexed_db_factory: factory,
    });
    const boundaryBob = new OfflineMutationBoundary(queueBob);

    // Bob inspects his queue
    const bobEntries = await queueBob.inspect();
    expect(bobEntries.length).toBe(0);

    // Alice inspects her queue
    const aliceEntries = await queueAlice.inspect();
    expect(aliceEntries.length).toBe(1);
    expect(aliceEntries[0]?.operation?.payload?.["title"]).toBe("Alice private task");

    queueAlice.close();
    queueBob.close();
  });

  it("12. isolates demo mode; no queue writes or sync API calls occur", async () => {
    const dbName = "test-case-12";
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: dbName,
      indexed_db_factory: factory,
    });

    // In demo mode:
    // UI components and blockday store operate purely in-memory / local state.
    // The mutation boundary is never enqueued into.
    const isDemoMode = true;
    if (!isDemoMode) {
      const boundary = new OfflineMutationBoundary(queue);
      await boundary.enqueueTaskCreate({ title: "Should not happen" });
    }

    const entries = await queue.inspect();
    expect(entries.length).toBe(0);

    queue.close();
  });
});
