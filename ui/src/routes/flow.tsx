import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { GridPanel, StatBlock, TaskRow, toneBg } from "@/components/blockday-ui";
import { historicalDays, projectById, type Task } from "@/lib/blockday-data";
import { morph, useBlockday } from "@/lib/blockday-store";

export const Route = createFileRoute("/flow")({
  head: () => ({
    meta: [
      { title: "Flow — Blockday" },
      { name: "description", content: "Your day as a vertical timeline: blocks sized by duration, free gaps left visible." },
      { property: "og:title", content: "Flow — Blockday" },
      { property: "og:description", content: "Your day as a vertical timeline: blocks sized by duration, free gaps left visible." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: FlowPage,
});

const START = 6 * 60; // 06:00
const END = 22 * 60; // 22:00
const HOUR = 56; // px per hour
const HOURS = Array.from({ length: (END - START) / 60 + 1 }, (_, i) => START / 60 + i);

function clock(min: number) {
  return `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
}

function FlowPage() {
  const { tasks, setTasks, dayOffset } = useBlockday();
  const isToday = dayOffset === 0;
  const dayTasks: Task[] = dayOffset < 0 ? (historicalDays[dayOffset] ?? []) : tasks;

  const [nowMin, setNowMin] = useState<number | null>(null);
  useEffect(() => {
    const update = () => {
      const d = new Date();
      setNowMin(d.getHours() * 60 + d.getMinutes());
    };
    update();
    const id = window.setInterval(update, 60000);
    return () => window.clearInterval(id);
  }, []);

  const toggle = (id: string) => {
    if (!isToday) return;
    morph(() => setTasks((ts) => ts.map((t) => (t.id === id ? { ...t, done: !t.done } : t))));
  };

  const open = dayTasks.filter((t) => !t.done);
  const done = dayTasks.filter((t) => t.done);
  const scheduled = open.filter((t) => t.start !== undefined).sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
  const floating = open.filter((t) => t.start === undefined);
  const frog = open.find((t) => t.frog);

  const timelineH = ((END - START) / 60) * HOUR;
  const bodyH = timelineH + (floating.length ? 56 + floating.length * 66 : 0);
  const scheduledMin = scheduled.reduce((sum, t) => sum + t.estimate, 0);
  const wrapsAt = scheduled.length ? Math.max(...scheduled.map((t) => (t.start ?? 0) + t.estimate)) : 0;
  const showNow = isToday && nowMin !== null && nowMin >= START && nowMin <= END;

  return (
    <main className="page-grid grid-cols-[minmax(0,11fr)_20px_minmax(0,10fr)_240px]">
      {/* Timeline */}
      <section className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)] gap-[5px]">
        <GridPanel className="flex items-end justify-between gap-4 py-5">
          <div>
            <p className="section-label">Hardest first, softest last</p>
            <h1 className="mt-1 font-display text-4xl uppercase leading-[0.92]">Day flow</h1>
          </div>
          <p className="pb-1 text-right text-[9px] font-black uppercase tracking-[0.14em] text-muted-foreground">
            Start 06:00 · Wraps {wrapsAt ? clock(START + wrapsAt) : "—"}
            <span className="mt-1 block">{scheduled.length} placed · {floating.length} floating</span>
          </p>
        </GridPanel>

        <GridPanel>
          <div className="relative" style={{ height: bodyH }}>
            {HOURS.map((h, i) => (
              <div key={h} className="absolute left-0 right-0 flex items-center" style={{ top: i * HOUR }}>
                <span className="w-16 pl-1 text-[9px] font-black uppercase tracking-[0.12em] text-muted-foreground">{String(h).padStart(2, "0")}:00</span>
                <span className="h-[2px] flex-1 bg-grid/25" />
              </div>
            ))}

            {scheduled.map((t) => {
              const project = projectById(t.projectId);
              const top = ((t.start ?? 0) / 60) * HOUR;
              const height = Math.max(34, (t.estimate / 60) * HOUR - 6);
              return (
                <div
                  key={t.id}
                  className={`flow-block animate-slide-panel ${project ? toneBg[project.tone] : "bg-canvas"}`}
                  style={{ top, height }}
                >
                  <p className={`truncate text-[13px] font-bold leading-tight ${project && project.tone !== "yellow" && project.tone !== "neutral" ? "text-on-primary" : "text-block-ink"}`}>{t.title}</p>
                  <p className={`mt-0.5 text-[8px] font-black uppercase tracking-[0.14em] ${project && project.tone !== "yellow" && project.tone !== "neutral" ? "text-on-primary/80" : "text-muted-foreground"}`}>
                    {clock(START + (t.start ?? 0))} · {t.estimate} min{project ? ` · ${project.name}` : ""}
                  </p>
                </div>
              );
            })}

            {showNow && (
              <div className="absolute left-0 right-0 z-10" style={{ top: ((nowMin - START) / 60) * HOUR }}>
                <div className="flex items-center">
                  <span className="bg-block-red px-1.5 py-0.5 text-[8px] font-black uppercase tracking-[0.12em] text-on-primary">Now</span>
                  <span className="h-[3px] flex-1 bg-block-red" />
                </div>
              </div>
            )}

            {floating.length > 0 && (
              <>
                <div className="absolute left-16 right-0 flex items-center gap-3" style={{ top: timelineH + 16 }}>
                  <span className="section-label whitespace-nowrap">Floating · unscheduled</span>
                  <span className="h-[2px] flex-1" style={{ border: "1px dashed var(--grid)" }} />
                </div>
                {floating.map((t, i) => {
                  const project = projectById(t.projectId);
                  return (
                    <div
                      key={t.id}
                      className="flow-block flow-floating animate-slide-panel"
                      style={{ top: timelineH + 52 + i * 66, height: 58, animationDelay: `${i * 70}ms` }}
                    >
                      <p className="truncate text-[13px] font-bold leading-tight">{t.title}</p>
                      <p className="mt-0.5 text-[8px] font-black uppercase tracking-[0.14em] text-muted-foreground">
                        {t.estimate} min{project ? ` · ${project.name}` : ""} · place it anywhere
                      </p>
                    </div>
                  );
                })}
              </>
            )}
          </div>
        </GridPanel>
      </section>

      {/* Energy strip */}
      <div className="energy-strip" aria-hidden>
        <span>High energy</span>
        <i />
        <span>Low</span>
      </div>

      {/* Task workspace */}
      <section className="grid min-h-0 grid-rows-[minmax(0,1fr)] gap-[5px]">
        <GridPanel label={isToday ? `Today's list · ${open.length} open` : `${done.length + open.length} tasks`}>
          <div className="today-ledger -mx-1">
            {open.map((t) => (
              <TaskRow key={t.id} task={t} compact onToggle={isToday ? () => toggle(t.id) : undefined} />
            ))}
            {!open.length && <p className="px-1 py-3 text-xs text-muted-foreground">Everything is done.</p>}
          </div>
          {done.length > 0 && (
            <>
              <p className="mt-4 border-t-2 border-grid pt-3 text-[9px] font-black uppercase tracking-[0.18em] text-muted-foreground">Done · {done.length}</p>
              <div className="today-done -mx-1">
                {done.map((t) => (
                  <TaskRow key={t.id} task={t} compact onToggle={isToday ? () => toggle(t.id) : undefined} />
                ))}
              </div>
            </>
          )}
        </GridPanel>
      </section>

      {/* Utility rail */}
      <aside className="flex min-h-0 flex-col gap-[5px]">
        {frog && (
          <div className="stat-block bg-block-red text-on-primary">
            <p className="text-[9px] font-black uppercase tracking-[0.18em]">Eat the frog</p>
            <p className="mt-2 text-sm font-bold leading-snug">{frog.title}</p>
            {isToday && (
              <button onClick={() => toggle(frog.id)} className="mt-3 border-2 border-on-primary px-3 py-1.5 text-[9px] font-black uppercase tracking-[0.16em] transition-colors hover:bg-on-primary hover:text-block-red">
                Eat it
              </button>
            )}
          </div>
        )}
        <div className="grid grid-cols-2 gap-[5px]">
          <StatBlock value={`${Math.floor(scheduledMin / 60)}h${String(scheduledMin % 60).padStart(2, "0")}`} label="Planned" tone="blue" />
          <StatBlock value={floating.length} label="Floating" tone="yellow" />
        </div>
        <GridPanel label="On this day" className="flex-1">
          <ul className="space-y-2">
            {[...new Set(open.map((t) => t.projectId))].map((id) => {
              const p = projectById(id);
              if (!p) return null;
              return (
                <li key={id} className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.1em]">
                  <span className={`project-swatch ${toneBg[p.tone]}`} /> {p.name}
                </li>
              );
            })}
          </ul>
        </GridPanel>
      </aside>
    </main>
  );
}
