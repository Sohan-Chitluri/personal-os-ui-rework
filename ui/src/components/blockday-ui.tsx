import { Link } from "@tanstack/react-router";
import { Check, ChevronLeft, ChevronRight, Moon, Search, Sun } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { dailyCompletions, deadline, projectById, type Task, type Tone } from "@/lib/blockday-data";
import { dayLabel, morph, useBlockday } from "@/lib/blockday-store";

export const toneBg: Record<Tone, string> = { red: "bg-block-red", blue: "bg-block-blue", yellow: "bg-block-yellow", neutral: "bg-canvas" };
export const toneText: Record<Tone, string> = { red: "text-on-primary", blue: "text-on-primary", yellow: "text-block-ink", neutral: "text-foreground" };

export function IconButton({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`icon-button ${className}`} {...props} />;
}

export function SectionLabel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`section-label ${className}`}>{children}</span>;
}

export function GridPanel({ children, className = "", label, aside }: { children: ReactNode; className?: string; label?: ReactNode; aside?: ReactNode }) {
  return (
    <section className={`grid-panel animate-slide-panel ${className}`}>
      {label && <div className="mb-4 flex items-center justify-between gap-3"><SectionLabel>{label}</SectionLabel>{aside}</div>}
      {children}
    </section>
  );
}

export function StatBlock({ value, label, tone = "neutral", className = "" }: { value: ReactNode; label: string; tone?: Tone; className?: string }) {
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

export function TaskRow({ task, onToggle, compact }: { task: Task; onToggle?: (() => void) | undefined; compact?: boolean }) {
  const project = projectById(task.projectId);
  const d = deadline(task.dueIn);
  return (
    <article style={{ viewTransitionName: `task-${task.id}` }} className={`task-row ${task.done ? "task-done" : ""} ${compact ? "min-h-0" : ""}`}>
      <DeadlineStrip dueIn={task.dueIn} />
      {onToggle && (
        <button aria-label={task.done ? `Mark ${task.title} incomplete` : `Complete ${task.title}`} onClick={onToggle} className="check-button">
          {task.done && <Check />}
        </button>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {task.frog && !task.done && <span className="frog-label">Eat the frog</span>}
          <h2 className="truncate text-[16px] font-bold">{task.title}</h2>
        </div>
        <div className="mt-1.5 flex items-center gap-3 text-[9px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
          {project && <span className="flex items-center gap-1.5"><span className={`project-swatch ${toneBg[project.tone]}`} />{project.name}</span>}
          <span>{task.estimate} min</span><span>{task.mood}</span>
        </div>
      </div>
      {!task.done && <span className={`deadline-label deadline-label-${d.kind}`}>{d.label}</span>}
    </article>
  );
}

export function HeatMap({ values = dailyCompletions, size = "md" }: { values?: number[]; size?: "md" | "lg" }) {
  const max = Math.max(...values);
  const tone = (v: number) => v === 0 ? "bg-muted" : v >= max * 0.85 ? "bg-block-red" : v >= max * 0.55 ? "bg-block-blue" : v >= max * 0.3 ? "bg-block-yellow" : "bg-canvas";
  return (
    <div className={`grid grid-cols-7 ${size === "lg" ? "gap-2" : "gap-1.5"}`} aria-label="Completion heat map">
      {values.map((v, i) => (
        <span key={i} title={`${v} done`} style={{ animationDelay: `${(i % 7) * 60 + Math.floor(i / 7) * 120}ms` }} className={`heat-cell aspect-square border-2 border-grid ${tone(v)}`} />
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
          <span className="block font-display text-xl uppercase leading-none tracking-tight">Blockday</span>
          <span className="mt-0.5 block text-[8px] font-black uppercase tracking-[0.24em] text-muted-foreground">Boogie Woogie</span>
        </div>
        <span className="grid h-7 w-7 grid-cols-2 grid-rows-2 border-2 border-grid" aria-hidden>
          <i className="bg-block-red" /><i className="bg-canvas" /><i className="bg-block-blue" /><i className="bg-block-yellow" />
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
          <span className="text-[8px] font-black uppercase tracking-[0.16em] text-muted-foreground">In the mood for</span>
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
          <span className="mr-2 text-[8px] font-black uppercase tracking-[0.18em] text-muted-foreground">Search</span>
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
      </div>
    </header>
  );
}
