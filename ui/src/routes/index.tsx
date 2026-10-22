import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { ArchiveRestore, Plus, Zap } from "lucide-react";
import {
  DemoModeBanner,
  GridPanel,
  HeatMap,
  TaskConflictBanner,
  TaskEditDialog,
  TaskEmptyState,
  TaskErrorBanner,
  TaskRow,
  toneBg,
} from "@/components/blockday-ui";
import { MondrianMosaic } from "@/components/mondrian-mosaic";
import { dailyCompletions, historicalDays, projectById, type Task } from "@/lib/blockday-data";
import { dayLabel, morph, useBlockday } from "@/lib/blockday-store";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Today — Blockday" },
      {
        name: "description",
        content: "Compose today: blocks with deadline colors, a done list, and past-day replays.",
      },
      { property: "og:title", content: "Today — Blockday" },
      {
        property: "og:description",
        content: "Compose today: blocks with deadline colors, a done list, and past-day replays.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: TodayPage,
});

function TodayPage() {
  const {
    tasks,
    addTask,
    updateTask,
    toggleTask,
    conflictError,
    clearConflict,
    error,
    clearError,
    refetchTasks,
    isDemoMode,
    enableDemoMode,
    disableDemoMode,
    archived,
    archive,
    query,
    dayOffset,
    today,
  } = useBlockday();
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const replay = dayOffset < 0;
  const dayTasks: Task[] = replay ? (historicalDays[dayOffset] ?? []) : tasks;
  const q = query.trim().toLowerCase();
  const match = (t: Task) => t.title.toLowerCase().includes(q);

  const open = dayTasks.filter((t) => !t.done && !archived.includes(t.id) && match(t));
  const doneToday = dayTasks.filter((t) => t.done && !archived.includes(t.id) && match(t));
  const sorted = [...open].sort((a, b) => (a.dueIn ?? 99) - (b.dueIn ?? 99));
  const overdue = sorted.filter((t) => t.dueIn !== null && t.dueIn < 0).length;
  const plannedMin = sorted.reduce((sum, t) => sum + t.estimate, 0);
  const capacity = dayTasks.length ? Math.round((doneToday.length / dayTasks.length) * 100) : 0;

  const toggle = (id: string) => {
    if (replay) return;
    void toggleTask(id).catch(() => {});
  };

  const date = new Date(today ?? Date.now());
  date.setDate(date.getDate() + dayOffset);
  const dayNum = date.getDate();
  const weekday = date.toLocaleDateString("en-US", { weekday: "long" });
  const month = date.toLocaleDateString("en-US", { month: "long", year: "numeric" });

  return (
    <main className="page-grid grid-cols-[minmax(0,1fr)_minmax(260px,300px)]">
      <section className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)_auto] gap-[5px]">
        {/* Header: big date numeral + headline, small capacity mosaic as the only colour block */}
        <div className="grid grid-cols-[auto_minmax(0,1fr)_180px] gap-[5px]">
          <div className="grid place-items-center bg-block-yellow px-6 text-block-ink">
            <span className="font-display text-7xl leading-none tabular-nums">{dayNum}</span>
          </div>
          <GridPanel className="py-5">
            <p className="section-label">
              {replay ? `Replay · ${dayLabel(today, dayOffset)}` : `${weekday} · ${month}`}
            </p>
            <h1 className="mt-2 font-display text-4xl uppercase leading-[0.92]">
              Make the hard thing happen.
            </h1>
            <p className="mt-3 text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
              {sorted.length} open · {overdue} overdue · {Math.floor(plannedMin / 60)}h{" "}
              {plannedMin % 60}m planned
            </p>
          </GridPanel>
          <div className="min-h-32">
            <MondrianMosaic>
              <p className="flex items-center gap-1 font-display text-xl leading-none tabular-nums">
                <Zap className="size-3 fill-current" />
                {capacity}%
              </p>
              <p className="mt-0.5 text-[8px] font-bold uppercase tracking-[0.2em]">Capacity</p>
            </MondrianMosaic>
          </div>
        </div>

        <GridPanel
          className="px-6 flex min-h-0 flex-col overflow-hidden"
          label={`Blocks · ${sorted.length}`}
          aside={
            <div className="flex items-center gap-2.5">
              <span className="text-[9px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
                Sorted by deadline
              </span>
              {!replay && (
                <button
                  type="button"
                  onClick={() => setIsCreating(true)}
                  className="ghost-chip px-2 py-0.5 text-[9px]"
                  aria-label="Add new block"
                >
                  <Plus className="mr-0.5 inline size-2.5" /> New
                </button>
              )}
            </div>
          }
        >
          {isDemoMode && <DemoModeBanner onExit={disableDemoMode} />}
          {conflictError && (
            <TaskConflictBanner
              message={`Revision conflict: ${conflictError.message}`}
              onResolve={refetchTasks}
              onDismiss={clearConflict}
            />
          )}
          {error && !conflictError && (
            <TaskErrorBanner
              message={error.message}
              onDismiss={clearError}
              onEnableDemo={enableDemoMode}
            />
          )}

          <div className="today-ledger flex-1 min-h-0 overflow-y-auto">
            {sorted.map((t) => (
              <TaskRow
                key={t.id}
                task={t}
                onSelect={() => setEditingTask(t)}
                onToggle={replay ? undefined : () => toggle(t.id)}
              />
            ))}
            {!sorted.length && <TaskEmptyState message="No blocks match this view." />}
          </div>
        </GridPanel>

        <GridPanel
          className="today-done px-6 py-4 flex min-h-0 flex-col overflow-hidden max-h-48 shrink-0"
          label={`Done today · ${doneToday.length}`}
          aside={
            <button
              disabled={!doneToday.length || replay}
              onClick={() => morph(() => archive(doneToday.map((t) => t.id)))}
              title="Locally hide completed blocks on this device"
              className="flex items-center gap-1.5 border border-grid px-2 py-1 text-[9px] font-black uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 transition-colors"
            >
              <ArchiveRestore className="size-3" /> Archive (this device)
            </button>
          }
        >
          <div className="today-ledger flex-1 min-h-0 overflow-y-auto">
            {doneToday.map((t) => (
              <TaskRow
                key={t.id}
                task={t}
                compact
                onSelect={() => setEditingTask(t)}
                onToggle={replay ? undefined : () => toggle(t.id)}
              />
            ))}
            {!doneToday.length && (
              <p className="pt-3 text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
                Nothing finished yet.
              </p>
            )}
          </div>
        </GridPanel>
      </section>

      {editingTask && (
        <TaskEditDialog
          open={Boolean(editingTask)}
          task={editingTask}
          onClose={() => setEditingTask(null)}
          onSave={async (fields) => {
            const updates: Partial<Task> = {
              title: fields.title,
            };
            if (fields.description !== undefined) updates.description = fields.description;
            if (fields.projectId !== undefined) updates.projectId = fields.projectId;
            if (fields.softDeadline !== undefined) updates.softDeadline = fields.softDeadline;
            if (fields.hardDeadline !== undefined) updates.hardDeadline = fields.hardDeadline;
            if (fields.estimate !== undefined) updates.estimate = fields.estimate;
            if (fields.mood !== undefined) updates.mood = fields.mood;
            if (fields.frog !== undefined) updates.frog = fields.frog;
            await updateTask(editingTask.id, updates);
            setEditingTask(null);
          }}
        />
      )}

      {isCreating && (
        <TaskEditDialog
          open={isCreating}
          onClose={() => setIsCreating(false)}
          onSave={async (fields) => {
            await addTask(fields.title, fields.projectId, fields.estimate, fields.mood, {
              softDeadline: fields.softDeadline,
              hardDeadline: fields.hardDeadline,
            });
            setIsCreating(false);
          }}
        />
      )}

      {/* Secondary rail: one quiet column instead of competing cards */}
      <aside className="flex min-h-0 flex-col gap-[5px]">
        <GridPanel label="Today in numbers">
          <div className="today-rail-row">
            <span>Done</span>
            <span className="font-display text-2xl tabular-nums">{doneToday.length}</span>
          </div>
          <div className="today-rail-row">
            <span>Open</span>
            <span className="font-display text-2xl tabular-nums">{sorted.length}</span>
          </div>
          <div className="today-rail-row">
            <span className="flex items-center gap-2">
              <span className="deadline-strip deadline-overdue h-3" />
              Overdue
            </span>
            <span className="font-display text-2xl tabular-nums">{overdue}</span>
          </div>
          <div className="today-rail-row border-b-0">
            <span>Planned</span>
            <span className="font-display text-lg tabular-nums">
              {Math.floor(plannedMin / 60)}h {plannedMin % 60}m
            </span>
          </div>
        </GridPanel>
        <GridPanel label="Momentum · 5 weeks">
          <HeatMap />
        </GridPanel>
        <GridPanel
          label="Projects on this day"
          className="flex-1 min-h-0 flex flex-col justify-between overflow-hidden"
        >
          <ul className="flex-1 min-h-0 overflow-y-auto space-y-0.5">
            {[...new Set(dayTasks.map((t) => t.projectId))].map((id) => {
              const project = projectById(id);
              if (!project) return null;
              const count = dayTasks.filter((t) => t.projectId === id).length;
              return (
                <li key={id} className="today-rail-row">
                  <span className="flex items-center gap-2">
                    <span className={`project-swatch ${toneBg[project.tone]}`} />
                    {project.name}
                  </span>
                  <span className="tabular-nums">{count}</span>
                </li>
              );
            })}
          </ul>
          <ul className="mt-5 shrink-0 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground border-t border-grid/20 pt-3">
            <li className="flex items-center gap-2">
              <span className="deadline-strip deadline-overdue h-3" />
              Overdue
            </li>
            <li className="flex items-center gap-2">
              <span className="deadline-strip deadline-today h-3" />
              Today
            </li>
            <li className="flex items-center gap-2">
              <span className="deadline-strip deadline-upcoming h-3" />
              Upcoming
            </li>
            <li className="flex items-center gap-2">
              <span className="deadline-strip deadline-none h-3" />
              No date
            </li>
          </ul>
        </GridPanel>
      </aside>
    </main>
  );
}
