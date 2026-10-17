import { createFileRoute } from "@tanstack/react-router";
import { Pause, Play, RotateCcw, SkipForward } from "lucide-react";
import { GridPanel, IconButton, StatBlock, toneBg } from "@/components/blockday-ui";
import { projectById } from "@/lib/blockday-data";
import { formatClock, useBlockday, type TimerMode } from "@/lib/blockday-store";

export const Route = createFileRoute("/focus")({
  head: () => ({
    meta: [
      { title: "Focus — Blockday" },
      { name: "description", content: "A big Mondrian timer: focus and break sessions for the block you picked." },
      { property: "og:title", content: "Focus — Blockday" },
      { property: "og:description", content: "A big Mondrian timer: focus and break sessions for the block you picked." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: FocusPage,
});

function FocusPage() {
  const { tasks, timer, setMode, toggleTimer, resetTimer, skipTimer, selectTask } = useBlockday();
  const active = tasks.find((t) => t.id === timer.taskId) ?? tasks[0];
  const activeProject = active ? projectById(active.projectId) : undefined;

  return (
    <main className="page-grid grid-cols-[minmax(0,1fr)_340px]">
      <section className="grid min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-[5px]">
        <GridPanel>
          <div className="mx-auto flex max-w-xl flex-col items-center">
            <div className="grid w-full grid-cols-2 gap-2">
              {(["Focus", "Break"] as TimerMode[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`mode-tab ${timer.mode === m ? (m === "Focus" ? "bg-block-red text-on-primary" : "bg-block-blue text-on-primary") : "bg-canvas text-foreground hover:-translate-y-0.5"} transition-transform`}
                >
                  {m}
                </button>
              ))}
            </div>

            <div className="timer-face mt-6 w-full text-center">
              <div className="timer-fill" style={{ transform: `scaleX(${timer.seconds / Math.max(1, timer.total)})` }} />
              <div className="relative z-10">
                <p className="font-display text-[6.5rem] leading-none tabular-nums">{formatClock(timer.seconds)}</p>
                <p className="mt-3 truncate text-[10px] font-black uppercase tracking-[0.16em]">
                  {active ? active.title : "Choose a block"}
                  {activeProject ? ` · ${activeProject.name}` : ""}
                </p>
              </div>
            </div>

            <div className="mt-5 grid w-full grid-cols-[minmax(0,1fr)_56px_56px] gap-2">
              <button onClick={toggleTimer} className="timer-action h-12 text-sm">
                {timer.running ? <><Pause /> Pause</> : <><Play /> Start</>}
              </button>
              <IconButton aria-label="Reset timer" onClick={resetTimer} className="h-12 w-12"><RotateCcw /></IconButton>
              <IconButton aria-label="Skip to next session" onClick={skipTimer} className="h-12 w-12"><SkipForward /></IconButton>
            </div>
          </div>
        </GridPanel>

        <GridPanel label="Pick a block">
          <div className="grid grid-cols-2 gap-2">
            {tasks.filter((t) => !t.done).map((t) => {
              const p = projectById(t.projectId);
              const selected = active?.id === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => selectTask(t.id)}
                  className={`flex min-h-14 items-center gap-3 border-2 border-grid bg-panel px-3 py-2 text-left transition-transform hover:-translate-y-0.5 ${selected ? "task-selected" : ""}`}
                >
                  <span className={`size-4 flex-shrink-0 border border-grid ${p ? toneBg[p.tone] : ""}`} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-bold">{t.title}</span>
                    <span className="text-[8px] font-black uppercase tracking-[0.14em] text-muted-foreground">{t.estimate} min · {t.mood}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </GridPanel>
      </section>

      <aside className="flex min-h-0 flex-col gap-[5px]">
        <GridPanel label="Sessions today">
          <div className="flex flex-wrap gap-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <span
                key={i}
                className={`size-9 border-2 border-grid ${i < timer.sessions ? "bg-block-red" : i === timer.sessions && timer.running && timer.mode === "Focus" ? "animate-pulse bg-block-yellow" : "bg-canvas"}`}
              />
            ))}
          </div>
          <p className="mt-3 text-[9px] font-black uppercase tracking-[0.14em] text-muted-foreground">{timer.sessions} of 8 focus sessions finished</p>
        </GridPanel>
        <div className="grid grid-cols-2 gap-[5px]">
          <StatBlock value={timer.mode} label="Current mode" tone={timer.mode === "Focus" ? "red" : "blue"} />
          <StatBlock value={timer.sessions} label="Sessions done" tone="yellow" />
        </div>
        <GridPanel label="The rhythm" className="flex-1">
          <ul className="space-y-2.5 text-[10px] font-bold uppercase tracking-[0.12em]">
            <li>25 minutes of focus.</li>
            <li>5 minutes of break.</li>
            <li>Skip moves you to the next phase.</li>
          </ul>
        </GridPanel>
      </aside>
    </main>
  );
}
