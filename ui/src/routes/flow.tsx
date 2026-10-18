import { createFileRoute } from "@tanstack/react-router";
import { Check, ChevronLeft, ChevronRight, Clock, Play, RotateCcw } from "lucide-react";
import { useState } from "react";
import { toneBg } from "@/components/blockday-ui";
import { historicalDays, projectById, type Task } from "@/lib/blockday-data";
import { formatClock, morph, useBlockday } from "@/lib/blockday-store";

export const Route = createFileRoute("/flow")({
  head: () => ({
    meta: [
      { title: "Flow — Blockday" },
      { name: "description", content: "Hardest first, softest last: a substantial timeline alongside a task workspace and utility rail." },
      { property: "og:title", content: "Flow — Blockday" },
      { property: "og:description", content: "Hardest first, softest last: a substantial timeline alongside a task workspace and utility rail." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: FlowPage,
});

export function FlowPage() {
  const {
    tasks,
    setTasks,
    addTask,
    dayOffset,
    setDayOffset,
    today,
    timer,
    setMode,
    toggleTimer,
    resetTimer,
    selectTask,
  } = useBlockday();

  const [newTitle, setNewTitle] = useState("");
  const [sortByEnergy, setSortByEnergy] = useState(false);
  const [synced, setSynced] = useState("Synced 2h ago");

  const isToday = dayOffset === 0;
  const dayTasks: Task[] = dayOffset < 0 ? (historicalDays[dayOffset] ?? []) : tasks;

  const toggle = (id: string) => {
    if (!isToday) return;
    morph(() => setTasks((ts) => ts.map((t) => (t.id === id ? { ...t, done: !t.done } : t))));
  };

  const handleAddTask = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim()) return;
    addTask(newTitle.trim(), "blockday", 15);
    setNewTitle("");
  };

  const date = new Date(today ?? Date.now());
  date.setDate(date.getDate() + dayOffset);
  const dayNum = date.getDate();
  const weekday = date.toLocaleDateString("en-US", { weekday: "long" });
  const monthYear = date.toLocaleDateString("en-US", { month: "long", year: "numeric" });

  const rawOpen = dayTasks.filter((t) => !t.done);
  const done = dayTasks.filter((t) => t.done);

  // Sorting
  const energyOrder: Record<string, number> = { Deep: 1, Clear: 2, Creative: 3, Easy: 4 };
  const open = sortByEnergy
    ? [...rawOpen].sort((a, b) => (energyOrder[a.mood] ?? 5) - (energyOrder[b.mood] ?? 5))
    : rawOpen;

  const plannedMin = open.reduce((sum, t) => sum + t.estimate, 0);

  // Flow timeline items: build scheduled items list from open + demo tasks
  const timelineSchedule = [
    { time: "9:00am", title: "Water the plants", drift: "10M", project: "home", energy: "high" },
    { time: "9:10am", title: "Tidy the desk", drift: "15M", project: "personal", energy: "mid" },
    { time: "9:35am", title: "Resolve the onboarding flow", drift: "50M", project: "blockday", energy: "high" },
    { time: "10:30am", title: "Write launch page headline", drift: "25M", project: "blockday", energy: "creative" },
  ];

  const calendarEvents = [
    { time: "10:00am", title: "Standup" },
    { time: "12:30pm", title: "Lunch" },
    { time: "2:00pm", title: "1:1" },
    { time: "5:30pm", title: "Gym" },
  ];

  const frog = open.find((t) => t.frog) || open[0];

  return (
    <main className="page-grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_260px]">
      {/* COLUMN 1: DAY FLOW TIMELINE */}
      <section className="flex min-h-0 flex-col bg-canvas border-r-[5px] border-grid">
        {/* Day Flow Header */}
        <div className="flex items-end justify-between border-b-2 border-grid p-4 sm:p-5">
          <div>
            <span className="section-label text-muted-foreground">Hardest first. Softest last.</span>
            <h1 className="mt-1 font-display text-4xl uppercase leading-none tracking-tight">Day Flow</h1>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-muted-foreground">
              <span>Start <strong className="text-foreground">09:00 AM</strong></span>
              <Clock className="size-3" />
              <span>Wraps <strong className="text-foreground">9:25 AM</strong></span>
            </div>
            <button
              onClick={() => {
                morph(() => setSortByEnergy(true));
              }}
              className="ghost-chip bg-canvas hover:bg-block-ink hover:text-canvas"
            >
              Apply to list
            </button>
          </div>
        </div>

        {/* Timeline Body with side Energy Bar */}
        <div className="flex min-h-0 flex-1">
          {/* Timeline Blocks Stack */}
          <div className="flex min-h-0 flex-1 flex-col justify-between overflow-y-auto">
            <div className="flex flex-col">
              {timelineSchedule.map((item, idx) => {
                const project = projectById(item.project);
                return (
                  <div
                    key={idx}
                    className="group flex items-center justify-between border-b-2 border-grid bg-canvas px-4 py-4 transition-colors hover:bg-panel"
                  >
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xs font-bold text-muted-foreground">{item.time}</span>
                      <span className={`project-swatch ${project ? toneBg[project.tone] : "bg-panel"}`} />
                      <span className="font-bold text-sm tracking-tight">{item.title}</span>
                    </div>
                    <span className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
                      DRIFT · {item.drift}
                    </span>
                  </div>
                );
              })}

              {/* Free gap line */}
              <div className="flex items-center justify-between border-b-2 border-dashed border-grid/40 px-4 py-3 text-[10px] font-bold uppercase tracking-[0.16em] text-muted-foreground/60">
                <span>09:25am — Free space</span>
                <span>35M open</span>
              </div>
            </div>

            {/* Bottom: Calendar Event Strip (Diagonal stripes) */}
            <div className="mt-auto border-t-2 border-grid">
              <div className="calendar-hatched flex items-center justify-between border-b-2 border-grid px-4 py-5 font-bold">
                <div className="flex items-center gap-3 bg-canvas/90 px-2 py-1 border border-grid">
                  <span className="font-mono text-xs text-muted-foreground">10:00am</span>
                  <span className="text-sm uppercase tracking-tight">Standup</span>
                </div>
                <span className="bg-canvas/90 px-2 py-1 text-[9px] font-black uppercase tracking-[0.18em] text-muted-foreground border border-grid">
                  Calendar
                </span>
              </div>
            </div>
          </div>

          {/* Vertical Energy Meter */}
          <div className="flex w-6 flex-col items-center justify-between border-l-2 border-grid bg-canvas py-4" aria-hidden>
            <span className="text-[7px] font-black uppercase tracking-[0.24em] text-muted-foreground [writing-mode:vertical-rl]">
              High Energy
            </span>
            <div className="my-2 flex w-2 flex-1 flex-col overflow-hidden border border-grid">
              <span className="h-1/3 w-full bg-block-red" />
              <span className="h-1/3 w-full bg-block-blue" />
              <span className="h-1/3 w-full bg-block-yellow" />
            </div>
            <span className="text-[7px] font-black uppercase tracking-[0.24em] text-muted-foreground [writing-mode:vertical-rl]">
              Low
            </span>
          </div>
        </div>
      </section>

      {/* COLUMN 2: TASK WORKSPACE */}
      <section className="flex min-h-0 flex-col bg-canvas border-r-[5px] border-grid">
        {/* Date Numeral + Action Controls Header */}
        <div className="border-b-2 border-grid p-4 sm:p-5">
          <div className="flex items-baseline justify-between gap-4">
            <div className="flex items-baseline gap-3">
              <span className="font-display text-5xl font-black leading-none tabular-nums text-foreground">
                {dayNum}
              </span>
              <div>
                <h2 className="font-display text-xl uppercase leading-none tracking-tight">{weekday}</h2>
                <p className="mt-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-muted-foreground">
                  {monthYear}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => morph(() => setDayOffset(Math.max(-3, dayOffset - 1)))}
                disabled={dayOffset <= -3}
                className="ghost-chip px-2"
                title="Previous Day"
              >
                <ChevronLeft className="size-3" />
              </button>
              <button
                onClick={() => morph(() => setDayOffset(0))}
                className="ghost-chip px-2 text-[9px]"
              >
                Today
              </button>
              <button
                onClick={() => morph(() => setDayOffset(Math.min(0, dayOffset + 1)))}
                disabled={dayOffset >= 0}
                className="ghost-chip px-2"
                title="Next Day"
              >
                <ChevronRight className="size-3" />
              </button>
              <button
                onClick={() => morph(() => setSortByEnergy(!sortByEnergy))}
                className={`ghost-chip flex items-center gap-1 text-[9px] ${sortByEnergy ? "bg-block-ink text-canvas" : ""}`}
                title="Sort by Energy"
              >
                <span className="flex gap-0.5">
                  <i className="size-1.5 bg-block-red" />
                  <i className="size-1.5 bg-block-yellow" />
                  <i className="size-1.5 bg-block-blue" />
                </span>
                Sort by energy
              </button>
            </div>
          </div>

          <div className="mt-3 flex items-center justify-between text-[9px] font-black uppercase tracking-[0.16em] text-muted-foreground border-t border-grid/20 pt-2">
            <span>{open.length} Open · {done.length} Done · {plannedMin}M Planned</span>
            <span>Wraps ~9:25AM</span>
          </div>
        </div>

        {/* Add to today input */}
        <form onSubmit={handleAddTask} className="border-b-2 border-grid bg-canvas px-4 py-2.5">
          <input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="Add to today..."
            className="w-full bg-transparent text-xs font-medium outline-none placeholder:text-muted-foreground"
          />
        </form>

        {/* Task List */}
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {/* Open Tasks */}
          <div className="divide-y divide-grid/20">
            {open.map((t) => {
              const project = projectById(t.projectId);
              return (
                <div
                  key={t.id}
                  onClick={() => selectTask(t.id)}
                  className="group flex cursor-pointer items-center justify-between px-4 py-3 transition-colors hover:bg-panel"
                >
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        toggle(t.id);
                      }}
                      className="check-button size-5"
                      aria-label={`Complete ${t.title}`}
                    >
                      {t.done && <Check className="size-3 stroke-[3]" />}
                    </button>
                    <span className="text-sm font-bold tracking-tight">{t.title}</span>
                  </div>
                  <div className="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
                    <span>{t.estimate}M</span>
                    <span className={`project-swatch ${project ? toneBg[project.tone] : "bg-panel"}`} />
                  </div>
                </div>
              );
            })}
            {!open.length && (
              <p className="px-4 py-4 text-xs text-muted-foreground">No open tasks for today.</p>
            )}
          </div>

          {/* Done Tasks Divider */}
          {done.length > 0 && (
            <div className="mt-4 border-t-2 border-grid">
              <div className="bg-panel px-4 py-1.5 text-[9px] font-black uppercase tracking-[0.18em] text-muted-foreground border-b border-grid/20">
                Done · {done.length}
              </div>
              <div className="divide-y divide-grid/20 opacity-60">
                {done.map((t) => {
                  const project = projectById(t.projectId);
                  return (
                    <div
                      key={t.id}
                      onClick={() => toggle(t.id)}
                      className="group flex cursor-pointer items-center justify-between px-4 py-2 hover:opacity-100 transition-opacity"
                    >
                      <div className="flex items-center gap-3">
                        <span className={`h-4 w-1 flex-shrink-0 ${project ? toneBg[project.tone] : "bg-block-blue"}`} />
                        <span className="check-button size-4 bg-block-ink text-canvas">
                          <Check className="size-2.5 stroke-[4]" />
                        </span>
                        <span className="text-xs line-through">{t.title}</span>
                      </div>
                      <div className="flex items-center gap-2 font-mono text-[9px] text-muted-foreground">
                        <span>{t.estimate}M</span>
                        <span className={`project-swatch size-2 ${project ? toneBg[project.tone] : "bg-panel"}`} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Mondrian Red Anchor Block filling the rest of column height */}
          <div className="mt-auto min-h-[90px] border-t-2 border-grid bg-block-red" />
        </div>
      </section>

      {/* COLUMN 3: NARROW UTILITY RAIL */}
      <aside className="flex min-h-0 flex-col gap-[5px] bg-grid">
        {/* EAT THE FROG CARD */}
        {frog && (
          <div className="flex flex-col justify-between border-b-2 border-grid bg-block-red p-4 text-on-primary min-h-[140px]">
            <div>
              <span className="text-[8px] font-black uppercase tracking-[0.2em] opacity-90">Eat the frog</span>
              <h3 className="mt-2 text-base font-bold leading-tight">{frog.title}</h3>
              <p className="mt-1 font-mono text-[9px] uppercase tracking-wider opacity-80">
                Drift · {frog.estimate}M
              </p>
            </div>
            <button
              onClick={() => toggle(frog.id)}
              className="mt-4 self-start border-2 border-on-primary bg-transparent px-3 py-1 text-[9px] font-black uppercase tracking-[0.16em] text-on-primary transition-colors hover:bg-on-primary hover:text-block-red"
            >
              Eat it
            </button>
          </div>
        )}

        {/* GOOGLE CALENDAR CARD */}
        <div className="flex flex-col border-b-2 border-grid bg-canvas p-4">
          <div className="flex items-center justify-between">
            <span className="text-[9px] font-black uppercase tracking-[0.16em]">Google Calendar</span>
            <span className="text-[10px] text-muted-foreground">✕</span>
          </div>
          <div className="mt-1 flex items-center gap-1.5 text-[8px] font-bold uppercase tracking-wider text-muted-foreground">
            <span className="size-1.5 bg-block-blue" />
            <span>{synced}</span>
          </div>

          <div className="mt-3 space-y-1.5 border-t border-grid/20 pt-2 text-xs">
            {calendarEvents.map((evt) => (
              <div key={evt.time} className="flex items-baseline justify-between font-mono">
                <span className="text-[10px] text-muted-foreground">{evt.time}</span>
                <span className="font-sans font-bold text-xs">{evt.title}</span>
              </div>
            ))}
          </div>

          <div className="mt-4 flex gap-2 border-t border-grid/20 pt-2">
            <button
              onClick={() => setSynced("Synced just now")}
              className="ghost-chip flex-1 py-1 text-center text-[8px]"
            >
              Sync
            </button>
            <button
              onClick={() => alert("Flow scheduled into calendar")}
              className="ghost-chip flex-1 py-1 text-center text-[8px]"
            >
              Send flow
            </button>
          </div>
        </div>

        {/* FOCUS TIMER CARD */}
        <div className="flex flex-1 flex-col justify-between border-b-2 border-grid bg-canvas p-4">
          <div>
            <div className="flex border-2 border-grid">
              <button
                onClick={() => setMode("Focus")}
                className={`flex-1 py-1 text-center text-[9px] font-black uppercase tracking-[0.14em] transition-colors ${timer.mode === "Focus" ? "bg-block-ink text-canvas" : "bg-canvas text-foreground"}`}
              >
                Focus
              </button>
              <button
                onClick={() => setMode("Break")}
                className={`flex-1 py-1 text-center text-[9px] font-black uppercase tracking-[0.14em] transition-colors ${timer.mode === "Break" ? "bg-block-ink text-canvas" : "bg-canvas text-foreground"}`}
              >
                Break
              </button>
            </div>

            <div className="mt-4 text-center">
              <span className="font-display text-5xl font-black leading-none tabular-nums tracking-tight">
                {formatClock(timer.seconds)}
              </span>
              <p className="mt-2 text-[8px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
                Drag a task here, or press ▶ on one
              </p>
            </div>
          </div>

          <div className="mt-4">
            <div className="flex gap-2">
              <button
                onClick={toggleTimer}
                className="ghost-chip flex-1 py-1.5 text-center text-[9px] bg-block-blue text-on-primary hover:bg-block-ink hover:text-canvas"
              >
                <Play className="inline-block size-2.5 mr-1 fill-current" />
                {timer.running ? "Pause" : "Start"}
              </button>
              <button
                onClick={resetTimer}
                className="ghost-chip py-1.5 px-2.5"
                title="Reset timer"
              >
                <RotateCcw className="size-2.5" />
              </button>
            </div>
            <p className="mt-2 text-center text-[8px] font-black uppercase tracking-[0.16em] text-muted-foreground">
              {timer.sessions} Today
            </p>
          </div>
        </div>
      </aside>
    </main>
  );
}
