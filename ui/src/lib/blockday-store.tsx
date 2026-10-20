import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { flushSync } from "react-dom";
import { makeTodayTasks } from "./blockday-data";
import { taskApi, TaskConflictError } from "./task-api";
import { projectApi } from "./project-api";
import type { BackendProject, ProjectCreatePayload, ProjectUpdatePayload } from "./project-types";
import {
  backendToTask,
  taskToCreatePayload,
  taskToUpdatePayload,
  type BackendTask,
  type Mood,
  type Task,
} from "./task-types";
import { getCognitoAccessToken } from "./auth";
import { IndexedDbOfflineOperationQueue } from "./offline-queue";
import {
  databaseNameForAccessToken,
  IndexedDbSyncPersistence,
  SyncCoordinator,
  type SyncCoordinatorState,
  type SyncStatus,
} from "./sync-coordinator";
import { syncApi } from "./sync-api";
import { OfflineMutationBoundary } from "./offline-mutations";

export function morph(fn: () => void) {
  const d = document as Document & { startViewTransition?: (cb: () => void) => void };
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (d.startViewTransition && !reduce) d.startViewTransition(() => flushSync(fn));
  else fn();
}

export type TimerMode = "Focus" | "Break";
const lengths: Record<TimerMode, number> = { Focus: 25 * 60, Break: 5 * 60 };

export function normalizeError(err: unknown): Error {
  if (err instanceof Error) {
    if (
      err instanceof TypeError &&
      (err.message.includes("fetch") ||
        err.message.includes("NetworkError") ||
        err.message.includes("Failed"))
    ) {
      return new Error(`Network failure: Unable to reach server (${err.message})`);
    }
    return err;
  }
  return new Error(String(err));
}

export type Store = {
  tasks: Task[];
  projects: BackendProject[];
  projectsLoading: boolean;
  projectsError: Error | null;
  createProject: (payload: ProjectCreatePayload) => Promise<BackendProject>;
  updateProject: (
    id: string,
    payload: ProjectUpdatePayload,
    revision?: number,
  ) => Promise<BackendProject>;
  deleteProject: (id: string) => Promise<void>;
  setTasks: (fn: (t: Task[]) => Task[]) => void;
  addTask: (
    title: string,
    projectId?: string,
    estimate?: number,
    mood?: Mood,
  ) => Promise<Task | undefined>;
  toggleTask: (id: string) => Promise<Task | undefined>;
  updateTask: (id: string, updates: Partial<Task>) => Promise<Task | undefined>;
  deleteTask: (id: string) => Promise<void>;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  conflictError: TaskConflictError | null;
  clearConflict: () => void;
  clearError: () => void;
  refetchTasks: () => void;
  isDemoMode: boolean;
  enableDemoMode: () => void;
  disableDemoMode: () => void;
  /** Local presentation preference (task IDs hidden on this device only; not persisted to backend). */
  archived: string[];
  archive: (ids: string[]) => void;
  dayOffset: number;
  setDayOffset: (n: number) => void;
  today: Date | null;
  isDark: boolean;
  toggleTheme: () => void;
  query: string;
  setQuery: (q: string) => void;
  selectedProjectId: string;
  setSelectedProjectId: (id: string) => void;
  moodFilter: Mood | "All";
  setMoodFilter: (m: Mood | "All") => void;
  timer: {
    mode: TimerMode;
    seconds: number;
    total: number;
    running: boolean;
    sessions: number;
    taskId: string;
  };
  setMode: (m: TimerMode) => void;
  toggleTimer: () => void;
  resetTimer: () => void;
  skipTimer: () => void;
  selectTask: (id: string) => void;
  coordinator: SyncCoordinator | null;
  mutationBoundary: OfflineMutationBoundary | null;
  syncStatus: SyncStatus;
  syncError: string | null;
};

export interface BlockdayProviderProps {
  children: ReactNode;
  initialMutationBoundary?: OfflineMutationBoundary | null;
  initialCoordinator?: SyncCoordinator | null;
}

const Ctx = createContext<Store | null>(null);

export function useOptionalBlockday(): Store | null {
  return useContext(Ctx);
}

const PREFERENCES_KEY = "blockday-preferences-v1";

export function BlockdayProvider({
  children,
  initialMutationBoundary,
  initialCoordinator,
}: BlockdayProviderProps) {
  const queryClient = useQueryClient();

  const [activeCoordinator, setActiveCoordinator] = useState<SyncCoordinator | null>(
    initialCoordinator ?? null,
  );
  const [activeMutationBoundary, setActiveMutationBoundary] =
    useState<OfflineMutationBoundary | null>(initialMutationBoundary ?? null);
  const [syncState, setSyncState] = useState<SyncCoordinatorState>({
    status: "synced",
    error: null,
  });

  const [archived, setArchived] = useState<string[]>([]);
  const [dayOffset, setDayOffset] = useState(0);
  const [today, setToday] = useState<Date | null>(null);
  const [isDark, setIsDark] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [moodFilter, setMoodFilter] = useState<Mood | "All">("All");
  const [mode, setModeState] = useState<TimerMode>("Focus");
  const [seconds, setSeconds] = useState(lengths.Focus);
  const [running, setRunning] = useState(false);
  const [sessions, setSessions] = useState(3);
  const [taskId, setTaskId] = useState("t1");
  const [loaded, setLoaded] = useState(false);

  const [mutationError, setMutationError] = useState<Error | null>(null);
  const [conflictError, setConflictError] = useState<TaskConflictError | null>(null);

  const [isDemoMode, setIsDemoMode] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      const urlParams = new URLSearchParams(window.location.search);
      if (urlParams.get("demo") === "true") return true;
      return localStorage.getItem("blockday_demo_mode") === "true";
    } catch {
      return false;
    }
  });

  const clearConflict = useCallback(() => setConflictError(null), []);
  const clearError = useCallback(() => setMutationError(null), []);

  const token = getCognitoAccessToken();
  const syncEnabled = Boolean(token) && !isDemoMode;

  useEffect(() => {
    if (initialMutationBoundary || initialCoordinator) {
      if (initialCoordinator) setActiveCoordinator(initialCoordinator);
      if (initialMutationBoundary) setActiveMutationBoundary(initialMutationBoundary);
      return;
    }
    if (!syncEnabled || !token) {
      setActiveCoordinator(null);
      setActiveMutationBoundary(null);
      setSyncState({ status: "synced", error: null });
      return;
    }

    let disposed = false;
    let queue: IndexedDbOfflineOperationQueue | null = null;
    let persistence: IndexedDbSyncPersistence | null = null;
    let coordinator: SyncCoordinator | null = null;
    let handleOnline: (() => void) | null = null;

    void databaseNameForAccessToken(token)
      .then((databaseName) => {
        if (disposed) return;
        queue = new IndexedDbOfflineOperationQueue({
          database_name: databaseName,
          max_batch_size: 100,
        });
        persistence = new IndexedDbSyncPersistence(databaseName);
        coordinator = new SyncCoordinator(
          queue,
          persistence,
          syncApi,
          (next) => {
            if (!disposed) setSyncState(next);
          },
          100,
          (items) => {
            if (items.some((item) => item.entity_type === "task")) {
              void queryClient.invalidateQueries({ queryKey: ["tasks"] });
            }
            if (items.some((item) => item.entity_type === "project")) {
              void queryClient.invalidateQueries({ queryKey: ["projects"] });
            }
          },
          (entry, ack) => {
            const op = entry.operation;
            if (!op) return;
            if (op.entity_type === "task") {
              queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
                old.map((t) => {
                  if (t.id === op.entity_id) {
                    if (ack.status === "applied" || ack.status === "duplicate") {
                      return {
                        ...t,
                        revision: ack.revision ?? t.revision,
                        syncStatus: "synced",
                        conflictId: null,
                      };
                    } else if (ack.status === "conflicted") {
                      return {
                        ...t,
                        syncStatus: "conflict",
                        conflictId: ack.conflict_id,
                      };
                    } else if (ack.status === "permanent") {
                      return { ...t, syncStatus: "failed" };
                    }
                  }
                  return t;
                }),
              );
              if (ack.status === "conflicted") {
                setConflictError(
                  new TaskConflictError(ack.message || "Revision conflict detected", {
                    type: "https://api.pos-deku.com/errors/sync/stale_revision",
                    title: "Revision conflict",
                    status: 409,
                    detail: ack.message || "Revision conflict detected",
                    extensions: {
                      current_revision: ack.revision ?? undefined,
                    },
                  }),
                );
              }
            } else if (op.entity_type === "project") {
              queryClient.setQueryData<BackendProject[]>(["projects"], (old = []) =>
                old.map((p) => {
                  if (p.id === op.entity_id) {
                    if (ack.status === "applied" || ack.status === "duplicate") {
                      return {
                        ...p,
                        revision: ack.revision ?? p.revision,
                        syncStatus: "synced",
                        conflictId: null,
                      };
                    } else if (ack.status === "conflicted") {
                      return {
                        ...p,
                        syncStatus: "conflict",
                        conflictId: ack.conflict_id,
                      };
                    } else if (ack.status === "permanent") {
                      return { ...p, syncStatus: "failed" };
                    }
                  }
                  return p;
                }),
              );
            }
          },
        );

        const boundary = new OfflineMutationBoundary(queue, coordinator, persistence);
        setActiveCoordinator(coordinator);
        setActiveMutationBoundary(boundary);

        handleOnline = () => {
          void coordinator?.syncNow().catch(() => {});
        };
        if (typeof window !== "undefined") {
          window.addEventListener("online", handleOnline);
          (window as unknown as { __posCoordinator?: unknown }).__posCoordinator = coordinator;
        }

        void queryClient.invalidateQueries({ queryKey: ["tasks"] });
        void queryClient.invalidateQueries({ queryKey: ["projects"] });
      })
      .catch((error: unknown) => {
        if (!disposed) {
          setSyncState({
            status: "failed",
            error: error instanceof Error ? error.message : String(error),
          });
        }
      });

    return () => {
      disposed = true;
      if (typeof window !== "undefined" && handleOnline) {
        window.removeEventListener("online", handleOnline);
        delete (window as unknown as { __posCoordinator?: unknown }).__posCoordinator;
      }
      queue?.close();
      persistence?.close();
    };
  }, [syncEnabled, token, initialMutationBoundary, initialCoordinator, queryClient]);

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: async (): Promise<BackendProject[]> => {
      if (isDemoMode) {
        return [];
      }
      if (activeMutationBoundary) {
        return activeMutationBoundary.loadProjects();
      }
      return (await projectApi.listProjects()).items;
    },
    enabled: !syncEnabled || Boolean(activeMutationBoundary) || syncState.status === "failed",
    retry: false,
  });
  const projects = isDemoMode ? [] : (projectsQuery.data ?? []);

  useEffect(() => {
    if (isDemoMode || !projects.length) return;
    if (!projects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(projects[0]!.id);
    }
  }, [isDemoMode, projects, selectedProjectId]);

  const createProject = useCallback(
    async (payload: ProjectCreatePayload) => {
      clearError();
      clearConflict();
      if (activeMutationBoundary) {
        try {
          const { project: created } = await activeMutationBoundary.enqueueProjectCreate(payload);
          queryClient.setQueryData<BackendProject[]>(["projects"], (old = []) => [...old, created]);
          setSelectedProjectId(created.id);
          return created;
        } catch (err: unknown) {
          const errorObj = normalizeError(err);
          setMutationError(errorObj);
          throw errorObj;
        }
      }
      const created = await projectApi.createProject(payload);
      queryClient.setQueryData<BackendProject[]>(["projects"], (old = []) => [...old, created]);
      setSelectedProjectId(created.id);
      return created;
    },
    [queryClient, activeMutationBoundary, clearError, clearConflict],
  );

  const updateProject = useCallback(
    async (id: string, payload: ProjectUpdatePayload, revision?: number) => {
      clearError();
      clearConflict();
      const currentProjects = queryClient.getQueryData<BackendProject[]>(["projects"]) ?? projects;
      const target = currentProjects.find((p) => p.id === id);

      if (activeMutationBoundary && target) {
        try {
          const { project: updated } = await activeMutationBoundary.enqueueProjectUpdate(
            target,
            payload,
            revision,
          );
          queryClient.setQueryData<BackendProject[]>(["projects"], (old = []) =>
            old.map((p) => (p.id === id ? updated : p)),
          );
          return updated;
        } catch (err: unknown) {
          const errorObj = normalizeError(err);
          setMutationError(errorObj);
          throw errorObj;
        }
      }
      const updated = await projectApi.updateProject(id, payload, revision);
      queryClient.setQueryData<BackendProject[]>(["projects"], (old = []) =>
        old.map((p) => (p.id === id ? updated : p)),
      );
      return updated;
    },
    [queryClient, projects, activeMutationBoundary, clearError, clearConflict],
  );

  const deleteProject = useCallback(
    async (id: string) => {
      clearError();
      clearConflict();
      const currentProjects = queryClient.getQueryData<BackendProject[]>(["projects"]) ?? projects;
      const target = currentProjects.find((p) => p.id === id);

      if (activeMutationBoundary && target) {
        try {
          await activeMutationBoundary.enqueueProjectDelete(target);
          queryClient.setQueryData<BackendProject[]>(["projects"], (old = []) =>
            old.filter((p) => p.id !== id),
          );
          if (selectedProjectId === id) setSelectedProjectId("");
          return;
        } catch (err: unknown) {
          const errorObj = normalizeError(err);
          setMutationError(errorObj);
          throw errorObj;
        }
      }
      await projectApi.deleteProject(id);
      queryClient.setQueryData<BackendProject[]>(["projects"], (old = []) =>
        old.filter((p) => p.id !== id),
      );
      if (selectedProjectId === id) setSelectedProjectId("");
    },
    [queryClient, projects, selectedProjectId, activeMutationBoundary, clearError, clearConflict],
  );

  const enableDemoMode = useCallback(() => {
    setIsDemoMode(true);
    try {
      localStorage.setItem("blockday_demo_mode", "true");
    } catch {
      // ignore local storage errors
    }
    clearError();
    clearConflict();
    queryClient.setQueryData<Task[]>(["tasks"], makeTodayTasks());
  }, [clearError, clearConflict, queryClient]);

  const disableDemoMode = useCallback(() => {
    setIsDemoMode(false);
    try {
      localStorage.removeItem("blockday_demo_mode");
    } catch {
      // ignore local storage errors
    }
    clearError();
    clearConflict();
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
  }, [clearError, clearConflict, queryClient]);

  // Canonical query for tasks
  const {
    data: tasks = [],
    isLoading,
    isError: isQueryError,
    error: queryError,
    refetch,
  } = useQuery({
    queryKey: ["tasks"],
    queryFn: async (): Promise<Task[]> => {
      if (isDemoMode) {
        return makeTodayTasks();
      }
      if (activeMutationBoundary) {
        return activeMutationBoundary.loadTasks();
      }
      try {
        const res = await taskApi.listTasks();
        if (res?.items && Array.isArray(res.items)) {
          return res.items.map((bt) => backendToTask(bt));
        }
        return [];
      } catch (err: unknown) {
        throw normalizeError(err);
      }
    },
    enabled: !syncEnabled || Boolean(activeMutationBoundary) || syncState.status === "failed",
    staleTime: 0,
    retry: false,
  });

  useEffect(() => {
    setToday(new Date());
    try {
      const raw = localStorage.getItem(PREFERENCES_KEY);
      if (raw) {
        const d = JSON.parse(raw) as {
          archived?: string[];
          isDark?: boolean;
          selectedProjectId?: string;
        };
        if (d.archived) setArchived(d.archived);
        if (d.isDark) setIsDark(true);
        if (d.selectedProjectId) setSelectedProjectId(d.selectedProjectId);
      }
    } catch {
      localStorage.removeItem(PREFERENCES_KEY);
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", isDark);
    if (loaded) {
      localStorage.setItem(
        PREFERENCES_KEY,
        JSON.stringify({ archived, isDark, selectedProjectId }),
      );
    }
  }, [archived, isDark, selectedProjectId, loaded]);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      setSeconds((s) => {
        if (s <= 1) {
          setRunning(false);
          if (mode === "Focus") setSessions((n) => n + 1);
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [running, mode]);

  const setTasks = useCallback(
    (fn: (t: Task[]) => Task[]) => {
      queryClient.setQueryData<Task[]>(["tasks"], (old = []) => fn(old));
    },
    [queryClient],
  );

  const addTask = useCallback(
    async (
      title: string,
      projectId = selectedProjectId || undefined,
      estimate = 15,
      mood: Mood = "Clear",
    ) => {
      const trimmed = title.trim();
      if (!trimmed) return;
      clearError();
      clearConflict();

      if (isDemoMode) {
        const demoTask: Task = {
          id: `demo_${Date.now()}`,
          title: trimmed,
          projectId: projectId ?? "unassigned",
          dueIn: 0,
          estimate,
          mood,
          done: false,
          revision: 1,
        };
        morph(() => {
          queryClient.setQueryData<Task[]>(["tasks"], (old = []) => [demoTask, ...old]);
        });
        return demoTask;
      }

      if (activeMutationBoundary) {
        try {
          const { task: createdTask } = await activeMutationBoundary.enqueueTaskCreate({
            title: trimmed,
            projectId,
            estimate,
            mood,
          });
          queryClient.setQueryData<Task[]>(["tasks"], (old = []) => [createdTask, ...old]);
          return createdTask;
        } catch (err: unknown) {
          const errorObj = normalizeError(err);
          setMutationError(errorObj);
          throw errorObj;
        }
      }

      const tempId = `t_${Date.now()}`;
      const optimisticTask: Task = {
        id: tempId,
        title: trimmed,
        projectId: projectId ?? "unassigned",
        dueIn: 0,
        estimate,
        mood,
        done: false,
        revision: 1,
      };

      morph(() => {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) => [optimisticTask, ...old]);
      });

      try {
        const payload = taskToCreatePayload({
          title: trimmed,
          ...(projectId ? { projectId } : {}),
          estimate,
          mood,
        });
        const created = await taskApi.createTask(payload);
        const serverTask = backendToTask(created, optimisticTask);
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === tempId ? serverTask : t)),
        );
        return serverTask;
      } catch (err: unknown) {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.filter((t) => t.id !== tempId),
        );
        const errorObj = normalizeError(err);
        setMutationError(errorObj);
        throw errorObj;
      }
    },
    [queryClient, isDemoMode, activeMutationBoundary, clearError, clearConflict, selectedProjectId],
  );

  const toggleTask = useCallback(
    async (id: string) => {
      clearError();
      clearConflict();

      const currentTasks = queryClient.getQueryData<Task[]>(["tasks"]) ?? tasks;
      const target = currentTasks.find((t) => t.id === id);
      if (!target) return;

      const nextDone = !target.done;
      const previousDone = target.done;

      if (isDemoMode) {
        const demoTask: Task = { ...target, done: nextDone };
        morph(() => {
          queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
            old.map((t) => (t.id === id ? demoTask : t)),
          );
        });
        return demoTask;
      }

      if (activeMutationBoundary) {
        try {
          const { task: updatedTask } = await activeMutationBoundary.enqueueTaskToggle(target);
          queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
            old.map((t) => (t.id === id ? updatedTask : t)),
          );
          return updatedTask;
        } catch (err: unknown) {
          const errorObj = normalizeError(err);
          setMutationError(errorObj);
          throw errorObj;
        }
      }

      morph(() => {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? { ...t, done: nextDone } : t)),
        );
      });

      try {
        let updated: BackendTask;
        if (nextDone) {
          updated = await taskApi.completeTask(id, target.revision);
        } else {
          updated = await taskApi.reopenTask(id, target.revision);
        }
        const serverTask = backendToTask(updated, { ...target, done: nextDone });
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? serverTask : t)),
        );
        return serverTask;
      } catch (err: unknown) {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? { ...t, done: previousDone } : t)),
        );

        if (err instanceof TaskConflictError) {
          setConflictError(err);
          void queryClient.invalidateQueries({ queryKey: ["tasks"] });
          throw err;
        }

        const errorObj = normalizeError(err);
        setMutationError(errorObj);
        throw errorObj;
      }
    },
    [queryClient, tasks, isDemoMode, activeMutationBoundary, clearError, clearConflict],
  );

  const updateTask = useCallback(
    async (id: string, updates: Partial<Task>) => {
      clearError();
      clearConflict();

      const currentTasks = queryClient.getQueryData<Task[]>(["tasks"]) ?? tasks;
      const target = currentTasks.find((t) => t.id === id);
      if (!target) return;

      if (isDemoMode) {
        const demoTask: Task = { ...target, ...updates };
        morph(() => {
          queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
            old.map((t) => (t.id === id ? demoTask : t)),
          );
        });
        return demoTask;
      }

      if (activeMutationBoundary) {
        try {
          const { task: updatedTask } = await activeMutationBoundary.enqueueTaskUpdate(
            target,
            updates,
          );
          queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
            old.map((t) => (t.id === id ? updatedTask : t)),
          );
          return updatedTask;
        } catch (err: unknown) {
          const errorObj = normalizeError(err);
          setMutationError(errorObj);
          throw errorObj;
        }
      }

      morph(() => {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? { ...t, ...updates } : t)),
        );
      });

      try {
        const payload = taskToUpdatePayload(updates, target);
        const updated = await taskApi.updateTask(id, payload, target.revision);
        const serverTask = backendToTask(updated, { ...target, ...updates });
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? serverTask : t)),
        );
        return serverTask;
      } catch (err: unknown) {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? target : t)),
        );

        if (err instanceof TaskConflictError) {
          setConflictError(err);
          void queryClient.invalidateQueries({ queryKey: ["tasks"] });
          throw err;
        }

        const errorObj = normalizeError(err);
        setMutationError(errorObj);
        throw errorObj;
      }
    },
    [queryClient, tasks, isDemoMode, activeMutationBoundary, clearError, clearConflict],
  );

  const deleteTask = useCallback(
    async (id: string) => {
      clearError();
      clearConflict();

      const currentTasks = queryClient.getQueryData<Task[]>(["tasks"]) ?? tasks;
      const targetIndex = currentTasks.findIndex((t) => t.id === id);
      const target = targetIndex !== -1 ? currentTasks[targetIndex] : undefined;
      if (!target) return;

      if (isDemoMode) {
        morph(() => {
          queryClient.setQueryData<Task[]>(["tasks"], (old = []) => old.filter((t) => t.id !== id));
        });
        return;
      }

      if (activeMutationBoundary) {
        try {
          await activeMutationBoundary.enqueueTaskDelete(target);
          queryClient.setQueryData<Task[]>(["tasks"], (old = []) => old.filter((t) => t.id !== id));
          return;
        } catch (err: unknown) {
          const errorObj = normalizeError(err);
          setMutationError(errorObj);
          throw errorObj;
        }
      }

      morph(() => {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) => old.filter((t) => t.id !== id));
      });

      try {
        await taskApi.deleteTask(id, target.revision);
      } catch (err: unknown) {
        // Rollback: restore task at its original list position
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) => {
          if (old.some((t) => t.id === target.id)) return old;
          const next = [...old];
          const insertAt = Math.min(Math.max(0, targetIndex), next.length);
          next.splice(insertAt, 0, target);
          return next;
        });

        if (err instanceof TaskConflictError) {
          setConflictError(err);
          void queryClient.invalidateQueries({ queryKey: ["tasks"] });
          throw err;
        }

        const errorObj = normalizeError(err);
        setMutationError(errorObj);
        throw errorObj;
      }
    },
    [queryClient, tasks, isDemoMode, activeMutationBoundary, clearError, clearConflict],
  );

  const setMode = useCallback((m: TimerMode) => {
    setModeState(m);
    setSeconds(lengths[m]);
    setRunning(false);
  }, []);

  const value = useMemo<Store>(
    () => ({
      tasks,
      projects,
      projectsLoading: !isDemoMode && projectsQuery.isLoading,
      projectsError: projectsQuery.error as Error | null,
      createProject,
      updateProject,
      deleteProject,
      setTasks,
      addTask,
      toggleTask,
      updateTask,
      deleteTask,
      isLoading,
      isError: isQueryError || Boolean(mutationError),
      error: mutationError || (queryError as Error | null),
      conflictError,
      clearConflict,
      clearError,
      refetchTasks: () => void refetch(),
      isDemoMode,
      enableDemoMode,
      disableDemoMode,
      archived,
      archive: (ids) => setArchived((a) => [...a, ...ids]),
      dayOffset,
      setDayOffset,
      today,
      isDark,
      toggleTheme: () => setIsDark((v) => !v),
      query,
      setQuery,
      selectedProjectId,
      setSelectedProjectId,
      moodFilter,
      setMoodFilter,
      timer: { mode, seconds, total: lengths[mode], running, sessions, taskId },
      setMode,
      toggleTimer: () => setRunning((r) => !r),
      resetTimer: () => {
        setSeconds(lengths[mode]);
        setRunning(false);
      },
      skipTimer: () => {
        if (mode === "Focus") setSessions((n) => n + 1);
        setMode(mode === "Focus" ? "Break" : "Focus");
      },
      selectTask: setTaskId,
      coordinator: activeCoordinator,
      mutationBoundary: activeMutationBoundary,
      syncStatus: syncState.status,
      syncError: syncState.error,
    }),
    [
      tasks,
      projects,
      projectsQuery.isLoading,
      projectsQuery.error,
      createProject,
      updateProject,
      deleteProject,
      setTasks,
      addTask,
      toggleTask,
      updateTask,
      deleteTask,
      isLoading,
      isQueryError,
      mutationError,
      queryError,
      conflictError,
      clearConflict,
      clearError,
      refetch,
      isDemoMode,
      enableDemoMode,
      disableDemoMode,
      archived,
      dayOffset,
      today,
      isDark,
      query,
      selectedProjectId,
      moodFilter,
      mode,
      seconds,
      running,
      sessions,
      taskId,
      setMode,
      activeCoordinator,
      activeMutationBoundary,
      syncState,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useBlockday() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useBlockday must be used inside BlockdayProvider");
  return v;
}

export function useTasks() {
  const {
    tasks,
    addTask,
    toggleTask,
    updateTask,
    deleteTask,
    isLoading,
    isError,
    error,
    conflictError,
    clearConflict,
    clearError,
    refetchTasks,
    isDemoMode,
    enableDemoMode,
    disableDemoMode,
  } = useBlockday();

  return {
    tasks,
    addTask,
    toggleTask,
    updateTask,
    deleteTask,
    isLoading,
    isError,
    error,
    conflictError,
    clearConflict,
    clearError,
    refetchTasks,
    isDemoMode,
    enableDemoMode,
    disableDemoMode,
  };
}

export function formatClock(total: number) {
  return `${Math.floor(total / 60)
    .toString()
    .padStart(2, "0")}:${(total % 60).toString().padStart(2, "0")}`;
}

export function dayLabel(
  today: Date | null,
  offset: number,
  opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" },
) {
  if (!today) return offset === 0 ? "Today" : `${offset} days`;
  const d = new Date(today);
  d.setDate(d.getDate() + offset);
  return d.toLocaleDateString("en-US", opts);
}
