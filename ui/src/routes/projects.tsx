import { createFileRoute } from "@tanstack/react-router";
import { Check, Plus } from "lucide-react";
import { useState } from "react";
import { GridPanel, DemoModeBanner, TaskConflictBanner, TaskEmptyState, TaskErrorBanner, toneBg, toneText } from "@/components/blockday-ui";
import { advancements, projectById, projects, workspace } from "@/lib/blockday-data";
import { morph, useBlockday } from "@/lib/blockday-store";

export const Route = createFileRoute("/projects")({
  head: () => ({
    meta: [
      { title: "Projects — Blockday" },
      { name: "description", content: "Nested projects, featured blocks, and a workspace of ideas, notes, tasks, and drafts." },
      { property: "og:title", content: "Projects — Blockday" },
      { property: "og:description", content: "Nested projects, featured blocks, and a workspace of ideas, notes, tasks, and drafts." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: ProjectsPage,
});

export function ProjectsPage() {
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
    selectedProjectId,
    setSelectedProjectId,
  } = useBlockday();
  const [taskInput, setTaskInput] = useState("");

  const selected = projectById(selectedProjectId) ?? projects[0]!;
  const featured = projects.filter((p) => p.active).slice(0, 3);
  const ws = workspace[selected.id] ?? { ideas: [], notes: [], drafts: [] };
  const roots = projects.filter((p) => !p.parent);

  const selectedTasks = tasks.filter((t) => t.projectId === selected.id);
  const openTasks = selectedTasks.filter((t) => !t.done);
  const doneTasks = selectedTasks.filter((t) => t.done);
  const totalEstimate = openTasks.reduce((s, t) => s + t.estimate, 0);

  const handleAddTask = (e: React.FormEvent) => {
    e.preventDefault();
    if (!taskInput.trim()) return;
    void addTask(taskInput.trim(), selected.id, 20).catch(() => {});
    setTaskInput("");
  };

  const handleToggleTask = (id: string) => {
    void toggleTask(id).catch(() => {});
  };

  return (
    <main className="page-grid grid-cols-1 overflow-y-auto">
      <div className="flex min-h-0 flex-col gap-[5px] bg-grid">
        {/* COMPACT NESTED PROJECT NAVIGATOR */}
        <section className="border-b-[5px] border-grid bg-canvas px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="text-[9px] font-black uppercase tracking-[0.2em] text-muted-foreground">
                Projects Navigator
              </span>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {roots.map((root) => {
                const children = projects.filter((p) => p.parent === root.id);
                const isRootActive = selected.id === root.id;
                return (
                  <div key={root.id} className="flex items-center gap-1 rounded-none border border-grid bg-panel/40 p-1">
                    <button
                      onClick={() => morph(() => setSelectedProjectId(root.id))}
                      className={`project-crumb ${isRootActive ? "project-crumb-active" : ""}`}
                    >
                      <span className={`project-swatch ${toneBg[root.tone]}`} />
                      <span>{root.name}</span>
                      <span className="font-mono text-[9px] opacity-75">{root.progress}%</span>
                    </button>

                    {children.length > 0 && <span className="text-muted-foreground px-0.5 text-xs">/</span>}

                    {children.map((child) => {
                      const isChildActive = selected.id === child.id;
                      return (
                        <button
                          key={child.id}
                          onClick={() => morph(() => setSelectedProjectId(child.id))}
                          className={`project-crumb ${isChildActive ? "project-crumb-active" : ""}`}
                        >
                          <span className={`project-swatch ${toneBg[child.tone]}`} />
                          <span>{child.name}</span>
                          <span className="font-mono text-[9px] opacity-75">{child.progress}%</span>
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        {/* FEATURED PROJECT BLOCKS */}
        <section className="grid grid-cols-1 gap-[5px] sm:grid-cols-3">
          {featured.map((p) => {
            const isCurrent = p.id === selected.id;
            return (
              <button
                key={p.id}
                onClick={() => morph(() => setSelectedProjectId(p.id))}
                className={`stat-block min-h-36 text-left transition-all hover:-translate-y-0.5 ${toneBg[p.tone]} ${toneText[p.tone]} ${isCurrent ? "outline outline-4 -outline-offset-4 outline-grid shadow-lg" : "opacity-95"}`}
              >
                <div className="flex items-start justify-between">
                  <p className="font-display text-2xl uppercase leading-none tracking-tight">{p.name}</p>
                  <span className="border border-current px-1.5 py-0.5 text-[8px] font-black uppercase tracking-wider">
                    {p.tone}
                  </span>
                </div>
                <p className="mt-2 text-[9px] font-bold uppercase tracking-[0.14em] opacity-80 line-clamp-1">
                  {p.summary}
                </p>
                <div className="mt-auto pt-4 flex items-baseline justify-between">
                  <p className="font-display text-5xl leading-none tabular-nums font-black">{p.progress}%</p>
                  <span className="text-[9px] font-black uppercase tracking-[0.18em]">complete</span>
                </div>
              </button>
            );
          })}
        </section>

        {/* SELECTED PROJECT HEADER & FAST ENTRY */}
        <section className="border-b-[5px] border-grid bg-canvas p-5">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0">
              <span className="section-label text-muted-foreground">
                {selected.parent ? `${projectById(selected.parent)?.name} · ` : ""}Project Workspace
              </span>
              <h1 className="mt-1 font-display text-4xl uppercase leading-none tracking-tight">
                {selected.name}
              </h1>
              <p className="mt-2 text-xs font-bold text-muted-foreground max-w-2xl leading-relaxed">
                {selected.summary}
              </p>
            </div>

            <div className="flex flex-col gap-2 min-w-[240px]">
              <div className="flex items-center justify-between text-[9px] font-black uppercase tracking-[0.16em] text-muted-foreground">
                <span>{openTasks.length} Open · {totalEstimate}M Work</span>
                <span>{selected.progress}% Complete</span>
              </div>
              <div className="h-3 w-full border-2 border-grid bg-canvas">
                <div className={`h-full ${toneBg[selected.tone]}`} style={{ width: `${selected.progress}%` }} />
              </div>
            </div>
          </div>

          {/* Quick task adder for this project */}
          <form onSubmit={handleAddTask} className="mt-4 flex items-center border-2 border-grid bg-panel/30">
            <Plus className="ml-3 size-4 text-muted-foreground" />
            <input
              value={taskInput}
              onChange={(e) => setTaskInput(e.target.value)}
              placeholder={`Add task to ${selected.name}...`}
              className="flex-1 bg-transparent px-3 py-2 text-xs font-medium outline-none placeholder:text-muted-foreground"
            />
            <button
              type="submit"
              className="border-l-2 border-grid bg-canvas px-4 py-2 text-[9px] font-black uppercase tracking-[0.14em] hover:bg-block-ink hover:text-canvas transition-colors"
            >
              Add
            </button>
          </form>
        </section>

        {/* WORKSPACE COMPOSITION: TASKS, IDEAS, NOTES & DRAFTS */}
        <div className="grid min-h-0 grid-cols-1 gap-[5px] lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,0.9fr)]">
          {/* COLUMN 1: PROJECT TASKS */}
          <GridPanel label={`Project Tasks · ${selectedTasks.length}`}>
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

            <div className="divide-y divide-grid/20 border-t border-grid/20">
              {openTasks.map((t) => (
                <div
                  key={t.id}
                  onClick={() => handleToggleTask(t.id)}
                  className="flex cursor-pointer items-center justify-between py-2.5 hover:bg-panel px-1 transition-colors"
                >
                  <div className="flex items-center gap-2.5">
                    <button
                      type="button"
                      className="check-button size-4"
                      aria-label={`Complete ${t.title}`}
                    >
                      {t.done && <Check className="size-2.5 stroke-[4]" />}
                    </button>
                    <span className="text-xs font-bold">{t.title}</span>
                  </div>
                  <span className="font-mono text-[9px] text-muted-foreground uppercase">{t.estimate}M</span>
                </div>
              ))}

              {!openTasks.length && (
                <p className="py-4 text-xs text-muted-foreground">All tasks for this project are complete.</p>
              )}

              {doneTasks.length > 0 && (
                <div className="pt-3">
                  <span className="text-[8px] font-black uppercase tracking-[0.18em] text-muted-foreground block mb-1">
                    Completed · {doneTasks.length}
                  </span>
                  {doneTasks.map((t) => (
                    <div
                      key={t.id}
                      onClick={() => handleToggleTask(t.id)}
                      className="flex cursor-pointer items-center justify-between py-1 px-1 opacity-50 hover:opacity-100 transition-opacity"
                    >
                      <span className="text-xs line-through">{t.title}</span>
                      <span className="font-mono text-[9px] text-muted-foreground">{t.estimate}M</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </GridPanel>

          {/* COLUMN 2: IDEAS & NOTES */}
          <div className="flex flex-col gap-[5px]">
            <GridPanel label="Ideas & Directions">
              <ul className="space-y-3">
                {ws.ideas.map((idea, i) => (
                  <li key={idea} className="flex items-baseline gap-3 text-xs font-bold leading-snug">
                    <span className="font-display text-base text-muted-foreground tabular-nums">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span>{idea}</span>
                  </li>
                ))}
                {!ws.ideas.length && (
                  <li className="text-xs text-muted-foreground">No ideas drafted yet.</li>
                )}
              </ul>
            </GridPanel>

            <GridPanel label="Design Notes" className="flex-1">
              <ul className="space-y-2.5">
                {ws.notes.map((note) => (
                  <li
                    key={note}
                    className="border-l-[4px] border-grid pl-3 text-xs font-medium leading-relaxed bg-panel/30 py-1.5 pr-2"
                  >
                    {note}
                  </li>
                ))}
                {!ws.notes.length && (
                  <li className="text-xs text-muted-foreground">No notes recorded yet.</li>
                )}
              </ul>
            </GridPanel>
          </div>

          {/* COLUMN 3: DRAFT ASSETS & RECENT PROGRESS */}
          <div className="flex flex-col gap-[5px]">
            <GridPanel label="Draft Assets">
              <div className="grid grid-cols-2 gap-2">
                {ws.drafts.map((tone, i) => (
                  <div
                    key={i}
                    className={`flex aspect-square flex-col justify-between border-2 border-grid p-2 text-[8px] font-black uppercase tracking-[0.14em] transition-transform hover:-translate-y-0.5 ${toneBg[tone]} ${toneText[tone]}`}
                  >
                    <span>Draft {String(i + 1).padStart(2, "0")}</span>
                    <span className="text-[14px] font-display font-black leading-none">{tone}</span>
                  </div>
                ))}
                {!ws.drafts.length && (
                  <p className="col-span-2 text-xs text-muted-foreground">No drafts cataloged.</p>
                )}
              </div>
            </GridPanel>

            <GridPanel label="Recent Progress" className="flex-1">
              <ul className="space-y-3">
                {advancements.map((a) => {
                  const p = projectById(a.projectId);
                  return (
                    <li key={a.text} className="flex items-start gap-2 text-[11px] font-bold leading-snug">
                      <span className={`mt-0.5 size-2.5 flex-shrink-0 border border-grid ${p ? toneBg[p.tone] : ""}`} />
                      <div className="min-w-0 flex-1">
                        <p>{a.text}</p>
                        <span className="text-[8px] font-black uppercase tracking-[0.12em] text-muted-foreground">
                          {p?.name} · {a.when}
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </GridPanel>

            {/* Geometric anchor slab */}
            <div className={`h-8 border-2 border-grid ${toneBg[selected.tone]}`} />
          </div>
        </div>
      </div>
    </main>
  );
}
