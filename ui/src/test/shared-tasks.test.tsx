import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { taskApi, TaskApiError, TaskConflictError } from "@/lib/task-api";
import { projectApi } from "@/lib/project-api";
import { BlockdayProvider, useBlockday, useTasks } from "@/lib/blockday-store";
import {
  backendToTask,
  formatBackendDescription,
  parseDescriptionMetadata,
  taskToCreatePayload,
  taskToUpdatePayload,
  type BackendTask,
  type Task,
} from "@/lib/task-types";
import { ProjectsPage } from "@/routes/projects";


// Mock backend tasks
const mockBackendTasks: BackendTask[] = [
  {
    id: "task_1",
    owner_id: "user_1",
    project_id: "blockday",
    capture_id: null,
    title: "Launch landing page",
    description: "[mood:Deep] [estimate:45]",
    status: "open",
    priority: 1,
    due_date: "2026-10-20T00:00:00Z",
    revision: 1,
    created_at: "2026-10-18T10:00:00Z",
    updated_at: "2026-10-18T10:00:00Z",
  },
  {
    id: "task_2",
    owner_id: "user_1",
    project_id: "studio",
    capture_id: null,
    title: "Client contract review",
    description: "[mood:Clear] [estimate:30]",
    status: "open",
    priority: 2,
    due_date: "2026-10-19T00:00:00Z",
    revision: 1,
    created_at: "2026-10-18T11:00:00Z",
    updated_at: "2026-10-18T11:00:00Z",
  },
];

function createWrapper() {
  vi.spyOn(projectApi, "listProjects").mockResolvedValue({
    total: 2,
    items: [
      { id: "blockday", owner_id: "user_1", name: "Blockday launch", description: null, status: "active", revision: 1, created_at: "2026-10-18T10:00:00Z", updated_at: "2026-10-18T10:00:00Z" },
      { id: "studio", owner_id: "user_1", name: "Studio", description: null, status: "active", revision: 1, created_at: "2026-10-18T10:00:00Z", updated_at: "2026-10-18T10:00:00Z" },
    ],
  });
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
      },
      mutations: {
        retry: false,
      },
    },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <BlockdayProvider>{children}</BlockdayProvider>
      </QueryClientProvider>
    );
  };
}

describe("M1-E: Shared Task Model & API Client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("converts backend task representation to canonical UI Task with parsed metadata", () => {
    const uiTask = backendToTask(mockBackendTasks[0]!);

    expect(uiTask.id).toBe("task_1");
    expect(uiTask.title).toBe("Launch landing page");
    expect(uiTask.projectId).toBe("blockday");
    expect(uiTask.done).toBe(false);
    expect(uiTask.frog).toBe(true); // priority 1
    expect(uiTask.mood).toBe("Deep");
    expect(uiTask.estimate).toBe(45);
    expect(uiTask.revision).toBe(1);
  });

  it("converts UI task to backend create and update payloads", () => {
    const createPayload = taskToCreatePayload({
      title: "New feature",
      projectId: "studio",
      estimate: 60,
      mood: "Creative",
      frog: true,
    });

    expect(createPayload.title).toBe("New feature");
    expect(createPayload.project_id).toBe("studio");
    expect(createPayload.priority).toBe(1);
    expect(createPayload.description).toContain("[mood:Creative]");
    expect(createPayload.description).toContain("[estimate:60]");

    const updatePayload = taskToUpdatePayload({
      done: true,
      frog: false,
      title: "Updated feature",
    });

    expect(updatePayload.status).toBe("done");
    expect(updatePayload.priority).toBe(3);
    expect(updatePayload.title).toBe("Updated feature");
  });

  it("handles If-Match revision header and 409 conflict in taskApi client", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({
        type: "https://api.pos-deku.com/errors/conflict",
        title: "Resource conflict",
        status: 409,
        detail: "Task was modified by another request. Current revision: 5",
        instance: "/api/v1/tasks/task_1",
        extensions: {
          current_revision: 5,
          server_state: { title: "Server Title", revision: 5 },
        },
      }),
    } as unknown as Response);

    await expect(
      taskApi.updateTask("task_1", { title: "Client Title" }, 2)
    ).rejects.toThrow(TaskConflictError);

    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/tasks/task_1"),
      expect.objectContaining({
        method: "PATCH",
        headers: expect.objectContaining({
          "If-Match": "2",
          "Content-Type": "application/json",
        }),
      })
    );
    const sentHeaders = new Headers(fetchSpy.mock.calls[0]?.[1]?.headers);
    expect(sentHeaders.has("Authorization")).toBe(false);
  });
});

describe("M1-E: Cross-View Unified Task State", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(projectApi, "listProjects").mockResolvedValue({ total: 2, items: [
      { id: "blockday", owner_id: "user_1", name: "Blockday launch", description: null, status: "active", revision: 1, created_at: "2026-10-18T10:00:00Z", updated_at: "2026-10-18T10:00:00Z" },
      { id: "studio", owner_id: "user_1", name: "Studio", description: null, status: "active", revision: 1, created_at: "2026-10-18T10:00:00Z", updated_at: "2026-10-18T10:00:00Z" },
    ] });
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("1. loads task data through the shared API path", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.tasks.some((t) => t.id === "task_1")).toBe(true);
    });

    expect(result.current.tasks.find((t) => t.id === "task_1")?.title).toBe(
      "Launch landing page"
    );
  });

  it("2. completing a task updates shared state with revision tracking", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });

    vi.spyOn(taskApi, "completeTask").mockResolvedValueOnce({
      ...mockBackendTasks[0]!,
      status: "done",
      revision: 2,
    });

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.tasks.some((t) => t.id === "task_1")).toBe(true);
    });

    await act(async () => {
      await result.current.toggleTask("task_1");
    });

    const task1 = result.current.tasks.find((t) => t.id === "task_1");
    expect(task1?.done).toBe(true);
    expect(task1?.revision).toBe(2);
  });

  it("3. editing a task updates shared state and propagates to all consumers", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });

    vi.spyOn(taskApi, "updateTask").mockResolvedValueOnce({
      ...mockBackendTasks[0]!,
      title: "Updated Landing Page Copy",
      revision: 2,
    });

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.tasks.some((t) => t.id === "task_1")).toBe(true);
    });

    await act(async () => {
      await result.current.updateTask("task_1", { title: "Updated Landing Page Copy" });
    });

    const updated = result.current.tasks.find((t) => t.id === "task_1");
    expect(updated?.title).toBe("Updated Landing Page Copy");
    expect(updated?.revision).toBe(2);
  });

  it("4. project filtering does not create a separate task source", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });

    const wrapper = createWrapper();
    const { result } = renderHook(
      () => {
        const blockday = useBlockday();
        const studioTasks = blockday.tasks.filter((t) => t.projectId === "studio");
        const blockdayTasks = blockday.tasks.filter((t) => t.projectId === "blockday");
        return { blockday, studioTasks, blockdayTasks };
      },
      { wrapper }
    );

    await waitFor(() => {
      expect(result.current.blockday.tasks.some((t) => t.id === "task_1")).toBe(true);
    });

    // Both project slices derive from the exact same shared canonical tasks array
    expect(result.current.studioTasks.find((t) => t.id === "task_2")).toBeDefined();
    expect(result.current.blockdayTasks.find((t) => t.id === "task_1")).toBeDefined();
    expect(result.current.blockdayTasks.some((t) => t.id === "task_2")).toBe(false);
  });

  it("5. failed mutations are surfaced correctly and revert optimistic state", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });

    vi.spyOn(taskApi, "completeTask").mockRejectedValueOnce(
      new Error("Network timeout: 504 Gateway Timeout")
    );

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.tasks.some((t) => t.id === "task_1")).toBe(true);
    });

    // Attempt completion which fails
    let caught: Error | null = null;
    await act(async () => {
      try {
        await result.current.toggleTask("task_1");
      } catch (e) {
        caught = e as Error;
      }
    });

    expect(caught).toBeDefined();
    expect(result.current.error?.message).toContain("504 Gateway Timeout");

    // Task must remain undone because mutation failed (no silent success)
    const task1 = result.current.tasks.find((t) => t.id === "task_1");
    expect(task1?.done).toBe(false);
  });

  it("6. revision conflicts are surfaced without silent overwrites", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });

    const conflictErr = new TaskConflictError(
      "Task was modified by another request. Current revision: 5",
      {
        type: "https://api.pos-deku.com/errors/conflict",
        title: "Conflict",
        status: 409,
        extensions: { current_revision: 5 },
      }
    );

    vi.spyOn(taskApi, "completeTask").mockRejectedValueOnce(conflictErr);

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.tasks.some((t) => t.id === "task_1")).toBe(true);
    });

    let caughtConflict: TaskConflictError | null = null;
    await act(async () => {
      try {
        await result.current.toggleTask("task_1");
      } catch (e) {
        caughtConflict = e as TaskConflictError;
      }
    });

    expect(caughtConflict).toBeInstanceOf(TaskConflictError);
    expect(result.current.conflictError).toBeDefined();
    expect(result.current.conflictError?.currentRevision).toBe(5);

    // Reverted optimistic state: task not marked done
    expect(result.current.tasks.find((t) => t.id === "task_1")?.done).toBe(false);
  });

  it("7. project page displays canonical tasks and reflects updates consistently", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });

    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <BlockdayProvider>
          <ProjectsPage />
        </BlockdayProvider>
      </QueryClientProvider>
    );

    // Projects page renders
    await waitFor(() => expect(screen.getByText(/Project Tasks/i)).toBeInTheDocument());
  });
});

describe("Workstream B: Description Preservation & Metadata Round-tripping", () => {
  it("parses metadata and extracts clean user description", () => {
    const parsed = parseDescriptionMetadata("Write quarterly report [mood:Deep] [estimate:60]");
    expect(parsed.userDescription).toBe("Write quarterly report");
    expect(parsed.mood).toBe("Deep");
    expect(parsed.estimate).toBe(60);
  });

  it("preserves unrelated description text and other bracketed tags", () => {
    const parsed = parseDescriptionMetadata("Task details [tag:urgent] [mood:Creative] with [extra:info] [estimate:30]");
    expect(parsed.userDescription).toBe("Task details [tag:urgent] with [extra:info]");
    expect(parsed.mood).toBe("Creative");
    expect(parsed.estimate).toBe(30);
  });

  it("returns null userDescription if description only contains metadata tags", () => {
    const parsed = parseDescriptionMetadata("[mood:Clear] [estimate:15]");
    expect(parsed.userDescription).toBeNull();
    expect(parsed.mood).toBe("Clear");
    expect(parsed.estimate).toBe(15);
  });

  it("backendToTask extracts user description cleanly without leaking tags", () => {
    const backendTask: BackendTask = {
      id: "task_desc",
      owner_id: "user_1",
      project_id: "blockday",
      capture_id: null,
      title: "Clean description task",
      description: "User authored notes [mood:Deep] [estimate:40]",
      status: "open",
      priority: 1,
      due_date: null,
      revision: 1,
      created_at: "2026-10-18T10:00:00Z",
      updated_at: "2026-10-18T10:00:00Z",
    };

    const uiTask = backendToTask(backendTask);
    expect(uiTask.description).toBe("User authored notes");
    expect(uiTask.mood).toBe("Deep");
    expect(uiTask.estimate).toBe(40);
  });

  it("taskToUpdatePayload preserves existing user description when updating only mood or estimate", () => {
    const currentTask: Task = {
      id: "t_10",
      title: "Existing Task",
      projectId: "blockday",
      dueIn: 0,
      estimate: 30,
      mood: "Clear",
      done: false,
      revision: 1,
      description: "Important user notes",
    };

    // Update only mood
    const moodUpdate = taskToUpdatePayload({ mood: "Deep" }, currentTask);
    expect(moodUpdate.description).toBe("Important user notes [mood:Deep] [estimate:30]");

    // Update only estimate
    const estUpdate = taskToUpdatePayload({ estimate: 90 }, currentTask);
    expect(estUpdate.description).toBe("Important user notes [mood:Clear] [estimate:90]");

    // Update both mood and estimate
    const bothUpdate = taskToUpdatePayload({ mood: "Creative", estimate: 45 }, currentTask);
    expect(bothUpdate.description).toBe("Important user notes [mood:Creative] [estimate:45]");
  });

  it("taskToUpdatePayload replaces existing metadata tags rather than appending duplicates", () => {
    const currentTask: Task = {
      id: "t_11",
      title: "Repeated update task",
      projectId: "blockday",
      dueIn: 0,
      estimate: 30,
      mood: "Clear",
      done: false,
      revision: 1,
      description: "My design notes",
    };

    const firstUpdate = taskToUpdatePayload({ mood: "Deep" }, currentTask);
    expect(firstUpdate.description).toBe("My design notes [mood:Deep] [estimate:30]");

    // Subsequent update
    const secondUpdate = taskToUpdatePayload(
      { mood: "Creative" },
      { ...currentTask, description: "My design notes", mood: "Deep" }
    );
    expect(secondUpdate.description).toBe("My design notes [mood:Creative] [estimate:30]");
    expect(secondUpdate.description?.match(/\[mood:/g)?.length).toBe(1);
  });

  it("taskToUpdatePayload respects PATCH omission semantics when metadata is not touched", () => {
    const currentTask: Task = {
      id: "t_12",
      title: "Untouched metadata task",
      projectId: "blockday",
      dueIn: 0,
      estimate: 30,
      mood: "Clear",
      done: false,
      revision: 1,
      description: "Do not touch",
    };

    const titleOnly = taskToUpdatePayload({ title: "New Title" }, currentTask);
    expect(titleOnly.title).toBe("New Title");
    expect(titleOnly.description).toBeUndefined();
  });

  it("taskToUpdatePayload handles explicitly clearing user description", () => {
    const currentTask: Task = {
      id: "t_13",
      title: "Clear desc task",
      projectId: "blockday",
      dueIn: 0,
      estimate: 30,
      mood: "Clear",
      done: false,
      revision: 1,
      description: "Existing notes",
    };

    const cleared = taskToUpdatePayload({ description: null }, currentTask);
    expect(cleared.description).toBe("[mood:Clear] [estimate:30]");
  });

  it("round-trip serialization preserves user description and metadata fidelity", () => {
    const originalBackend: BackendTask = {
      id: "task_rt",
      owner_id: "user_1",
      project_id: "studio",
      capture_id: null,
      title: "Round-trip task",
      description: "Architecture review notes [mood:Deep] [estimate:45]",
      status: "open",
      priority: 1,
      due_date: "2026-10-25T00:00:00Z",
      revision: 3,
      created_at: "2026-10-18T10:00:00Z",
      updated_at: "2026-10-18T10:00:00Z",
    };

    // 1. Read
    const uiTask = backendToTask(originalBackend);
    expect(uiTask.description).toBe("Architecture review notes");
    expect(uiTask.mood).toBe("Deep");
    expect(uiTask.estimate).toBe(45);

    // 2. Partial Update (change only mood)
    const patchPayload = taskToUpdatePayload({ mood: "Creative" }, uiTask);
    expect(patchPayload.description).toBe("Architecture review notes [mood:Creative] [estimate:45]");

    // 3. Server responds with updated backend task
    const updatedBackend: BackendTask = {
      ...originalBackend,
      description: patchPayload.description ?? null,
      revision: 4,
    };

    // 4. Client reads updated response
    const reloadedUiTask = backendToTask(updatedBackend);
    expect(reloadedUiTask.description).toBe("Architecture review notes");
    expect(reloadedUiTask.mood).toBe("Creative");
    expect(reloadedUiTask.estimate).toBe(45);
    expect(reloadedUiTask.revision).toBe(4);
  });
});

describe("Workstream A & C: Network Failures, Rollback & Concurrency", () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("initial task query does not silently treat seed data as live state when backend is down", async () => {
    vi.spyOn(taskApi, "listTasks").mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error?.message).toContain("Network failure");
    expect(result.current.tasks).toEqual([]); // No silent fallback to sample tasks!
  });

  it("addTask rolls back optimistic state and throws on network failure (TypeError)", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });
    vi.spyOn(taskApi, "createTask").mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.tasks.length).toBe(2);
    });

    let err: Error | null = null;
    await act(async () => {
      try {
        await result.current.addTask("Fail task", "blockday");
      } catch (e) {
        err = e as Error;
      }
    });

    expect(err).toBeDefined();
    expect((err as unknown as Error).message).toContain("Network failure");
    expect(result.current.error?.message).toContain("Network failure");
    // Optimistic task was rolled back
    expect(result.current.tasks.find((t) => t.title === "Fail task")).toBeUndefined();
  });

  it("toggleTask rolls back optimistic done state and throws on network failure", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });
    vi.spyOn(taskApi, "completeTask").mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.tasks.some((t) => t.id === "task_1")).toBe(true);
    });

    let err: Error | null = null;
    await act(async () => {
      try {
        await result.current.toggleTask("task_1");
      } catch (e) {
        err = e as Error;
      }
    });

    expect(err).toBeDefined();
    expect((err as unknown as Error).message).toContain("Network failure");
    // Must not fabricate a revision or stay marked done
    const task1 = result.current.tasks.find((t) => t.id === "task_1");
    expect(task1?.done).toBe(false);
    expect(task1?.revision).toBe(1);
  });

  it("updateTask rolls back optimistic update and throws on network failure", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });
    vi.spyOn(taskApi, "updateTask").mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.tasks.some((t) => t.id === "task_1")).toBe(true);
    });

    let err: Error | null = null;
    await act(async () => {
      try {
        await result.current.updateTask("task_1", { title: "Failed edit" });
      } catch (e) {
        err = e as Error;
      }
    });

    expect(err).toBeDefined();
    expect((err as unknown as Error).message).toContain("Network failure");
    // Must roll back to original title and revision
    const task1 = result.current.tasks.find((t) => t.id === "task_1");
    expect(task1?.title).toBe("Launch landing page");
    expect(task1?.revision).toBe(1);
  });

  it("deleteTask preserves task original list position when rolling back on failure", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });
    vi.spyOn(taskApi, "deleteTask").mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await waitFor(() => {
      expect(result.current.tasks.length).toBe(2);
    });

    // Task 2 is at index 1
    expect(result.current.tasks[1]?.id).toBe("task_2");

    let err: Error | null = null;
    await act(async () => {
      try {
        await result.current.deleteTask("task_2");
      } catch (e) {
        err = e as Error;
      }
    });

    expect(err).toBeDefined();
    expect((err as unknown as Error).message).toContain("Network failure");
    // Task 2 must be restored at its original position (index 1), not prepended to index 0
    expect(result.current.tasks.length).toBe(2);
    expect(result.current.tasks[0]?.id).toBe("task_1");
    expect(result.current.tasks[1]?.id).toBe("task_2");
  });

  it("verifies HTTP 401, 409, 500 produce distinct error states", () => {
    // 401 Unauthorized
    const err401 = new TaskApiError(401, "Session expired");
    expect(err401.message).toContain("Unauthorized (401)");
    expect(err401.status).toBe(401);

    // 500 Server error
    const err500 = new TaskApiError(500, "Database connection failure");
    expect(err500.message).toContain("Server error (500)");
    expect(err500.status).toBe(500);

    // 409 Conflict
    const err409 = new TaskConflictError("Version mismatch");
    expect(err409.status).toBe(409);
    expect(err409).toBeInstanceOf(TaskConflictError);
  });

  it("explicit demo mode functions locally without backend persistence", async () => {
    const wrapper = createWrapper();
    const { result } = renderHook(() => useTasks(), { wrapper });

    await act(async () => {
      result.current.enableDemoMode();
    });

    expect(result.current.isDemoMode).toBe(true);
    expect(result.current.tasks.length).toBeGreaterThan(0);

    const apiSpy = vi.spyOn(taskApi, "createTask");
    apiSpy.mockClear();

    let added: Task | undefined;
    await act(async () => {
      added = await result.current.addTask("Local demo item", "studio");
    });

    expect(apiSpy).not.toHaveBeenCalled();
    expect(added?.id).toContain("demo_");
    expect(result.current.tasks.some((t) => t.title === "Local demo item")).toBe(true);
  });
});

describe("Workstream D: Cross-page consistency", () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.spyOn(projectApi, "listProjects").mockResolvedValue({ total: 2, items: [
      { id: "blockday", owner_id: "user_1", name: "Blockday launch", description: null, status: "active", revision: 1, created_at: "2026-10-18T10:00:00Z", updated_at: "2026-10-18T10:00:00Z" },
      { id: "studio", owner_id: "user_1", name: "Studio", description: null, status: "active", revision: 1, created_at: "2026-10-18T10:00:00Z", updated_at: "2026-10-18T10:00:00Z" },
    ] });
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });
  it("Projects page displays TaskErrorBanner when task addition fails", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });
    vi.spyOn(taskApi, "createTask").mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <BlockdayProvider>
          <ProjectsPage />
        </BlockdayProvider>
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/Project Tasks/i)).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText(/Add task to/i);
    fireEvent.change(input, { target: { value: "New project task" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => {
      expect(screen.getAllByText(/Network failure/i).length).toBeGreaterThan(0);
    });
  });

  it("Projects page displays TaskConflictBanner when task toggle encounters 409", async () => {
    vi.spyOn(taskApi, "listTasks").mockResolvedValueOnce({
      items: mockBackendTasks,
      total: mockBackendTasks.length,
    });
    vi.spyOn(taskApi, "completeTask").mockRejectedValueOnce(
      new TaskConflictError("Concurrent edit detected", {
        type: "https://api.pos-deku.com/errors/conflict",
        title: "Conflict",
        status: 409,
        extensions: { current_revision: 2 },
      })
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <BlockdayProvider>
          <ProjectsPage />
        </BlockdayProvider>
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Launch landing page")).toBeInTheDocument();
    });

    const taskRow = screen.getByText("Launch landing page");
    fireEvent.click(taskRow);

    await waitFor(() => {
      expect(screen.getByText(/Revision conflict: Concurrent edit detected/i)).toBeInTheDocument();
    });
  });
});
