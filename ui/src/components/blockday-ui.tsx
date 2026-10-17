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
  const { isDark, toggleTheme, query, setQuery, dayOffset, setDayOffset, today } = useBlockday();
  return (
    <header className="app-header">
      <Link to="/" className="flex items-center gap-3 border-r-[5px] border-grid px-5">
        <span className="grid h-9 w-9 grid-cols-2 grid-rows-2 border-[3px] border-grid" aria-hidden>
          <i className="bg-block-red" /><i className="bg-canvas" /><i className="bg-block-blue" /><i className="bg-block-yellow" />
        </span>
        <span><span className="block font-display text-xl uppercase leading-none">Blockday</span><span className="mt-1 block text-[9px] font-bold uppercase tracking-[0.22em] text-muted-foreground">Daily composition</span></span>
      </Link>
      <nav className="flex items-center gap-1 overflow-x-auto px-4" aria-label="Pages">
        {pages.map((p) => (
          <Link key={p.to} to={p.to} activeOptions={{ exact: true }} className="nav-tab" activeProps={{ className: "nav-tab-active" }}><span className="nav-num">{pages.indexOf(p) + 1}</span>{p.label}</Link>
        ))}
      </nav>
      <div className="flex items-center justify-end gap-2 border-l-[5px] border-grid px-4">
        <div className="relative w-44 xl:w-56">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input aria-label="Search tasks" value={query} onChange={(e) => setQuery(e.target.value)} className="field h-10 w-full pl-9" placeholder="Search" />
        </div>
        <IconButton aria-label="Previous day" onClick={() => morph(() => setDayOffset(Math.max(-3, dayOffset - 1)))} disabled={dayOffset <= -3}><ChevronLeft /></IconButton>
        <span className="w-24 text-center text-[10px] font-black uppercase tracking-[0.12em]">{dayOffset === 0 ? "Today" : dayLabel(today, dayOffset)}</span>
        <IconButton aria-label="Next day" onClick={() => morph(() => setDayOffset(Math.min(0, dayOffset + 1)))} disabled={dayOffset >= 0}><ChevronRight /></IconButton>
        <IconButton aria-label="Toggle theme" onClick={toggleTheme}>{isDark ? <Sun /> : <Moon />}</IconButton>
      </div>
    </header>
  );
}
