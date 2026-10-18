import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { makeTodayTasks } from "./blockday-data";
import { taskApi, TaskConflictError } from "./task-api";
import {
  backendToTask,
  taskToCreatePayload,
  taskToUpdatePayload,
  type BackendTask,
  type Mood,
  type Task,
} from "./task-types";

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
      (err.message.includes("fetch") || err.message.includes("NetworkError") || err.message.includes("Failed"))
    ) {
      return new Error(`Network failure: Unable to reach server (${err.message})`);
    }
    return err;
  }
  return new Error(String(err));
}

export type Store = {
  tasks: Task[];
  setTasks: (fn: (t: Task[]) => Task[]) => void;
  addTask: (title: string, projectId?: string, estimate?: number, mood?: Mood) => Promise<Task | undefined>;
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
  timer: { mode: TimerMode; seconds: number; total: number; running: boolean; sessions: number; taskId: string };
  setMode: (m: TimerMode) => void;
  toggleTimer: () => void;
  resetTimer: () => void;
  skipTimer: () => void;
  selectTask: (id: string) => void;
};

const Ctx = createContext<Store | null>(null);
const PREFERENCES_KEY = "blockday-preferences-v1";

export function BlockdayProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  const [archived, setArchived] = useState<string[]>([]);
  const [dayOffset, setDayOffset] = useState(0);
  const [today, setToday] = useState<Date | null>(null);
  const [isDark, setIsDark] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("blockday");
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

  const enableDemoMode = useCallback(() => {
    setIsDemoMode(true);
    try {
      localStorage.setItem("blockday_demo_mode", "true");
    } catch {}
    clearError();
    clearConflict();
    queryClient.setQueryData<Task[]>(["tasks"], makeTodayTasks());
  }, [clearError, clearConflict, queryClient]);

  const disableDemoMode = useCallback(() => {
    setIsDemoMode(false);
    try {
      localStorage.removeItem("blockday_demo_mode");
    } catch {}
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
        JSON.stringify({ archived, isDark, selectedProjectId })
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
    [queryClient]
  );

  const addTask = useCallback(
    async (title: string, projectId = "blockday", estimate = 15, mood: Mood = "Clear") => {
      const trimmed = title.trim();
      if (!trimmed) return;
      clearError();
      clearConflict();

      if (isDemoMode) {
        const demoTask: Task = {
          id: `demo_${Date.now()}`,
          title: trimmed,
          projectId,
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

      const tempId = `t_${Date.now()}`;
      const optimisticTask: Task = {
        id: tempId,
        title: trimmed,
        projectId,
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
          projectId,
          estimate,
          mood,
        });
        const created = await taskApi.createTask(payload);
        const serverTask = backendToTask(created, optimisticTask);
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === tempId ? serverTask : t))
        );
        return serverTask;
      } catch (err: unknown) {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.filter((t) => t.id !== tempId)
        );
        const errorObj = normalizeError(err);
        setMutationError(errorObj);
        throw errorObj;
      }
    },
    [queryClient, isDemoMode, clearError, clearConflict]
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
            old.map((t) => (t.id === id ? demoTask : t))
          );
        });
        return demoTask;
      }

      morph(() => {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? { ...t, done: nextDone } : t))
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
          old.map((t) => (t.id === id ? serverTask : t))
        );
        return serverTask;
      } catch (err: unknown) {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? { ...t, done: previousDone } : t))
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
    [queryClient, tasks, isDemoMode, clearError, clearConflict]
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
            old.map((t) => (t.id === id ? demoTask : t))
          );
        });
        return demoTask;
      }

      morph(() => {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? { ...t, ...updates } : t))
        );
      });

      try {
        const payload = taskToUpdatePayload(updates, target);
        const updated = await taskApi.updateTask(id, payload, target.revision);
        const serverTask = backendToTask(updated, { ...target, ...updates });
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? serverTask : t))
        );
        return serverTask;
      } catch (err: unknown) {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.map((t) => (t.id === id ? target : t))
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
    [queryClient, tasks, isDemoMode, clearError, clearConflict]
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
          queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
            old.filter((t) => t.id !== id)
          );
        });
        return;
      }

      morph(() => {
        queryClient.setQueryData<Task[]>(["tasks"], (old = []) =>
          old.filter((t) => t.id !== id)
        );
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
    [queryClient, tasks, isDemoMode, clearError, clearConflict]
  );

  const setMode = useCallback((m: TimerMode) => {
    setModeState(m);
    setSeconds(lengths[m]);
    setRunning(false);
  }, []);

  const value = useMemo<Store>(
    () => ({
      tasks,
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
    }),
    [
      tasks,
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
    ]
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
  opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" }
) {
  if (!today) return offset === 0 ? "Today" : `${offset} days`;
  const d = new Date(today);
  d.setDate(d.getDate() + offset);
  return d.toLocaleDateString("en-US", opts);
}
