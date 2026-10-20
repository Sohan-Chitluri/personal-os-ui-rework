import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ChevronRight,
  Moon,
  RefreshCw,
  Search,
  Sun,
  X,
} from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import {
  dailyCompletions,
  deadline,
  projectById,
  type Mood,
  type Task,
  type Tone,
} from "@/lib/blockday-data";
import { dayLabel, morph, useBlockday } from "@/lib/blockday-store";
import { beginCognitoLogout, getCognitoAccessToken, isCognitoConfigured } from "@/lib/auth";

export const toneBg: Record<Tone, string> = {
  red: "bg-block-red",
  blue: "bg-block-blue",
  yellow: "bg-block-yellow",
  neutral: "bg-canvas",
};
export const toneText: Record<Tone, string> = {
  red: "text-on-primary",
  blue: "text-on-primary",
  yellow: "text-block-ink",
  neutral: "text-foreground",
};

export function IconButton({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`icon-button ${className}`} {...props} />;
}

export function SectionLabel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <span className={`section-label ${className}`}>{children}</span>;
}

export function GridPanel({
  children,
  className = "",
  label,
  aside,
}: {
  children: ReactNode;
  className?: string;
  label?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className={`grid-panel animate-slide-panel ${className}`}>
      {label && (
        <div className="mb-4 flex items-center justify-between gap-3">
          <SectionLabel>{label}</SectionLabel>
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

export function StatBlock({
  value,
  label,
  tone = "neutral",
  className = "",
}: {
  value: ReactNode;
  label: string;
  tone?: Tone;
  className?: string;
}) {
  return (
    <div className={`stat-block ${toneBg[tone]} ${toneText[tone]} ${className}`}>
      <p className="font-display text-4xl leading-none tabular-nums">{value}</p>
      <p className="mt-2 text-[9px] font-black uppercase tracking-[0.18em]">{label}</p>
    </div>
  );
}

export function DeadlineStrip({ dueIn }: { dueIn: number | null }) {
  const d = deadline(dueIn);
  return <span aria-hidden className={`deadline-strip deadline-${d.kind}`} />;
}

export interface TaskRowProps {
  task: Task;
  onToggle?: (() => void) | undefined;
  onSelect?: (() => void) | undefined;
  isSelected?: boolean;
  compact?: boolean;
  className?: string;
}

export function TaskRow({
  task,
  onToggle,
  onSelect,
  isSelected,
  compact,
  className = "",
}: TaskRowProps) {
  const project = projectById(task.projectId);
  const d = deadline(task.dueIn);
  return (
    <article
      style={{ viewTransitionName: `task-${task.id}` }}
      onClick={onSelect}
      className={`task-row ${task.done ? "task-done" : ""} ${isSelected ? "task-selected border-l-4 border-grid font-bold" : ""} ${compact ? "min-h-0 py-2" : ""} ${onSelect ? "cursor-pointer" : ""} ${className}`}
    >
      <DeadlineStrip dueIn={task.dueIn} />
      {onToggle && (
        <button
          type="button"
          aria-label={task.done ? `Mark ${task.title} incomplete` : `Complete ${task.title}`}
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          className="check-button"
        >
          {task.done && <Check />}
        </button>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {task.frog && !task.done && <span className="frog-label">Eat the frog</span>}
          <h2 className={`truncate ${compact ? "text-xs" : "text-[16px] font-bold"}`}>
            {task.title}
          </h2>
        </div>
        <div className="mt-1 flex items-center gap-3 text-[9px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
          {project && (
            <span className="flex items-center gap-1.5">
              <span className={`project-swatch ${toneBg[project.tone]}`} />
              {project.name}
            </span>
          )}
          <span>{task.estimate} min</span>
          {!compact && <span>{task.mood}</span>}
          {task.revision > 1 && (
            <span className="font-mono text-[8px] opacity-60">r{task.revision}</span>
          )}
          {task.syncStatus && task.syncStatus !== "synced" && (
            <span
              className={`px-1 py-0.5 text-[8px] font-mono uppercase tracking-wider ${
                task.syncStatus === "conflict" || task.syncStatus === "failed"
                  ? "bg-block-red text-white"
                  : task.syncStatus === "syncing"
                    ? "bg-block-blue text-white"
                    : "bg-block-yellow text-black"
              }`}
              aria-label={`Sync status: ${task.syncStatus}`}
            >
              {task.syncStatus === "pending"
                ? "Queued"
                : task.syncStatus === "syncing"
                  ? "Syncing"
                  : task.syncStatus === "conflict"
                    ? "Conflict"
                    : "Failed"}
            </span>
          )}
        </div>
      </div>
      {!task.done && !compact && (
        <span className={`deadline-label deadline-label-${d.kind}`}>{d.label}</span>
      )}
    </article>
  );
}

export function DemoModeBanner({ onExit }: { onExit?: () => void }) {
  return (
    <aside
      aria-label="Demo mode notice"
      className="mb-3 flex items-center justify-between border-2 border-block-blue bg-block-blue/15 p-2 text-xs font-bold text-foreground"
    >
      <div className="flex items-center gap-2">
        <span className="bg-block-blue px-1.5 py-0.5 text-[8.5px] font-black uppercase tracking-wider text-on-primary shrink-0">
          Demo Mode
        </span>
        <span className="text-muted-foreground">
          Local only: sample data in use. Changes are not persisted to the backend.
        </span>
      </div>
      {onExit && (
        <button
          onClick={onExit}
          className="border border-grid bg-canvas px-2 py-0.5 text-[9px] font-black uppercase tracking-wider hover:bg-block-ink hover:text-canvas transition-colors"
        >
          Exit Demo
        </button>
      )}
    </aside>
  );
}

export function TaskConflictBanner({
  message,
  onResolve,
  onDismiss,
}: {
  message: string;
  onResolve?: () => void;
  onDismiss?: () => void;
}) {
  return (
    <aside
      aria-label="Task conflict alert"
      className="mb-3 flex items-center justify-between border-2 border-block-yellow bg-block-yellow/20 p-2 text-xs font-bold text-foreground"
    >
      <div className="flex items-center gap-2">
        <AlertTriangle className="size-4 shrink-0 text-block-yellow" />
        <span className="bg-block-yellow px-1.5 py-0.5 text-[8.5px] font-black uppercase tracking-wider text-block-ink shrink-0">
          Conflict 409
        </span>
        <span>{message}</span>
      </div>
      <div className="flex items-center gap-2">
        {onResolve && (
          <button
            onClick={onResolve}
            className="flex items-center gap-1 border border-grid bg-canvas px-2 py-0.5 text-[9px] font-black uppercase tracking-wider hover:bg-block-ink hover:text-canvas"
          >
            <RefreshCw className="size-2.5" /> Refresh
          </button>
        )}
        {onDismiss && (
          <button
            onClick={onDismiss}
            aria-label="Dismiss conflict alert"
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
    </aside>
  );
}

export function TaskErrorBanner({
  message,
  onDismiss,
  onEnableDemo,
}: {
  message: string;
  onDismiss?: () => void;
  onEnableDemo?: () => void;
}) {
  const isNetwork =
    message.toLowerCase().includes("fetch") ||
    message.toLowerCase().includes("network") ||
    message.toLowerCase().includes("offline");
  const isAuth = message.toLowerCase().includes("unauthorized") || message.includes("401");
  const isServer = message.toLowerCase().includes("server error") || message.includes("500");

  let badge = "Error";
  if (isNetwork) badge = "Network Offline";
  else if (isAuth) badge = "Auth 401";
  else if (isServer) badge = "Server 500";

  return (
    <aside
      aria-label="Task error notice"
      className="mb-3 flex items-center justify-between border-2 border-block-red bg-block-red/15 p-2 text-xs font-bold text-foreground"
    >
      <div className="flex items-center gap-2 min-w-0">
        <AlertTriangle className="size-4 shrink-0 text-block-red" />
        <span className="bg-block-red px-1.5 py-0.5 text-[8.5px] font-black uppercase tracking-wider text-on-primary shrink-0">
          {badge}
        </span>
        <span className="truncate">{message}</span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {onEnableDemo && isNetwork && (
          <button
            onClick={onEnableDemo}
            className="flex items-center gap-1 border border-grid bg-canvas px-2 py-0.5 text-[9px] font-black uppercase tracking-wider hover:bg-block-ink hover:text-canvas"
          >
            Switch to Demo
          </button>
        )}
        {onDismiss && (
          <button
            onClick={onDismiss}
            aria-label="Dismiss error notice"
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
    </aside>
  );
}

export function TaskEmptyState({ message = "No blocks match this view." }: { message?: string }) {
  return (
    <div className="border-[3px] border-dashed border-grid p-8 text-center">
      <p className="font-display text-2xl uppercase">Clear space.</p>
      <p className="mt-2 text-xs text-muted-foreground">{message}</p>
    </div>
  );
}

export function HeatMap({
  values = dailyCompletions,
  size = "md",
}: {
  values?: number[];
  size?: "md" | "lg";
}) {
  const max = Math.max(...values);
  const tone = (v: number) =>
    v === 0
      ? "bg-muted"
      : v >= max * 0.85
        ? "bg-block-red"
        : v >= max * 0.55
          ? "bg-block-blue"
          : v >= max * 0.3
            ? "bg-block-yellow"
            : "bg-canvas";
  return (
    <div
      className={`grid grid-cols-7 ${size === "lg" ? "gap-2" : "gap-1.5"}`}
      aria-label="Completion heat map"
    >
      {values.map((v, i) => (
        <span
          key={i}
          title={`${v} done`}
          style={{ animationDelay: `${(i % 7) * 60 + Math.floor(i / 7) * 120}ms` }}
          className={`heat-cell aspect-square border-2 border-grid ${tone(v)}`}
        />
      ))}
    </div>
  );
}

const pages = [
  { to: "/", label: "Today" },
  { to: "/flow", label: "Flow" },
  { to: "/projects", label: "Projects" },
  { to: "/focus", label: "Focus" },
  { to: "/stats", label: "Stats" },
  { to: "/diary", label: "Diary" },
  { to: "/devices", label: "Devices" },
  { to: "/raphael", label: "Raphael" },
] as const;

export function AppHeader() {
  const { isDark, toggleTheme, query, setQuery, moodFilter, setMoodFilter } = useBlockday();

  const moods: { mood: Mood | "All"; label: string; bg: string }[] = [
    { mood: "Deep", label: "Deep", bg: "bg-block-red" },
    { mood: "Clear", label: "Clear", bg: "bg-block-blue" },
    { mood: "Creative", label: "Creative", bg: "bg-block-yellow" },
    { mood: "Easy", label: "Easy", bg: "bg-panel" },
    { mood: "All", label: "All", bg: "bg-block-ink" },
  ];

  return (
    <header className="app-header">
      <Link to="/" className="flex items-center justify-between border-r-[5px] border-grid px-4">
        <div>
          <span className="block font-display text-xl uppercase leading-none tracking-tight">
            Blockday
          </span>
          <span className="mt-0.5 block text-[8px] font-black uppercase tracking-[0.24em] text-muted-foreground">
            Boogie Woogie
          </span>
        </div>
        <span className="grid h-7 w-7 grid-cols-2 grid-rows-2 border-2 border-grid" aria-hidden>
          <i className="bg-block-red" />
          <i className="bg-canvas" />
          <i className="bg-block-blue" />
          <i className="bg-block-yellow" />
        </span>
      </Link>
      <nav className="flex items-center gap-1 overflow-x-auto px-2" aria-label="Pages">
        {pages.map((p) => (
          <Link
            key={p.to}
            to={p.to}
            activeOptions={{ exact: true }}
            className="nav-tab border-r-2 border-grid/20 last:border-r-0"
            activeProps={{ className: "nav-tab-active border-transparent shadow-inner" }}
          >
            <span className="nav-num">{pages.indexOf(p) + 1}</span>
            {p.label}
          </Link>
        ))}
      </nav>
      <div className="flex items-center justify-end gap-3 border-l-[5px] border-grid px-4">
        {/* Mood filter section from Boogie Woogie reference */}
        <div className="hidden items-center gap-2 xl:flex">
          <span className="text-[8px] font-black uppercase tracking-[0.16em] text-muted-foreground">
            In the mood for
          </span>
          <div className="flex items-center gap-1">
            {moods.map((m) => {
              const active = moodFilter === m.mood;
              return (
                <button
                  key={m.mood}
                  onClick={() => setMoodFilter(active && m.mood !== "All" ? "All" : m.mood)}
                  title={`Filter: ${m.label}`}
                  className={`size-4 border border-grid transition-transform hover:scale-110 ${m.bg} ${active ? "ring-2 ring-grid ring-offset-1" : "opacity-80 hover:opacity-100"}`}
                />
              );
            })}
          </div>
        </div>

        {/* Search Bar */}
        <div className="flex items-center border-2 border-grid bg-canvas px-2.5 py-1">
          <span className="mr-2 text-[8px] font-black uppercase tracking-[0.18em] text-muted-foreground">
            Search
          </span>
          <input
            aria-label="Search tasks"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-28 bg-transparent text-xs outline-none placeholder:text-muted-foreground sm:w-36 lg:w-44"
            placeholder="Find anything"
          />
        </div>

        {/* Theme toggle */}
        <button
          aria-label="Toggle theme"
          onClick={toggleTheme}
          title={isDark ? "Switch to light theme" : "Switch to dark theme"}
          className="flex h-9 w-9 items-center justify-center border-2 border-grid bg-canvas transition-colors hover:bg-panel"
        >
          <div className="flex h-5 w-5 overflow-hidden rounded-full border border-grid">
            <span className="w-1/2 bg-foreground" />
            <span className="w-1/2 bg-background" />
          </div>
        </button>

        {isCognitoConfigured() && getCognitoAccessToken() && (
          <button
            type="button"
            aria-label="Sign out"
            title="Sign out of Personal OS"
            onClick={() => beginCognitoLogout()}
            className="flex h-9 items-center justify-center border-2 border-grid bg-canvas px-2.5 text-[10px] font-bold uppercase transition-colors hover:bg-panel"
          >
            Sign out
          </button>
        )}
      </div>
    </header>
  );
}
