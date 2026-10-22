import type {
  OfflineOperationQueue,
  QueueEntry,
  SyncOperationEnvelope,
  EntityType,
} from "./offline-queue";
import type { SyncCoordinator, SyncPersistence } from "./sync-coordinator";
import {
  backendToTask,
  calcDueInDays,
  formatBackendDescription,
  parseDescriptionMetadata,
  type BackendTask,
  type EntitySyncStatus,
  type Mood,
  type Task,
} from "./task-types";
import type { BackendProject, ProjectCreatePayload, ProjectUpdatePayload } from "./project-types";
import { taskApi } from "./task-api";
import { projectApi } from "./project-api";

const CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/**
 * Generate a standard 26-character Crockford Base32 ULID.
 * Complies with the PostgreSQL schema constraint:
 * 10 characters of timestamp (48-bit ms) + 16 characters of cryptographic randomness (80-bit).
 */
export function generateUlid(seedTime: number = Date.now()): string {
  let timeStr = "";
  let time = Math.max(0, Math.floor(seedTime));
  for (let i = 0; i < 10; i += 1) {
    const mod = time % 32;
    timeStr = CROCKFORD_BASE32[mod] + timeStr;
    time = Math.floor(time / 32);
  }

  const randomBytes = new Uint8Array(10);
  crypto.getRandomValues(randomBytes);
  let randomStr = "";
  let buffer = 0;
  let bitsLeft = 0;
  let byteIndex = 0;
  for (let i = 0; i < 16; i += 1) {
    while (bitsLeft < 5 && byteIndex < randomBytes.length) {
      buffer = (buffer << 8) | (randomBytes[byteIndex] ?? 0);
      bitsLeft += 8;
      byteIndex += 1;
    }
    const val = (buffer >> (bitsLeft - 5)) & 31;
    bitsLeft -= 5;
    randomStr += CROCKFORD_BASE32[val];
  }

  return timeStr + randomStr;
}

export function isUlid(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length === 26 &&
    /^[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{26}$/.test(value)
  );
}

export interface TaskCreateMutationParams {
  title: string;
  projectId?: string | null | undefined;
  estimate?: number | undefined;
  mood?: Mood | undefined;
  frog?: boolean | undefined;
  dueDate?: string | null | undefined;
  softDeadline?: string | null | undefined;
  hardDeadline?: string | null | undefined;
  description?: string | null | undefined;
  entityId?: string | undefined;
  dependsOn?: string[] | undefined;
  operationId?: string | undefined;
  idempotencyKey?: string | undefined;
}

export interface TaskUpdateMutationParams {
  updates: Partial<Task>;
  dependsOn?: string[] | undefined;
  operationId?: string | undefined;
  idempotencyKey?: string | undefined;
}

export interface ProjectCreateMutationParams {
  name: string;
  description?: string | null | undefined;
  status?: string | undefined;
  entityId?: string | undefined;
  dependsOn?: string[] | undefined;
  operationId?: string | undefined;
  idempotencyKey?: string | undefined;
}

export interface ProjectUpdateMutationParams {
  updates: ProjectUpdatePayload;
  expectedRevision?: number | undefined;
  dependsOn?: string[] | undefined;
  operationId?: string | undefined;
  idempotencyKey?: string | undefined;
}

export function buildTaskCreateEnvelope(params: TaskCreateMutationParams): SyncOperationEnvelope {
  const entityId = params.entityId ?? generateUlid();
  const operationId = (params.operationId ?? crypto.randomUUID()).toLowerCase();
  const idempotencyKey = params.idempotencyKey ?? `task-create-${entityId}-${operationId}`;
  const formattedDesc = formatBackendDescription(params.description, params.mood, params.estimate);
  const projectId =
    params.projectId && params.projectId !== "unassigned" && isUlid(params.projectId)
      ? params.projectId
      : null;

  return {
    protocol_version: 1,
    operation_id: operationId,
    idempotency_key: idempotencyKey,
    entity_type: "task",
    entity_id: entityId,
    operation_type: "create",
    base_revision: null,
    payload: {
      title: params.title.trim(),
      description: formattedDesc,
      status: "open",
      priority: params.frog ? 1 : 3,
      due_date: params.softDeadline ?? params.dueDate ?? null,
      project_id: projectId,
      capture_id: null,
      ...(params.hardDeadline !== undefined ? { hard_deadline: params.hardDeadline } : {}),
    },
    created_at: new Date().toISOString(),
    depends_on: params.dependsOn ? [...params.dependsOn] : [],
    resolves_conflict_id: null,
  };
}

export function buildTaskUpdateEnvelope(
  currentTask: Task,
  updates: Partial<Task>,
  options: {
    baseRevision?: number;
    dependsOn?: string[];
    operationId?: string;
    idempotencyKey?: string;
  } = {},
): SyncOperationEnvelope {
  const operationId = (options.operationId ?? crypto.randomUUID()).toLowerCase();
  const idempotencyKey =
    options.idempotencyKey ?? `task-update-${currentTask.id}-${Date.now()}-${operationId}`;
  const merged = { ...currentTask, ...updates };
  const formattedDesc = formatBackendDescription(merged.description, merged.mood, merged.estimate);
  const projectId =
    merged.projectId && merged.projectId !== "unassigned" && isUlid(merged.projectId)
      ? merged.projectId
      : null;

  return {
    protocol_version: 1,
    operation_id: operationId,
    idempotency_key: idempotencyKey,
    entity_type: "task",
    entity_id: currentTask.id,
    operation_type: "update",
    base_revision: options.baseRevision ?? currentTask.revision,
    payload: {
      title: merged.title,
      description: formattedDesc,
      status: merged.done ? "done" : "open",
      priority: merged.priority ?? (merged.frog ? 1 : 3),
      due_date: merged.softDeadline ?? merged.dueDate ?? null,
      project_id: projectId,
      capture_id: null,
      ...(updates.hardDeadline !== undefined || currentTask.hardDeadline !== undefined
        ? { hard_deadline: merged.hardDeadline ?? null }
        : {}),
    },
    created_at: new Date().toISOString(),
    depends_on: options.dependsOn ? [...options.dependsOn] : [],
    resolves_conflict_id: null,
  };
}

export function buildTaskDeleteEnvelope(
  currentTask: Task,
  options: {
    baseRevision?: number;
    dependsOn?: string[];
    operationId?: string;
    idempotencyKey?: string;
  } = {},
): SyncOperationEnvelope {
  const operationId = (options.operationId ?? crypto.randomUUID()).toLowerCase();
  const idempotencyKey =
    options.idempotencyKey ?? `task-delete-${currentTask.id}-${Date.now()}-${operationId}`;

  return {
    protocol_version: 1,
    operation_id: operationId,
    idempotency_key: idempotencyKey,
    entity_type: "task",
    entity_id: currentTask.id,
    operation_type: "delete",
    base_revision: options.baseRevision ?? currentTask.revision,
    payload: null,
    created_at: new Date().toISOString(),
    depends_on: options.dependsOn ? [...options.dependsOn] : [],
    resolves_conflict_id: null,
  };
}

export function buildProjectCreateEnvelope(
  params: ProjectCreateMutationParams,
): SyncOperationEnvelope {
  const entityId = params.entityId ?? generateUlid();
  const operationId = (params.operationId ?? crypto.randomUUID()).toLowerCase();
  const idempotencyKey = params.idempotencyKey ?? `project-create-${entityId}-${operationId}`;

  return {
    protocol_version: 1,
    operation_id: operationId,
    idempotency_key: idempotencyKey,
    entity_type: "project",
    entity_id: entityId,
    operation_type: "create",
    base_revision: null,
    payload: {
      name: params.name.trim(),
      description: params.description ?? null,
      status: params.status ?? "active",
    },
    created_at: new Date().toISOString(),
    depends_on: params.dependsOn ? [...params.dependsOn] : [],
    resolves_conflict_id: null,
  };
}

export function buildProjectUpdateEnvelope(
  currentProject: BackendProject,
  updates: ProjectUpdatePayload,
  options: {
    baseRevision?: number;
    dependsOn?: string[];
    operationId?: string;
    idempotencyKey?: string;
  } = {},
): SyncOperationEnvelope {
  const operationId = (options.operationId ?? crypto.randomUUID()).toLowerCase();
  const idempotencyKey =
    options.idempotencyKey ?? `project-update-${currentProject.id}-${Date.now()}-${operationId}`;
  const mergedName = updates.name !== undefined ? updates.name : currentProject.name;
  const mergedDesc =
    updates.description !== undefined ? updates.description : currentProject.description;
  const mergedStatus = updates.status !== undefined ? updates.status : currentProject.status;

  return {
    protocol_version: 1,
    operation_id: operationId,
    idempotency_key: idempotencyKey,
    entity_type: "project",
    entity_id: currentProject.id,
    operation_type: "update",
    base_revision: options.baseRevision ?? currentProject.revision,
    payload: {
      name: mergedName,
      description: mergedDesc,
      status: mergedStatus,
    },
    created_at: new Date().toISOString(),
    depends_on: options.dependsOn ? [...options.dependsOn] : [],
    resolves_conflict_id: null,
  };
}

export function buildProjectDeleteEnvelope(
  currentProject: BackendProject,
  options: {
    baseRevision?: number;
    dependsOn?: string[];
    operationId?: string;
    idempotencyKey?: string;
  } = {},
): SyncOperationEnvelope {
  const operationId = (options.operationId ?? crypto.randomUUID()).toLowerCase();
  const idempotencyKey =
    options.idempotencyKey ?? `project-delete-${currentProject.id}-${Date.now()}-${operationId}`;

  return {
    protocol_version: 1,
    operation_id: operationId,
    idempotency_key: idempotencyKey,
    entity_type: "project",
    entity_id: currentProject.id,
    operation_type: "delete",
    base_revision: options.baseRevision ?? currentProject.revision,
    payload: null,
    created_at: new Date().toISOString(),
    depends_on: options.dependsOn ? [...options.dependsOn] : [],
    resolves_conflict_id: null,
  };
}

export function buildResolveConflictEnvelope(params: {
  conflictId: string;
  entityType: EntityType;
  entityId: string;
  baseRevision: number;
  payload: Record<string, unknown> | null;
  dependsOn?: string[] | undefined;
  operationId?: string | undefined;
  idempotencyKey?: string | undefined;
}): SyncOperationEnvelope {
  const operationId = (params.operationId ?? crypto.randomUUID()).toLowerCase();
  const idempotencyKey =
    params.idempotencyKey ?? `resolve-${params.conflictId}-${Date.now()}-${operationId}`;

  return {
    protocol_version: 1,
    operation_id: operationId,
    idempotency_key: idempotencyKey,
    entity_type: params.entityType,
    entity_id: params.entityId,
    operation_type: "resolve_conflict",
    base_revision: params.baseRevision,
    payload: params.payload,
    created_at: new Date().toISOString(),
    depends_on: params.dependsOn ? [...params.dependsOn] : [],
    resolves_conflict_id: params.conflictId.toLowerCase(),
  };
}

export class OfflineMutationBoundary {
  constructor(
    readonly queue: OfflineOperationQueue,
    readonly coordinator?: SyncCoordinator | null,
    readonly persistence?: SyncPersistence | null,
  ) {}

  async findPendingOperationForEntity(
    entityType: EntityType,
    entityId: string,
  ): Promise<QueueEntry | undefined> {
    const entries = await this.queue.inspect({
      statuses: ["pending", "in_flight", "retryable"],
    });
    const matches = entries.filter(
      (e) => e.operation?.entity_type === entityType && e.operation?.entity_id === entityId,
    );
    return matches[matches.length - 1];
  }

  async enqueueTaskCreate(
    params: TaskCreateMutationParams,
  ): Promise<{ task: Task; entry: QueueEntry; envelope: SyncOperationEnvelope }> {
    const dependsOn = params.dependsOn ? [...params.dependsOn] : [];

    // If task links to a project, check if there is an unresolved project operation in queue
    if (params.projectId && params.projectId !== "unassigned") {
      const pendingProject = await this.findPendingOperationForEntity("project", params.projectId);
      if (pendingProject && !dependsOn.includes(pendingProject.operation_id)) {
        dependsOn.push(pendingProject.operation_id);
      }
    }

    const envelope = buildTaskCreateEnvelope({ ...params, dependsOn });

    // Persist to IndexedDB before claiming success
    const entry = await this.queue.enqueue(envelope);

    const task: Task = {
      id: envelope.entity_id,
      title: (envelope.payload?.["title"] as string) || params.title.trim(),
      projectId:
        params.projectId && params.projectId !== "unassigned" ? params.projectId : "unassigned",
      dueIn: null,
      estimate: params.estimate ?? 15,
      mood: params.mood ?? "Clear",
      frog: params.frog ?? false,
      done: false,
      revision: 1,
      description: params.description ?? null,
      dueDate: params.dueDate ?? null,
      priority: params.frog ? 1 : 3,
      syncStatus: "pending",
    };

    this.triggerSync();
    return { task, entry, envelope };
  }

  async enqueueTaskUpdate(
    currentTask: Task,
    updates: Partial<Task>,
  ): Promise<{ task: Task; entry: QueueEntry; envelope: SyncOperationEnvelope }> {
    const pendingOp = await this.findPendingOperationForEntity("task", currentTask.id);
    const dependsOn: string[] = [];
    let baseRevision = currentTask.revision;

    if (pendingOp) {
      dependsOn.push(pendingOp.operation_id);
      if (
        pendingOp.operation?.base_revision !== null &&
        pendingOp.operation?.base_revision !== undefined
      ) {
        baseRevision = pendingOp.operation.base_revision + 1;
      } else {
        baseRevision = 1;
      }
    }

    if (
      updates.projectId &&
      updates.projectId !== "unassigned" &&
      updates.projectId !== currentTask.projectId
    ) {
      const pendingProject = await this.findPendingOperationForEntity("project", updates.projectId);
      if (pendingProject && !dependsOn.includes(pendingProject.operation_id)) {
        dependsOn.push(pendingProject.operation_id);
      }
    }

    const envelope = buildTaskUpdateEnvelope(currentTask, updates, {
      baseRevision,
      dependsOn,
    });

    const entry = await this.queue.enqueue(envelope);
    const updatedTask: Task = {
      ...currentTask,
      ...updates,
      syncStatus: "pending",
    };

    this.triggerSync();
    return { task: updatedTask, entry, envelope };
  }

  async enqueueTaskToggle(
    currentTask: Task,
  ): Promise<{ task: Task; entry: QueueEntry; envelope: SyncOperationEnvelope }> {
    return this.enqueueTaskUpdate(currentTask, { done: !currentTask.done });
  }

  async enqueueTaskDelete(
    currentTask: Task,
  ): Promise<{ entry: QueueEntry; envelope: SyncOperationEnvelope }> {
    const pendingOp = await this.findPendingOperationForEntity("task", currentTask.id);
    const dependsOn: string[] = [];
    let baseRevision = currentTask.revision;

    if (pendingOp) {
      dependsOn.push(pendingOp.operation_id);
      if (
        pendingOp.operation?.base_revision !== null &&
        pendingOp.operation?.base_revision !== undefined
      ) {
        baseRevision = pendingOp.operation.base_revision + 1;
      } else {
        baseRevision = 1;
      }
    }

    const envelope = buildTaskDeleteEnvelope(currentTask, {
      baseRevision,
      dependsOn,
    });

    const entry = await this.queue.enqueue(envelope);
    this.triggerSync();
    return { entry, envelope };
  }

  async enqueueProjectCreate(
    params: ProjectCreateMutationParams,
  ): Promise<{ project: BackendProject; entry: QueueEntry; envelope: SyncOperationEnvelope }> {
    const envelope = buildProjectCreateEnvelope(params);
    const entry = await this.queue.enqueue(envelope);

    const project: BackendProject = {
      id: envelope.entity_id,
      owner_id: "",
      name: (envelope.payload?.["name"] as string) || params.name.trim(),
      description: params.description ?? null,
      status: "active",
      revision: 1,
      created_at: envelope.created_at,
      updated_at: envelope.created_at,
      syncStatus: "pending",
    };

    this.triggerSync();
    return { project, entry, envelope };
  }

  async enqueueProjectUpdate(
    currentProject: BackendProject,
    updates: ProjectUpdatePayload,
    expectedRevision?: number,
  ): Promise<{ project: BackendProject; entry: QueueEntry; envelope: SyncOperationEnvelope }> {
    const pendingOp = await this.findPendingOperationForEntity("project", currentProject.id);
    const dependsOn: string[] = [];
    let baseRevision = expectedRevision ?? currentProject.revision;

    if (pendingOp) {
      dependsOn.push(pendingOp.operation_id);
      if (
        pendingOp.operation?.base_revision !== null &&
        pendingOp.operation?.base_revision !== undefined
      ) {
        baseRevision = pendingOp.operation.base_revision + 1;
      } else {
        baseRevision = 1;
      }
    }

    const envelope = buildProjectUpdateEnvelope(currentProject, updates, {
      baseRevision,
      dependsOn,
    });

    const entry = await this.queue.enqueue(envelope);
    const updatedProject: BackendProject = {
      ...currentProject,
      ...updates,
      syncStatus: "pending",
    };

    this.triggerSync();
    return { project: updatedProject, entry, envelope };
  }

  async enqueueProjectDelete(
    currentProject: BackendProject,
  ): Promise<{ entry: QueueEntry; envelope: SyncOperationEnvelope }> {
    const pendingOp = await this.findPendingOperationForEntity("project", currentProject.id);
    const dependsOn: string[] = [];
    let baseRevision = currentProject.revision;

    if (pendingOp) {
      dependsOn.push(pendingOp.operation_id);
      if (
        pendingOp.operation?.base_revision !== null &&
        pendingOp.operation?.base_revision !== undefined
      ) {
        baseRevision = pendingOp.operation.base_revision + 1;
      } else {
        baseRevision = 1;
      }
    }

    const envelope = buildProjectDeleteEnvelope(currentProject, {
      baseRevision,
      dependsOn,
    });

    const entry = await this.queue.enqueue(envelope);
    this.triggerSync();
    return { entry, envelope };
  }

  async enqueueResolveConflict(params: {
    conflictId: string;
    entityType: "task" | "project";
    entityId: string;
    baseRevision: number;
    payload: Record<string, unknown> | null;
  }): Promise<{ entry: QueueEntry; envelope: SyncOperationEnvelope }> {
    const envelope = buildResolveConflictEnvelope(params);
    const entry = await this.queue.enqueue(envelope);
    this.triggerSync();
    return { entry, envelope };
  }

  async loadTasks(prefetchedTasks?: Task[]): Promise<Task[]> {
    let baseTasks: Task[] = prefetchedTasks ?? [];

    if (!prefetchedTasks) {
      try {
        const res = await taskApi.listTasks();
        if (res?.items && Array.isArray(res.items)) {
          baseTasks = res.items.map((bt) => backendToTask(bt));
        }
      } catch (err: unknown) {
        // If offline, check applied change history from persistence
        if (this.persistence) {
          try {
            const changes = await this.persistence.listAppliedChanges();
            const taskChanges = changes.filter((c) => c.entity_type === "task");
            const byId = new Map<string, Task>();
            for (const item of taskChanges) {
              if (item.tombstone || item.snapshot?.deleted) {
                byId.delete(item.entity_id);
              } else if (item.snapshot?.data) {
                const bt: BackendTask = {
                  id: item.entity_id,
                  owner_id: "",
                  project_id: (item.snapshot.data["project_id"] as string) || null,
                  capture_id: (item.snapshot.data["capture_id"] as string) || null,
                  title: (item.snapshot.data["title"] as string) || "",
                  description: (item.snapshot.data["description"] as string) || null,
                  status: (item.snapshot.data["status"] as string) || "open",
                  priority: (item.snapshot.data["priority"] as number) ?? null,
                  due_date: (item.snapshot.data["due_date"] as string) || null,
                  hard_deadline: (item.snapshot.data["hard_deadline"] as string) || null,
                  revision: item.revision,
                  created_at: "",
                  updated_at: "",
                };
                byId.set(item.entity_id, backendToTask(bt));
              }
            }
            baseTasks = [...byId.values()];
          } catch {
            // Ignore persistence read errors
          }
        }
        // If still empty and not a network error, rethrow
        const isNetwork =
          err instanceof TypeError ||
          (err instanceof Error &&
            (err.message.includes("Network") ||
              err.message.includes("fetch") ||
              err.message.includes("Failed")));
        if (baseTasks.length === 0 && !isNetwork) {
          throw err;
        }
      }
    }

    return this.overlayQueueOnTasks(baseTasks);
  }

  async loadProjects(prefetchedProjects?: BackendProject[]): Promise<BackendProject[]> {
    let baseProjects: BackendProject[] = prefetchedProjects ?? [];

    if (!prefetchedProjects) {
      try {
        const res = await projectApi.listProjects();
        if (res?.items && Array.isArray(res.items)) {
          baseProjects = res.items;
        }
      } catch (err: unknown) {
        if (this.persistence) {
          try {
            const changes = await this.persistence.listAppliedChanges();
            const projectChanges = changes.filter((c) => c.entity_type === "project");
            const byId = new Map<string, BackendProject>();
            for (const item of projectChanges) {
              if (item.tombstone || item.snapshot?.deleted) {
                byId.delete(item.entity_id);
              } else if (item.snapshot?.data) {
                byId.set(item.entity_id, {
                  id: item.entity_id,
                  owner_id: "",
                  name: (item.snapshot.data["name"] as string) || "",
                  description: (item.snapshot.data["description"] as string) || null,
                  status: (item.snapshot.data["status"] as string) || "active",
                  revision: item.revision,
                  created_at: "",
                  updated_at: "",
                });
              }
            }
            baseProjects = [...byId.values()];
          } catch {
            // Ignore persistence read errors
          }
        }
        const isNetwork =
          err instanceof TypeError ||
          (err instanceof Error &&
            (err.message.includes("Network") ||
              err.message.includes("fetch") ||
              err.message.includes("Failed")));
        if (baseProjects.length === 0 && !isNetwork) {
          throw err;
        }
      }
    }

    return this.overlayQueueOnProjects(baseProjects);
  }

  async overlayQueueOnTasks(serverTasks: Task[]): Promise<Task[]> {
    const entries = await this.queue.inspect();
    const taskEntries = entries
      .filter((e) => e.operation?.entity_type === "task")
      .sort((a, b) => Date.parse(a.enqueued_at) - Date.parse(b.enqueued_at));

    const taskMap = new Map<string, Task>(
      serverTasks.map((t) => [t.id, { ...t, syncStatus: t.syncStatus ?? "synced" }]),
    );

    for (const entry of taskEntries) {
      if (entry.status === "cancelled") continue;
      const op = entry.operation;
      if (!op) continue;

      const entitySyncStatus: EntitySyncStatus =
        entry.status === "conflicted"
          ? "conflict"
          : entry.status === "failed"
            ? "failed"
            : entry.status === "acknowledged"
              ? "synced"
              : "pending";

      if (op.operation_type === "create") {
        const payload = op.payload ?? {};
        const parsed = parseDescriptionMetadata(payload["description"] as string | null);
        const softDeadline = (payload["due_date"] as string) || null;
        const hardDeadline = (payload["hard_deadline"] as string) || null;
        const softDueIn = calcDueInDays(softDeadline);
        const hardDueIn = calcDueInDays(hardDeadline);
        const task: Task = {
          id: op.entity_id,
          title: (payload["title"] as string) || "Untitled Task",
          projectId: (payload["project_id"] as string) || "unassigned",
          dueIn: softDueIn ?? hardDueIn ?? null,
          softDueIn,
          hardDueIn,
          estimate: parsed.estimate ?? 15,
          mood: parsed.mood ?? "Clear",
          frog: payload["priority"] === 1,
          done: payload["status"] === "done",
          revision: entry.acknowledgement?.revision ?? 1,
          description: parsed.userDescription,
          dueDate: softDeadline,
          softDeadline,
          hardDeadline,
          priority: (payload["priority"] as number) ?? null,
          syncStatus: entitySyncStatus,
          conflictId: entry.acknowledgement?.conflict_id ?? null,
        };
        taskMap.set(op.entity_id, task);
      } else if (op.operation_type === "update") {
        const existing = taskMap.get(op.entity_id);
        const payload = op.payload ?? {};
        const parsed = parseDescriptionMetadata(payload["description"] as string | null);
        if (existing) {
          const softDeadline =
            payload["due_date"] !== undefined
              ? (payload["due_date"] as string) || null
              : existing.softDeadline;
          const hardDeadline =
            payload["hard_deadline"] !== undefined
              ? (payload["hard_deadline"] as string) || null
              : existing.hardDeadline;
          const softDueIn = calcDueInDays(softDeadline);
          const hardDueIn = calcDueInDays(hardDeadline);
          taskMap.set(op.entity_id, {
            ...existing,
            title: payload["title"] !== undefined ? (payload["title"] as string) : existing.title,
            projectId:
              payload["project_id"] !== undefined
                ? (payload["project_id"] as string) || "unassigned"
                : existing.projectId,
            done: payload["status"] !== undefined ? payload["status"] === "done" : existing.done,
            priority:
              payload["priority"] !== undefined
                ? (payload["priority"] as number)
                : existing.priority,
            frog: payload["priority"] !== undefined ? payload["priority"] === 1 : existing.frog,
            dueDate: softDeadline,
            softDeadline,
            hardDeadline,
            dueIn: softDueIn ?? hardDueIn ?? null,
            softDueIn,
            hardDueIn,
            description: parsed.userDescription ?? existing.description,
            mood: parsed.mood ?? existing.mood,
            estimate: parsed.estimate ?? existing.estimate,
            revision: entry.acknowledgement?.revision ?? existing.revision,
            syncStatus: entitySyncStatus,
            conflictId: entry.acknowledgement?.conflict_id ?? existing.conflictId,
          });
        }
      } else if (op.operation_type === "delete") {
        taskMap.delete(op.entity_id);
      }
    }

    return [...taskMap.values()];
  }

  async overlayQueueOnProjects(serverProjects: BackendProject[]): Promise<BackendProject[]> {
    const entries = await this.queue.inspect();
    const projectEntries = entries
      .filter((e) => e.operation?.entity_type === "project")
      .sort((a, b) => Date.parse(a.enqueued_at) - Date.parse(b.enqueued_at));

    const projectMap = new Map<string, BackendProject>(
      serverProjects.map((p) => [p.id, { ...p, syncStatus: p.syncStatus ?? "synced" }]),
    );

    for (const entry of projectEntries) {
      if (entry.status === "cancelled") continue;
      const op = entry.operation;
      if (!op) continue;

      const entitySyncStatus: EntitySyncStatus =
        entry.status === "conflicted"
          ? "conflict"
          : entry.status === "failed"
            ? "failed"
            : entry.status === "acknowledged"
              ? "synced"
              : "pending";

      if (op.operation_type === "create") {
        const payload = op.payload ?? {};
        const project: BackendProject = {
          id: op.entity_id,
          owner_id: "",
          name: (payload["name"] as string) || "Untitled Project",
          description: (payload["description"] as string) || null,
          status: (payload["status"] as string) || "active",
          revision: entry.acknowledgement?.revision ?? 1,
          created_at: op.created_at,
          updated_at: op.created_at,
          syncStatus: entitySyncStatus,
          conflictId: entry.acknowledgement?.conflict_id ?? null,
        };
        projectMap.set(op.entity_id, project);
      } else if (op.operation_type === "update") {
        const existing = projectMap.get(op.entity_id);
        const payload = op.payload ?? {};
        if (existing) {
          projectMap.set(op.entity_id, {
            ...existing,
            name: payload["name"] !== undefined ? (payload["name"] as string) : existing.name,
            description:
              payload["description"] !== undefined
                ? (payload["description"] as string)
                : existing.description,
            status:
              payload["status"] !== undefined ? (payload["status"] as string) : existing.status,
            revision: entry.acknowledgement?.revision ?? existing.revision,
            syncStatus: entitySyncStatus,
            conflictId: entry.acknowledgement?.conflict_id ?? existing.conflictId,
          });
        }
      } else if (op.operation_type === "delete") {
        projectMap.delete(op.entity_id);
      }
    }

    return [...projectMap.values()];
  }

  triggerSync(): void {
    if (this.coordinator && typeof navigator !== "undefined" && navigator.onLine) {
      void this.coordinator.syncNow().catch(() => {});
    }
  }
}
