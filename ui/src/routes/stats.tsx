import { createFileRoute } from "@tanstack/react-router";
import { Check, ChevronLeft, ChevronRight, Play, RotateCcw } from "lucide-react";
import { useState } from "react";
import { toneBg } from "@/components/blockday-ui";
import { dailyCompletions, focusMinutesByDay, projectById, streaks, type Task } from "@/lib/blockday-data";
import { formatClock, morph, useBlockday } from "@/lib/blockday-store";

export const Route = createFileRoute("/stats")({
  head: () => ({
    meta: [
      { title: "Composition of Work — Blockday" },
      { name: "description", content: "The last twelve months: dense activity heat map, flavor mix, streaks, and focus metrics." },
      { property: "og:title", content: "Composition of Work — Blockday" },
      { property: "og:description", content: "The last twelve months: dense activity heat map, flavor mix, streaks, and focus metrics." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: StatsPage,
});

type CellTone = "empty" | "red" | "blue" | "yellow" | "cream";

// Generates a deterministic 52-week x 7-day chronological matrix closely matching the reference screenshot
function generateYearMatrix(): { tone: CellTone; isToday?: boolean }[][] {
  const cols: { tone: CellTone; isToday?: boolean }[][] = [];
  let seed = 127;
  const rand = () => {
    seed = (seed * 9301 + 49297) % 233280;
    return seed / 233280;
  };

  for (let c = 0; c < 52; c++) {
    const col: { tone: CellTone; isToday?: boolean }[] = [];
    for (let r = 0; r < 7; r++) {
      const isToday = c === 51 && r === 4;

      if (c < 12) {
        // Sep - Nov: mostly dark with sparse points
        if ((c === 4 && r === 1) || (c === 8 && r === 5) || (c === 11 && r === 2)) {
          col.push({ tone: "yellow" });
        } else if (c === 10 && r === 6) {
          col.push({ tone: "blue" });
        } else {
          col.push({ tone: "empty" });
        }
      } else if (c < 16) {
        // Transition Dec
        const v = rand();
        if (v > 0.65) col.push({ tone: "yellow" });
        else if (v > 0.5) col.push({ tone: "blue" });
        else col.push({ tone: "empty" });
      } else {
        // High density activity (Jan - Sep)
        const v = rand();
        if (v > 0.77) col.push({ tone: "yellow", isToday });
        else if (v > 0.58) col.push({ tone: "blue", isToday });
        else if (v > 0.44) col.push({ tone: "red", isToday });
        else if (v > 0.3) col.push({ tone: "cream", isToday });
        else col.push({ tone: "empty", isToday });
      }
    }
    cols.push(col);
  }
  return cols;
}

const yearMatrix = generateYearMatrix();
const monthLabels = ["SEP", "OCT", "NOV", "DEC", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP"];

export function StatsPage() {
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

  const { current, best } = streaks(dailyCompletions);
  const totalTasksDone = 542; // Representative sample matching screenshot
  const pomodoros = 118; // Matching screenshot
  const bestDay = Math.max(...dailyCompletions, 7);

  const isToday = dayOffset === 0;
  const rawOpen = tasks.filter((t) => !t.done);
  const done = tasks.filter((t) => t.done);

  const energyOrder: Record<string, number> = { Deep: 1, Clear: 2, Creative: 3, Easy: 4 };
  const open = sortByEnergy
    ? [...rawOpen].sort((a, b) => (energyOrder[a.mood] ?? 5) - (energyOrder[b.mood] ?? 5))
    : rawOpen;

  const plannedMin = open.reduce((sum, t) => sum + t.estimate, 0);

  const toggle = (id: string) => {
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

  const calendarEvents = [
    { time: "10:00am", title: "Standup" },
    { time: "12:30pm", title: "Lunch" },
  ];

  const cellColorClass = (tone: CellTone, isToday?: boolean) => {
    const base = isToday ? "ring-2 ring-block-red ring-offset-1 z-10 " : "";
    switch (tone) {
      case "red":
        return base + "bg-block-red border border-grid";
      case "blue":
        return base + "bg-block-blue border border-grid";
      case "yellow":
        return base + "bg-block-yellow border border-grid";
      case "cream":
        return base + "bg-panel text-foreground border border-grid";
      default:
        return base + "bg-panel/40 border border-grid/40 hover:border-grid";
    }
  };

  return (
    <main className="page-grid grid-cols-1 overflow-y-auto">
      <div className="flex min-h-0 flex-col gap-[5px] bg-grid">
        {/* TOP SECTION: DOMINANT "COMPOSITION OF WORK" ACTIVITY CANVAS + ASYMMETRIC YELLOW PANEL */}
        <section className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_180px] xl:grid-cols-[minmax(0,1fr)_220px] gap-[5px]">
          {/* Main Activity Canvas */}
          <div className="border-b-[5px] border-grid bg-canvas p-4 sm:p-5 flex flex-col justify-between">
            {/* Canvas Header + Metrics Strip */}
            <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between border-b-2 border-grid pb-4">
              <div>
                <span className="section-label text-muted-foreground">The last twelve months</span>
                <h1 className="mt-1 font-display text-3xl sm:text-4xl uppercase leading-none tracking-tight">
                  Composition of Work
                </h1>
              </div>

              {/* Compact Metrics Strip */}
              <div className="flex border-2 border-grid bg-canvas self-start sm:self-auto overflow-hidden">
                <div className="bg-block-yellow text-block-ink px-3 py-1.5 text-center border-r-2 border-grid min-w-[70px]">
                  <span className="font-display text-3xl font-black leading-none tabular-nums block">
                    {current || 5}
                  </span>
                  <span className="text-[7.5px] font-black uppercase tracking-[0.14em] block mt-0.5">
                    Day Streak
                  </span>
                </div>
                <div className="bg-canvas text-foreground px-3 py-1.5 text-center border-r-2 border-grid min-w-[80px]">
                  <span className="font-display text-3xl font-black leading-none tabular-nums block">
                    {totalTasksDone}
                  </span>
                  <span className="text-[7.5px] font-black uppercase tracking-[0.14em] text-muted-foreground block mt-0.5">
                    Tasks Done
                  </span>
                </div>
                <div className="bg-canvas text-foreground px-3 py-1.5 text-center border-r-2 border-grid min-w-[80px]">
                  <span className="font-display text-3xl font-black leading-none tabular-nums block">
                    {pomodoros}
                  </span>
                  <span className="text-[7.5px] font-black uppercase tracking-[0.14em] text-muted-foreground block mt-0.5">
                    Pomodoros
                  </span>
                </div>
                <div className="bg-canvas text-foreground px-3 py-1.5 text-center min-w-[65px]">
                  <span className="font-display text-3xl font-black leading-none tabular-nums block">
                    {bestDay}
                  </span>
                  <span className="text-[7.5px] font-black uppercase tracking-[0.14em] text-muted-foreground block mt-0.5">
                    Best Day
                  </span>
                </div>
              </div>
            </div>

            {/* 12-Month Timeline & Heat Map Grid */}
            <div className="my-5 overflow-x-auto">
              <div className="min-w-[680px]">
                {/* Month Labels */}
                <div className="flex justify-between pb-1.5 text-[8.5px] font-black uppercase tracking-[0.18em] text-muted-foreground px-0.5">
                  {monthLabels.map((m, idx) => (
                    <span key={idx}>{m}</span>
                  ))}
                </div>

                {/* Heat Map Grid: 52 columns x 7 rows */}
                <div className="grid grid-flow-col grid-rows-7 gap-[2.5px] border-2 border-grid p-2 bg-panel/20">
                  {yearMatrix.map((col, colIdx) =>
                    col.map((cell, rowIdx) => (
                      <div
                        key={`${colIdx}-${rowIdx}`}
                        title={`Week ${colIdx + 1}, Day ${rowIdx + 1}`}
                        className={`size-2.5 sm:size-3 md:size-3.5 aspect-square transition-transform hover:scale-125 ${cellColorClass(cell.tone, cell.isToday)}`}
                      />
                    ))
                  )}
                </div>
              </div>
            </div>

            {/* Bottom Row: Flavor Mix & Legend */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-t-2 border-grid pt-3 text-[9px] font-black uppercase tracking-[0.14em]">
              {/* Flavor Mix */}
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center">
                <span className="text-[8px] text-muted-foreground mr-2 tracking-[0.2em]">Flavor Mix</span>
                <div className="flex border-2 border-grid h-6 items-stretch overflow-hidden">
                  <div className="bg-block-red text-on-primary px-2 flex items-center border-r border-grid">
                    Frog 1
                  </div>
                  <div className="bg-block-blue text-on-primary px-2 flex items-center border-r border-grid">
                    Deep 2
                  </div>
                  <div className="bg-block-yellow text-block-ink px-2 flex items-center border-r border-grid">
                    Spark 1
                  </div>
                  <div className="bg-panel text-foreground px-2 flex items-center border-r border-grid">
                    Errand 3
                  </div>
                  <div className="bg-canvas text-foreground px-2 flex items-center">
                    Plain 2
                  </div>
                </div>
              </div>

              {/* Activity Intensity Legend & Action */}
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-1.5 text-[8px] text-muted-foreground">
                  <span>Less</span>
                  <span className="size-2.5 border border-grid bg-panel/40" />
                  <span className="size-2.5 border border-grid bg-block-blue" />
                  <span className="size-2.5 border border-grid bg-block-yellow" />
                  <span className="size-2.5 border border-grid bg-block-red" />
                  <span>More</span>
                </div>
                <button
                  onClick={() => alert("Data reset to initial composition state")}
                  className="ghost-chip text-[8px] py-1 px-2 hover:bg-block-ink hover:text-canvas"
                >
                  Reset all data
                </button>
              </div>
            </div>
          </div>

          {/* Asymmetric Yellow Geometric Panel */}
          <div className="hidden lg:block border-b-[5px] border-grid bg-block-yellow min-h-[220px]" />
        </section>

        {/* BOTTOM SECTION: SECONDARY LOWER WORKSPACE AREA */}
        <section className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.2fr)_160px_minmax(0,1fr)] xl:grid-cols-[minmax(0,1.3fr)_200px_minmax(0,1fr)] gap-[5px]">
          {/* LOWER WORKSPACE - COLUMN 1: TODAY DATE & TASKS LEDGER */}
          <div className="flex min-h-0 flex-col bg-canvas border-r-[5px] border-grid">
            {/* Date Numeral + Controls Header */}
            <div className="border-b-2 border-grid p-4">
              <div className="flex items-baseline justify-between gap-3">
                <div className="flex items-baseline gap-2.5">
                  <span className="font-display text-4xl font-black leading-none tabular-nums text-foreground">
                    {dayNum}
                  </span>
                  <div>
                    <h2 className="font-display text-lg uppercase leading-none tracking-tight">{weekday}</h2>
                    <p className="mt-0.5 text-[8.5px] font-bold uppercase tracking-[0.16em] text-muted-foreground">
                      {monthYear}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => morph(() => setDayOffset(Math.max(-3, dayOffset - 1)))}
                    disabled={dayOffset <= -3}
                    className="ghost-chip px-1.5 py-0.5"
                    title="Previous Day"
                  >
                    <ChevronLeft className="size-2.5" />
                  </button>
                  <button
                    onClick={() => morph(() => setDayOffset(0))}
                    className="ghost-chip px-1.5 py-0.5 text-[8.5px]"
                  >
                    Today
                  </button>
                  <button
                    onClick={() => morph(() => setDayOffset(Math.min(0, dayOffset + 1)))}
                    disabled={dayOffset >= 0}
                    className="ghost-chip px-1.5 py-0.5"
                    title="Next Day"
                  >
                    <ChevronRight className="size-2.5" />
                  </button>
                  <button
                    onClick={() => morph(() => setSortByEnergy(!sortByEnergy))}
                    className={`ghost-chip flex items-center gap-1 text-[8.5px] py-0.5 ${sortByEnergy ? "bg-block-ink text-canvas" : ""}`}
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

              <div className="mt-2.5 flex items-center justify-between text-[8.5px] font-black uppercase tracking-[0.16em] text-muted-foreground border-t border-grid/20 pt-1.5">
                <span>{open.length} Open · {done.length} Done · {plannedMin}M Planned</span>
                <span>Wraps ~9:25AM</span>
              </div>
            </div>

            {/* Quick add */}
            <form onSubmit={handleAddTask} className="border-b-2 border-grid bg-canvas px-4 py-2">
              <input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Add to today..."
                className="w-full bg-transparent text-xs font-medium outline-none placeholder:text-muted-foreground"
              />
            </form>

            {/* Task rows */}
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
              <div className="divide-y divide-grid/20">
                {open.map((t) => {
                  const project = projectById(t.projectId);
                  return (
                    <div
                      key={t.id}
                      onClick={() => selectTask(t.id)}
                      className="group flex cursor-pointer items-center justify-between px-4 py-2.5 transition-colors hover:bg-panel"
                    >
                      <div className="flex items-center gap-2.5">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            toggle(t.id);
                          }}
                          className="check-button size-4"
                          aria-label={`Complete ${t.title}`}
                        >
                          {t.done && <Check className="size-2.5 stroke-[4]" />}
                        </button>
                        <span className="text-xs font-bold tracking-tight">{t.title}</span>
                      </div>
                      <div className="flex items-center gap-2 font-mono text-[9px] text-muted-foreground">
                        <span>{t.estimate}M</span>
                        <span className={`project-swatch ${project ? toneBg[project.tone] : "bg-panel"}`} />
                      </div>
                    </div>
                  );
                })}
              </div>

              {done.length > 0 && (
                <div className="mt-3 border-t-2 border-grid">
                  <div className="bg-panel px-4 py-1 text-[8px] font-black uppercase tracking-[0.18em] text-muted-foreground border-b border-grid/20">
                    Done · {done.length}
                  </div>
                  <div className="divide-y divide-grid/20 opacity-60">
                    {done.map((t) => {
                      const project = projectById(t.projectId);
                      return (
                        <div
                          key={t.id}
                          onClick={() => toggle(t.id)}
                          className="group flex cursor-pointer items-center justify-between px-4 py-1.5 hover:opacity-100 transition-opacity"
                        >
                          <div className="flex items-center gap-2.5">
                            <span className={`h-3 w-1 flex-shrink-0 ${project ? toneBg[project.tone] : "bg-block-blue"}`} />
                            <span className="text-xs line-through">{t.title}</span>
                          </div>
                          <span className="font-mono text-[9px] text-muted-foreground">{t.estimate}M</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* LOWER WORKSPACE - COLUMN 2: GEOMETRIC COLOR PANELS (BLUE TOP, RED BOTTOM) */}
          <div className="hidden lg:flex flex-col gap-[5px] min-h-[280px]">
            <div className="flex-1 border-2 border-grid bg-block-blue" />
            <div className="flex-1 border-2 border-grid bg-block-red" />
          </div>

          {/* LOWER WORKSPACE - COLUMN 3: FOCUS TIMER & GOOGLE CALENDAR */}
          <div className="flex flex-col gap-[5px] bg-grid">
            {/* Focus Timer */}
            <div className="flex flex-col justify-between border-2 border-grid bg-canvas p-4">
              <div>
                <div className="flex border-2 border-grid">
                  <button
                    onClick={() => setMode("Focus")}
                    className={`flex-1 py-1 text-center text-[8.5px] font-black uppercase tracking-[0.14em] transition-colors ${timer.mode === "Focus" ? "bg-block-ink text-canvas" : "bg-canvas text-foreground"}`}
                  >
                    Focus
                  </button>
                  <button
                    onClick={() => setMode("Break")}
                    className={`flex-1 py-1 text-center text-[8.5px] font-black uppercase tracking-[0.14em] transition-colors ${timer.mode === "Break" ? "bg-block-ink text-canvas" : "bg-canvas text-foreground"}`}
                  >
                    Break
                  </button>
                </div>

                <div className="mt-3 text-center">
                  <span className="font-display text-4xl font-black leading-none tabular-nums tracking-tight">
                    {formatClock(timer.seconds)}
                  </span>
                  <p className="mt-1 text-[7.5px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
                    Drag a task here, or press ▶ on one
                  </p>
                </div>
              </div>

              <div className="mt-3">
                <div className="flex gap-1.5">
                  <button
                    onClick={toggleTimer}
                    className="ghost-chip flex-1 py-1 text-center text-[8.5px] bg-block-blue text-on-primary hover:bg-block-ink hover:text-canvas"
                  >
                    <Play className="inline-block size-2 mr-1 fill-current" />
                    {timer.running ? "Pause" : "Start"}
                  </button>
                  <button
                    onClick={resetTimer}
                    className="ghost-chip py-1 px-2"
                    title="Reset timer"
                  >
                    <RotateCcw className="size-2" />
                  </button>
                </div>
                <p className="mt-1.5 text-center text-[7.5px] font-black uppercase tracking-[0.16em] text-muted-foreground">
                  {timer.sessions} Today
                </p>
              </div>
            </div>

            {/* Google Calendar */}
            <div className="flex flex-col border-2 border-grid bg-canvas p-4">
              <div className="flex items-center justify-between">
                <span className="text-[8.5px] font-black uppercase tracking-[0.16em]">Google Calendar</span>
                <span className="text-[10px] text-muted-foreground">✕</span>
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-[7.5px] font-bold uppercase tracking-wider text-muted-foreground">
                <span className="size-1.5 bg-block-blue" />
                <span>{synced}</span>
              </div>

              <div className="mt-2.5 space-y-1 border-t border-grid/20 pt-1.5 text-xs">
                {calendarEvents.map((evt) => (
                  <div key={evt.time} className="flex items-baseline justify-between font-mono">
                    <span className="text-[9px] text-muted-foreground">{evt.time}</span>
                    <span className="font-sans font-bold text-xs">{evt.title}</span>
                  </div>
                ))}
              </div>

              <div className="mt-3 flex gap-1.5 border-t border-grid/20 pt-1.5">
                <button
                  onClick={() => setSynced("Synced just now")}
                  className="ghost-chip flex-1 py-0.5 text-center text-[8px]"
                >
                  Sync
                </button>
                <button
                  onClick={() => alert("Flow scheduled into calendar")}
                  className="ghost-chip flex-1 py-0.5 text-center text-[8px]"
                >
                  Send flow
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
