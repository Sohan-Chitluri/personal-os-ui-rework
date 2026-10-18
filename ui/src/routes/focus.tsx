import { createFileRoute } from "@tanstack/react-router";
import { Check, ChevronLeft, ChevronRight, Play, RotateCcw } from "lucide-react";
import { useState } from "react";
import { DemoModeBanner, TaskConflictBanner, TaskErrorBanner, toneBg } from "@/components/blockday-ui";
import { historicalDays, projectById, type Task } from "@/lib/blockday-data";
import { formatClock, morph, useBlockday, type TimerMode } from "@/lib/blockday-store";

export const Route = createFileRoute("/focus")({
  head: () => ({
    meta: [
      { title: "Focus — Blockday" },
      { name: "description", content: "A monumental Mondrian timer: focus and break sessions with architectural framing and live task workspace." },
      { property: "og:title", content: "Focus — Blockday" },
      { property: "og:description", content: "A monumental Mondrian timer: focus and break sessions with architectural framing and live task workspace." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: FocusPage,
});

export function FocusPage() {
  const {
    tasks,
    addTask,
    toggleTask,
    conflictError,
    clearConflict,
    error,
    clearError,
    refetchTasks,
    isDemoMode,
    enableDemoMode,
    disableDemoMode,
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

  const isToday = dayOffset === 0;
  const dayTasks: Task[] = dayOffset < 0 ? (historicalDays[dayOffset] ?? []) : tasks;

  const rawOpen = dayTasks.filter((t) => !t.done);
  const done = dayTasks.filter((t) => t.done);

  const energyOrder: Record<string, number> = { Deep: 1, Clear: 2, Creative: 3, Easy: 4 };
  const open = sortByEnergy
    ? [...rawOpen].sort((a, b) => (energyOrder[a.mood] ?? 5) - (energyOrder[b.mood] ?? 5))
    : rawOpen;

  const plannedMin = open.reduce((sum, t) => sum + t.estimate, 0);

  const active = tasks.find((t) => t.id === timer.taskId) ?? open[0];
  const activeProject = active ? projectById(active.projectId) : undefined;
  const frog = open.find((t) => t.frog) || open[0];

  const toggle = (id: string) => {
    void toggleTask(id).catch(() => {});
  };

  const handleAddTask = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim()) return;
    void addTask(newTitle.trim(), "blockday", 15).catch(() => {});
    setNewTitle("");
  };

  const date = new Date(today ?? Date.now());
  date.setDate(date.getDate() + dayOffset);
  const dayNum = date.getDate();
  const weekday = date.toLocaleDateString("en-US", { weekday: "long" });
  const monthYear = date.toLocaleDateString("en-US", { month: "long", year: "numeric" });

  return (
    <main className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] xl:grid-cols-[minmax(0,1fr)_380px] min-h-[calc(100vh-72px)] bg-grid gap-[4px] border-l-[4px] border-grid overflow-y-auto">
      {/* =========================================================================
          STAGE 1 & 2: MAIN FOCUS STAGE (LEFT & CENTER)
          Mondrian wireframe mosaic + Monumental timer + Foundational primary color slabs
         ========================================================================= */}
      <section className="flex flex-col justify-between bg-canvas h-full min-h-[600px]">
        {/* Upper Focus Canvas */}
        <div className="flex-1 flex flex-col lg:flex-row items-center justify-around p-6 sm:p-8 xl:p-12 gap-8 xl:gap-12">
          {/* Mondrian Architectural Wireframe Mosaic */}
          <div className="w-full max-w-[320px] sm:max-w-[360px] xl:max-w-[420px] aspect-[4/5] border-[4px] border-grid bg-grid grid grid-cols-[1.2fr_1.8fr_0.8fr] grid-rows-[1.3fr_0.9fr_1.4fr_0.8fr] gap-[4px] flex-shrink-0 shadow-sm">
            {/* Ruled Asymmetric Rectangles */}
            <div className="bg-canvas transition-colors hover:bg-panel/40" />
            <div className="bg-canvas transition-colors hover:bg-panel/40 row-span-2" />
            <div className="bg-canvas transition-colors hover:bg-panel/40" />
            <div className="bg-canvas transition-colors hover:bg-panel/40" />
            <div className="bg-canvas transition-colors hover:bg-panel/40 row-span-2" />
            <div className="bg-canvas transition-colors hover:bg-panel/40 col-span-2" />
            <div className="bg-canvas transition-colors hover:bg-panel/40" />
            <div className="bg-canvas transition-colors hover:bg-panel/40 col-span-2" />
          </div>

          {/* Monumental Minimalist Focus Timer */}
          <div className="flex flex-col items-center justify-center text-center max-w-md w-full">
            {/* Mode Selector Tabs */}
            <div className="flex border-2 border-grid mb-6 sm:mb-8 overflow-hidden divide-x-2 divide-grid">
              {(["Focus", "Break"] as TimerMode[]).map((m) => {
                const isActive = timer.mode === m;
                return (
                  <button
                    key={m}
                    onClick={() => setMode(m)}
                    className={`px-6 py-1.5 text-[9.5px] font-black uppercase tracking-[0.18em] transition-colors ${isActive ? "bg-block-ink text-canvas" : "bg-canvas text-foreground hover:bg-panel"}`}
                  >
                    {m}
                  </button>
                );
              })}
            </div>

            {/* Monumental Digital Clock Display */}
            <span className="font-display text-7xl sm:text-8xl xl:text-9xl 2xl:text-[10rem] font-black leading-none tabular-nums tracking-tighter block text-foreground select-none">
              {formatClock(timer.seconds)}
            </span>

            {/* Task Context / Drag prompt */}
            <p className="mt-4 sm:mt-6 text-[9px] xl:text-[10px] font-bold uppercase tracking-[0.16em] text-muted-foreground px-4 line-clamp-1">
              {active ? `${active.title}${activeProject ? ` · ${activeProject.name}` : ""}` : "Drag a task here, or press ▶ on one"}
            </p>

            {/* Timer Actions: Start & Reset */}
            <div className="mt-6 flex items-center justify-center gap-2">
              <button
                onClick={toggleTimer}
                className="border-2 border-grid bg-canvas px-7 py-2.5 text-[10px] font-black uppercase tracking-[0.18em] hover:bg-block-ink hover:text-canvas transition-colors flex items-center gap-1.5"
              >
                <Play className="size-2.5 fill-current" />
                {timer.running ? "Pause" : "Start"}
              </button>
              <button
                onClick={resetTimer}
                className="border-2 border-grid bg-canvas px-5 py-2.5 text-[10px] font-black uppercase tracking-[0.18em] hover:bg-block-ink hover:text-canvas transition-colors flex items-center gap-1"
                title="Reset timer"
              >
                <RotateCcw className="size-2.5" />
                Reset
              </button>
            </div>

            {/* Completed sessions indicator */}
            <p className="mt-4 text-[9px] font-black uppercase tracking-[0.2em] text-muted-foreground">
              {timer.sessions} TODAY
            </p>
          </div>
        </div>

        {/* Foundational Horizontal Primary Color Slabs (Yellow | Blue | Red) */}
        <div className="grid grid-cols-[1fr_1.8fr_1.2fr] gap-[4px] bg-grid h-20 sm:h-24 xl:h-28 border-t-[4px] border-grid w-full">
          <div className="bg-block-yellow h-full" />
          <div className="bg-block-blue h-full" />
          <div className="bg-block-red h-full" />
        </div>
      </section>

      {/* =========================================================================
          STAGE 3: RIGHT UTILITY & TASK WORKSPACE
          Solid Red Eat the Frog + Today Tasks Ledger
         ========================================================================= */}
      <aside className="flex flex-col bg-grid gap-[4px] h-full">
        {/* EAT THE FROG CARD */}
        {frog && (
          <div className="bg-block-red text-on-primary p-5 flex flex-col justify-between min-h-[160px]">
            <div>
              <span className="text-[8.5px] font-black uppercase tracking-[0.2em] opacity-90 block">
                Eat the frog
              </span>
              <h3 className="mt-2 text-xl font-bold leading-tight">
                {frog.title}
              </h3>
              <p className="mt-1 font-mono text-[9px] uppercase tracking-wider opacity-85">
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

        {/* TODAY DATE & TASKS LEDGER */}
        <div className="bg-canvas flex flex-col flex-1 min-h-0 justify-between">
          <div>
            {/* Date Header */}
            <div className="border-b-2 border-grid p-4 xl:p-5">
              <div className="flex items-baseline justify-between gap-3">
                <div className="flex items-baseline gap-3">
                  <span className="font-display text-4xl xl:text-5xl font-black leading-none tabular-nums text-foreground">
                    {dayNum}
                  </span>
                  <div>
                    <h2 className="font-display text-lg xl:text-xl uppercase leading-none tracking-tight">{weekday}</h2>
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

            {/* Task list rows */}
            {isDemoMode && (
              <div className="p-2 border-b border-grid">
                <DemoModeBanner onExit={disableDemoMode} />
              </div>
            )}
            {conflictError && (
              <div className="p-2 border-b border-grid">
                <TaskConflictBanner
                  message={`Revision conflict: ${conflictError.message}`}
                  onResolve={refetchTasks}
                  onDismiss={clearConflict}
                />
              </div>
            )}
            {error && !conflictError && (
              <div className="p-2 border-b border-grid">
                <TaskErrorBanner
                  message={error.message}
                  onDismiss={clearError}
                  onEnableDemo={enableDemoMode}
                />
              </div>
            )}
            <div className="divide-y divide-grid/20 overflow-y-auto max-h-[380px] xl:max-h-[460px]">
              {open.map((t) => {
                const project = projectById(t.projectId);
                const isSelected = active?.id === t.id;
                return (
                  <div
                    key={t.id}
                    onClick={() => selectTask(t.id)}
                    className={`group flex cursor-pointer items-center justify-between px-4 py-2.5 transition-colors hover:bg-panel ${isSelected ? "bg-panel font-bold border-l-4 border-grid" : ""}`}
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
                      <span className="text-xs tracking-tight">{t.title}</span>
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
              <div className="border-t-2 border-grid">
                <div className="bg-panel px-4 py-1 text-[8px] font-black uppercase tracking-[0.18em] text-muted-foreground border-b border-grid/20">
                  Done · {done.length}
                </div>
                <div className="divide-y divide-grid/20 opacity-60 max-h-[140px] overflow-y-auto">
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
      </aside>
    </main>
  );
}
