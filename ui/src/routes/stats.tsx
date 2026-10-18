import { createFileRoute } from "@tanstack/react-router";
import { Check, ChevronLeft, ChevronRight, Play, RotateCcw } from "lucide-react";
import { useState } from "react";
import { toneBg } from "@/components/blockday-ui";
import { dailyCompletions, projectById, streaks } from "@/lib/blockday-data";
import { formatClock, morph, useBlockday } from "@/lib/blockday-store";

export const Route = createFileRoute("/stats")({
  head: () => ({
    meta: [
      { title: "Composition of Work — Blockday" },
      { name: "description", content: "The last twelve months: unified activity grid, flavor mix, streaks, and focus metrics." },
      { property: "og:title", content: "Composition of Work — Blockday" },
      { property: "og:description", content: "The last twelve months: unified activity grid, flavor mix, streaks, and focus metrics." },
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

  const { current } = streaks(dailyCompletions);
  const totalTasksDone = 542; // Representative sample matching reference
  const pomodoros = 118; // Matching reference
  const bestDay = Math.max(...dailyCompletions, 7);

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
    const base = isToday ? "ring-2 ring-block-red ring-offset-2 ring-offset-canvas z-10 " : "";
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
        return base + "bg-panel/25 border border-grid/35 hover:border-grid";
    }
  };

  return (
    <main className="grid grid-rows-[auto_1fr] min-h-[calc(100vh-72px)] bg-grid gap-[4px] border-l-[4px] border-grid overflow-y-auto">
      {/* =========================================================================
          UPPER SECTION: UNIFIED "COMPOSITION OF WORK" GRID
          Pure Mondrian grid cells without nested card boxes
         ========================================================================= */}
      <section className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_130px] xl:grid-cols-[minmax(0,1fr)_160px] gap-[4px] bg-grid flex-shrink-0">
        {/* Cell 1: Main Activity Canvas */}
        <div className="bg-canvas p-5 sm:p-6 xl:p-7 flex flex-col justify-between">
          {/* Header Row: Title + Integrated Metric Cells */}
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between pb-5 border-b-2 border-grid">
            <div>
              <span className="text-[10px] font-black uppercase tracking-[0.24em] text-muted-foreground block mb-1">
                The last twelve months
              </span>
              <h1 className="font-display text-4xl sm:text-5xl xl:text-6xl uppercase leading-none tracking-tight">
                Composition of Work
              </h1>
            </div>

            {/* Metrics: single contiguous grid row with internal line dividers */}
            <div className="flex border-2 border-grid bg-canvas self-start lg:self-auto overflow-hidden divide-x-2 divide-grid">
              <div className="bg-block-yellow text-block-ink px-4 py-2 sm:px-5 sm:py-2.5 text-center min-w-[85px] xl:min-w-[105px]">
                <span className="font-display text-4xl xl:text-5xl font-black leading-none tabular-nums block">
                  {current || 5}
                </span>
                <span className="text-[8.5px] xl:text-[9.5px] font-black uppercase tracking-[0.16em] block mt-1">
                  Day Streak
                </span>
              </div>
              <div className="bg-canvas text-foreground px-4 py-2 sm:px-5 sm:py-2.5 text-center min-w-[95px] xl:min-w-[120px]">
                <span className="font-display text-4xl xl:text-5xl font-black leading-none tabular-nums block">
                  {totalTasksDone}
                </span>
                <span className="text-[8.5px] xl:text-[9.5px] font-black uppercase tracking-[0.16em] text-muted-foreground block mt-1">
                  Tasks Done
                </span>
              </div>
              <div className="bg-canvas text-foreground px-4 py-2 sm:px-5 sm:py-2.5 text-center min-w-[95px] xl:min-w-[120px]">
                <span className="font-display text-4xl xl:text-5xl font-black leading-none tabular-nums block">
                  {pomodoros}
                </span>
                <span className="text-[8.5px] xl:text-[9.5px] font-black uppercase tracking-[0.16em] text-muted-foreground block mt-1">
                  Pomodoros
                </span>
              </div>
              <div className="bg-canvas text-foreground px-4 py-2 sm:px-5 sm:py-2.5 text-center min-w-[80px] xl:min-w-[95px]">
                <span className="font-display text-4xl xl:text-5xl font-black leading-none tabular-nums block">
                  {bestDay}
                </span>
                <span className="text-[8.5px] xl:text-[9.5px] font-black uppercase tracking-[0.16em] text-muted-foreground block mt-1">
                  Best Day
                </span>
              </div>
            </div>
          </div>

          {/* Activity Heat Map: Direct grid spanning the cell */}
          <div className="my-5 xl:my-6 overflow-x-auto">
            <div className="min-w-[760px] xl:min-w-[920px]">
              {/* Month Markers directly aligned over columns */}
              <div className="flex justify-between pb-2 text-[9px] xl:text-[10px] font-black uppercase tracking-[0.24em] text-muted-foreground px-1">
                {monthLabels.map((m, idx) => (
                  <span key={idx}>{m}</span>
                ))}
              </div>

              {/* 52-column x 7-row Activity Grid */}
              <div className="border-2 border-grid p-3 sm:p-4 bg-canvas">
                <div className="grid grid-flow-col grid-rows-7 gap-[3px] xl:gap-[4px]">
                  {yearMatrix.map((col, colIdx) =>
                    col.map((cell, rowIdx) => (
                      <div
                        key={`${colIdx}-${rowIdx}`}
                        title={`Week ${colIdx + 1}, Day ${rowIdx + 1}`}
                        className={`size-3.5 sm:size-4 md:size-[17px] xl:size-[19px] aspect-square transition-transform hover:scale-125 ${cellColorClass(cell.tone, cell.isToday)}`}
                      />
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Bottom Bar: Flavor Mix & Activity Intensity Legend */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between pt-4 border-t-2 border-grid text-[9.5px] font-black uppercase tracking-[0.14em]">
            {/* Flavor Mix: single continuous segmented bar */}
            <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center">
              <span className="text-[8.5px] text-muted-foreground mr-2 tracking-[0.2em]">Flavor Mix</span>
              <div className="flex border-2 border-grid h-7 xl:h-8 divide-x-2 divide-grid overflow-hidden">
                <div className="bg-block-red text-on-primary px-3 flex items-center">
                  Frog 1
                </div>
                <div className="bg-block-blue text-on-primary px-3 flex items-center">
                  Deep 2
                </div>
                <div className="bg-block-yellow text-block-ink px-3 flex items-center">
                  Spark 1
                </div>
                <div className="bg-panel text-foreground px-3 flex items-center">
                  Errand 3
                </div>
                <div className="bg-canvas text-foreground px-3 flex items-center">
                  Plain 2
                </div>
              </div>
            </div>

            {/* Activity Intensity Legend & Action */}
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1.5 text-[8.5px] text-muted-foreground">
                <span>Less</span>
                <span className="size-3 border border-grid bg-panel/30" />
                <span className="size-3 border border-grid bg-block-blue" />
                <span className="size-3 border border-grid bg-block-yellow" />
                <span className="size-3 border border-grid bg-block-red" />
                <span>More</span>
              </div>
              <button
                onClick={() => alert("Data reset to initial composition state")}
                className="ghost-chip text-[8.5px] py-1 px-2.5 hover:bg-block-ink hover:text-canvas"
              >
                Reset all data
              </button>
            </div>
          </div>
        </div>

        {/* Cell 2: Integrated Yellow Geometric Slab */}
        <div className="hidden lg:block bg-block-yellow h-full w-full" />
      </section>

      {/* =========================================================================
          LOWER SECTION: UNIFIED WORKSPACE GRID
          Zero floating boxes: directly divided cells matching the De Stijl geometry
         ========================================================================= */}
      <section className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.25fr)_130px_minmax(0,1.1fr)] xl:grid-cols-[minmax(0,1.3fr)_160px_minmax(0,1.1fr)] gap-[4px] bg-grid h-full">
        {/* Cell 1: Today Date & Tasks Ledger */}
        <div className="bg-canvas flex flex-col h-full justify-between">
          <div>
            {/* Date Header */}
            <div className="border-b-2 border-grid p-4 xl:p-5">
              <div className="flex items-baseline justify-between gap-3">
                <div className="flex items-baseline gap-3">
                  <span className="font-display text-5xl xl:text-6xl font-black leading-none tabular-nums text-foreground">
                    {dayNum}
                  </span>
                  <div>
                    <h2 className="font-display text-xl xl:text-2xl uppercase leading-none tracking-tight">{weekday}</h2>
                    <p className="mt-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-muted-foreground">
                      {monthYear}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => morph(() => setDayOffset(Math.max(-3, dayOffset - 1)))}
                    disabled={dayOffset <= -3}
                    className="ghost-chip px-2 py-1"
                    title="Previous Day"
                  >
                    <ChevronLeft className="size-3" />
                  </button>
                  <button
                    onClick={() => morph(() => setDayOffset(0))}
                    className="ghost-chip px-2 py-1 text-[9px]"
                  >
                    Today
                  </button>
                  <button
                    onClick={() => morph(() => setDayOffset(Math.min(0, dayOffset + 1)))}
                    disabled={dayOffset >= 0}
                    className="ghost-chip px-2 py-1"
                    title="Next Day"
                  >
                    <ChevronRight className="size-3" />
                  </button>
                  <button
                    onClick={() => morph(() => setSortByEnergy(!sortByEnergy))}
                    className={`ghost-chip flex items-center gap-1 text-[9px] py-1 ${sortByEnergy ? "bg-block-ink text-canvas" : ""}`}
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

            {/* Quick add task input */}
            <form onSubmit={handleAddTask} className="border-b-2 border-grid bg-canvas px-4 py-2.5">
              <input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Add to today..."
                className="w-full bg-transparent text-xs font-medium outline-none placeholder:text-muted-foreground"
              />
            </form>

            {/* Task list rows */}
            <div className="divide-y divide-grid/20">
              {open.map((t) => {
                const project = projectById(t.projectId);
                return (
                  <div
                    key={t.id}
                    onClick={() => selectTask(t.id)}
                    className="group flex cursor-pointer items-center justify-between px-4 py-2.5 xl:py-3 transition-colors hover:bg-panel"
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
            </div>

            {done.length > 0 && (
              <div className="border-t-2 border-grid">
                <div className="bg-panel px-4 py-1.5 text-[8.5px] font-black uppercase tracking-[0.18em] text-muted-foreground border-b border-grid/20">
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
                        <div className="flex items-center gap-3">
                          <span className={`h-3.5 w-1 flex-shrink-0 ${project ? toneBg[project.tone] : "bg-block-blue"}`} />
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

        {/* Cell 2: Contiguous Stacked Blue and Red Geometric Slabs */}
        <div className="hidden lg:grid grid-rows-2 gap-[4px] h-full">
          <div className="bg-block-blue h-full w-full" />
          <div className="bg-block-red h-full w-full" />
        </div>

        {/* Cell 3: Contiguous Stacked Focus Timer & Google Calendar */}
        <div className="hidden lg:grid grid-rows-2 gap-[4px] h-full">
          {/* Focus Timer */}
          <div className="bg-canvas p-4 xl:p-5 flex flex-col justify-between h-full">
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
                <p className="mt-1.5 text-[8px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
                  Drag a task here, or press ▶ on one
                </p>
              </div>
            </div>

            <div className="mt-3">
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
              <p className="mt-1.5 text-center text-[8px] font-black uppercase tracking-[0.16em] text-muted-foreground">
                {timer.sessions} Today
              </p>
            </div>
          </div>

          {/* Google Calendar */}
          <div className="bg-canvas p-4 xl:p-5 flex flex-col justify-between h-full">
            <div>
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
            </div>

            <div className="mt-3 flex gap-2 border-t border-grid/20 pt-2">
              <button
                onClick={() => setSynced("Synced just now")}
                className="ghost-chip flex-1 py-1 text-center text-[8.5px]"
              >
                Sync
              </button>
              <button
                onClick={() => alert("Flow scheduled into calendar")}
                className="ghost-chip flex-1 py-1 text-center text-[8.5px]"
              >
                Send flow
              </button>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
