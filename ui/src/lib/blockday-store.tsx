import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { makeTodayTasks, type Task } from "./blockday-data";

export function morph(fn: () => void) {
  const d = document as Document & { startViewTransition?: (cb: () => void) => void };
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (d.startViewTransition && !reduce) d.startViewTransition(() => flushSync(fn));
  else fn();
}

export type TimerMode = "Focus" | "Break";
const lengths: Record<TimerMode, number> = { Focus: 25 * 60, Break: 5 * 60 };

type Store = {
  tasks: Task[];
  setTasks: (fn: (t: Task[]) => Task[]) => void;
  archived: string[];
  archive: (ids: string[]) => void;
  dayOffset: number;
  setDayOffset: (n: number) => void;
  today: Date | null;
  isDark: boolean;
  toggleTheme: () => void;
  query: string;
  setQuery: (q: string) => void;
  timer: { mode: TimerMode; seconds: number; total: number; running: boolean; sessions: number; taskId: string };
  setMode: (m: TimerMode) => void;
  toggleTimer: () => void;
  resetTimer: () => void;
  skipTimer: () => void;
  selectTask: (id: string) => void;
};

const Ctx = createContext<Store | null>(null);
const KEY = "blockday-v2";

export function BlockdayProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasksState] = useState<Task[]>(makeTodayTasks);
  const [archived, setArchived] = useState<string[]>([]);
  const [dayOffset, setDayOffset] = useState(0);
  const [today, setToday] = useState<Date | null>(null);
  const [isDark, setIsDark] = useState(false);
  const [query, setQuery] = useState("");
  const [mode, setModeState] = useState<TimerMode>("Focus");
  const [seconds, setSeconds] = useState(lengths.Focus);
  const [running, setRunning] = useState(false);
  const [sessions, setSessions] = useState(3);
  const [taskId, setTaskId] = useState("t1");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setToday(new Date());
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        const d = JSON.parse(raw) as { tasks?: Task[]; archived?: string[]; isDark?: boolean };
        if (d.tasks) setTasksState(d.tasks);
        if (d.archived) setArchived(d.archived);
        if (d.isDark) setIsDark(true);
      }
    } catch { localStorage.removeItem(KEY); }
    setLoaded(true);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", isDark);
    if (loaded) localStorage.setItem(KEY, JSON.stringify({ tasks, archived, isDark }));
  }, [tasks, archived, isDark, loaded]);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setSeconds((s) => {
      if (s <= 1) { setRunning(false); if (mode === "Focus") setSessions((n) => n + 1); return 0; }
      return s - 1;
    }), 1000);
    return () => window.clearInterval(id);
  }, [running, mode]);

  const setTasks = useCallback((fn: (t: Task[]) => Task[]) => setTasksState(fn), []);
  const setMode = useCallback((m: TimerMode) => { setModeState(m); setSeconds(lengths[m]); setRunning(false); }, []);

  const value = useMemo<Store>(() => ({
    tasks, setTasks, archived,
    archive: (ids) => setArchived((a) => [...a, ...ids]),
    dayOffset, setDayOffset, today, isDark,
    toggleTheme: () => setIsDark((v) => !v),
    query, setQuery,
    timer: { mode, seconds, total: lengths[mode], running, sessions, taskId },
    setMode,
    toggleTimer: () => setRunning((r) => !r),
    resetTimer: () => { setSeconds(lengths[mode]); setRunning(false); },
    skipTimer: () => { if (mode === "Focus") setSessions((n) => n + 1); setMode(mode === "Focus" ? "Break" : "Focus"); },
    selectTask: setTaskId,
  }), [tasks, setTasks, archived, dayOffset, today, isDark, query, mode, seconds, running, sessions, taskId, setMode]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useBlockday() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useBlockday must be used inside BlockdayProvider");
  return v;
}

export function formatClock(total: number) {
  return `${Math.floor(total / 60).toString().padStart(2, "0")}:${(total % 60).toString().padStart(2, "0")}`;
}

export function dayLabel(today: Date | null, offset: number, opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" }) {
  if (!today) return offset === 0 ? "Today" : `${offset} days`;
  const d = new Date(today); d.setDate(d.getDate() + offset);
  return d.toLocaleDateString("en-US", opts);
}
