import { createFileRoute } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { Check, Plus, Trash2 } from "lucide-react";
import { DemoModeBanner, TaskConflictBanner, TaskErrorBanner } from "@/components/blockday-ui";
import { useBlockday } from "@/lib/blockday-store";

export const Route = createFileRoute("/projects")({
  head: () => ({ meta: [{ title: "Projects — Blockday" }] }),
  component: ProjectsPage,
});

export function ProjectsPage() {
  const {
    tasks,
    projects,
    projectsLoading,
    projectsError,
    createProject,
    updateProject,
    deleteProject,
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
  const [projectName, setProjectName] = useState("");
  const [rename, setRename] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const selected = projects.find((project) => project.id === selectedProjectId);
  const selectedTasks = tasks.filter((task) => task.projectId === selected?.id);

  const submitProject = async (event: FormEvent) => {
    event.preventDefault();
    if (!projectName.trim()) return;
    setActionError(null);
    try {
      const project = await createProject({ name: projectName.trim() });
      setSelectedProjectId(project.id);
      setProjectName("");
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    }
  };
  const submitTask = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected || !taskTitle.trim()) return;
    try {
      await addTask(taskTitle.trim(), selected.id);
      setTaskTitle("");
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <main className="page-grid grid-cols-1 overflow-y-auto bg-grid p-1">
      <section className="bg-canvas p-5">
        <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
          <div>
            <span className="section-label text-muted-foreground">Workspace</span>
            <h1 className="mt-1 font-display text-4xl uppercase">Projects</h1>
          </div>
          <form onSubmit={submitProject} className="flex border-2 border-grid">
            <input
              aria-label="Project name"
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="New project name"
              className="bg-transparent px-3 py-2 text-sm outline-none"
            />
            <button className="border-l-2 border-grid px-3 text-xs font-black uppercase">
              <Plus className="mr-1 inline size-3" />
              Create
            </button>
          </form>
        </header>
        {isDemoMode && <DemoModeBanner onExit={disableDemoMode} />}
        {projectsLoading && (
          <p role="status" className="py-6 text-sm">
            Loading projects…
          </p>
        )}
        {projectsError && !isDemoMode && (
          <div role="alert" className="my-3 border border-red-600 p-3 text-sm">
            Unable to load projects: {projectsError.message}.{" "}
            <button className="underline" onClick={() => location.reload()}>
              Retry
            </button>{" "}
            <button className="ml-3 underline" onClick={enableDemoMode}>
              Enter demo mode
            </button>
          </div>
        )}
        {actionError && (
          <p role="alert" className="my-3 border border-red-600 p-3 text-sm">
            {actionError}
          </p>
        )}
        {!projectsLoading && !projectsError && !projects.length && (
          <p className="py-6 text-sm text-muted-foreground">
            No projects yet. Create one to organize tasks.
          </p>
        )}
        {projects.length > 0 && (
          <div className="grid gap-1 lg:grid-cols-[260px_minmax(0,1fr)]">
            <nav aria-label="Projects" className="border-2 border-grid">
              {projects.map((project) => (
                <button
                  key={project.id}
                  onClick={() => setSelectedProjectId(project.id)}
                  className={`block w-full border-b border-grid/30 px-3 py-3 text-left text-sm ${project.id === selected?.id ? "bg-muted font-bold" : ""}`}
                >
                  <div className="flex items-center justify-between">
                    <span>{project.name}</span>
                    {project.syncStatus && project.syncStatus !== "synced" && (
                      <span
                        className={`px-1 py-0.5 text-[8px] font-mono uppercase tracking-wider ${project.syncStatus === "conflict" || project.syncStatus === "failed" ? "bg-block-red text-white" : project.syncStatus === "syncing" ? "bg-block-blue text-white" : "bg-block-yellow text-black"}`}
                      >
                        {project.syncStatus === "pending" ? "Queued" : project.syncStatus}
                      </span>
                    )}
                  </div>
                  <span className="block truncate font-mono text-[9px] text-muted-foreground">
                    {project.id}
                  </span>
                </button>
              ))}
            </nav>
            {selected && (
              <section className="min-w-0 border-2 border-grid p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <span className="section-label text-muted-foreground">Backend project</span>
                    <h2 className="font-display text-3xl uppercase">{selected.name}</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {selected.description || "No description"} · {selected.status}{" "}
                      {selected.syncStatus && selected.syncStatus !== "synced"
                        ? `· [${selected.syncStatus.toUpperCase()}]`
                        : ""}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <form
                      onSubmit={async (event) => {
                        event.preventDefault();
                        if (!rename.trim()) return;
                        try {
                          await updateProject(selected.id, { name: rename }, selected.revision);
                          setRename("");
                          setActionError(null);
                        } catch (cause) {
                          setActionError(cause instanceof Error ? cause.message : String(cause));
                        }
                      }}
                      className="flex border border-grid"
                    >
                      <input
                        aria-label="Rename project"
                        value={rename}
                        onChange={(event) => setRename(event.target.value)}
                        placeholder="Rename"
                        className="w-24 bg-transparent px-2 text-xs"
                      />
                      <button className="px-2 text-xs">Save</button>
                    </form>
                    <button
                      aria-label={`Delete ${selected.name}`}
                      onClick={async () => {
                        if (!confirm(`Delete ${selected.name}?`)) return;
                        try {
                          await deleteProject(selected.id);
                          setActionError(null);
                        } catch (cause) {
                          setActionError(cause instanceof Error ? cause.message : String(cause));
                        }
                      }}
                      className="border border-grid px-2"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </div>
                </div>
                <form onSubmit={submitTask} className="mt-5 flex border-2 border-grid">
                  <input
                    aria-label="Task title"
                    value={taskTitle}
                    onChange={(event) => setTaskTitle(event.target.value)}
                    placeholder={`Add task to ${selected.name}`}
                    className="flex-1 bg-transparent px-3 py-2 text-sm outline-none"
                  />
                  <button className="border-l-2 border-grid px-3 text-xs font-black uppercase">
                    Add task
                  </button>
                </form>
                <h3 className="mt-5 text-xs font-black uppercase tracking-wider">Project Tasks</h3>
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
                <ul className="mt-4 divide-y divide-grid/20">
                  {selectedTasks.map((task) => (
                    <li
                      key={task.id}
                      onClick={() => void toggleTask(task.id).catch(() => {})}
                      className="flex cursor-pointer items-center gap-2 py-2"
                    >
                      <button
                        aria-label={`${task.done ? "Reopen" : "Complete"} ${task.title}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          void toggleTask(task.id).catch(() => {});
                        }}
                        className="border border-grid p-1"
                      >
                        {task.done && <Check className="size-3" />}
                      </button>
                      <span className={task.done ? "text-sm line-through opacity-50" : "text-sm"}>
                        {task.title}
                      </span>
                      {task.syncStatus && task.syncStatus !== "synced" && (
                        <span
                          className={`ml-2 px-1 py-0.5 text-[8px] font-mono uppercase tracking-wider ${task.syncStatus === "conflict" || task.syncStatus === "failed" ? "bg-block-red text-white" : task.syncStatus === "syncing" ? "bg-block-blue text-white" : "bg-block-yellow text-black"}`}
                        >
                          {task.syncStatus === "pending" ? "Queued" : task.syncStatus}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
                {!selectedTasks.length && (
                  <p className="py-4 text-sm text-muted-foreground">
                    No tasks are associated with this project.
                  </p>
                )}
              </section>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
