import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState, type FormEvent } from "react";
import { Check, Plus, Trash2 } from "lucide-react";
import { DemoModeBanner, TaskConflictBanner, TaskErrorBanner } from "@/components/blockday-ui";
import { ProjectDevelopmentSection } from "@/components/project-development-section";
import { useBlockday } from "@/lib/blockday-store";
import {
  cleanProjectDescription,
  getProjectCategory,
  type ProjectCategory,
} from "@/lib/project-categories";
import type { BackendProject } from "@/lib/project-types";
import type { Task } from "@/lib/task-types";

export const Route = createFileRoute("/projects")({
  head: () => ({ meta: [{ title: "Projects — Blockday" }] }),
  component: ProjectsPage,
});

interface ProjectWindowProps {
  category: ProjectCategory;
  categoryLabel: string;
  project: BackendProject | undefined;
  tasks: Task[];
  onAddTask: (title: string, projectId: string) => Promise<void>;
  onToggleTask: (taskId: string) => Promise<void>;
  onRename: (projectId: string, newName: string, revision: number) => Promise<void>;
  onDelete: (projectId: string) => Promise<void>;
  onSwitchCategory: (project: BackendProject) => Promise<void>;
  conflictError: Error | null;
  clearConflict: () => void;
  error: Error | null;
  clearError: () => void;
  refetchTasks: () => void;
  enableDemoMode: () => void;
  setActionError: (msg: string | null) => void;
}

function ProjectWindow({
  category,
  categoryLabel,
  project,
  tasks,
  onAddTask,
  onToggleTask,
  onRename,
  onDelete,
  onSwitchCategory,
  conflictError,
  clearConflict,
  error,
  clearError,
  refetchTasks,
  enableDemoMode,
  setActionError,
}: ProjectWindowProps) {
  const [rename, setRename] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [isSubmittingTask, setIsSubmittingTask] = useState(false);

  if (!project) {
    return (
      <section className="min-w-0 border-2 border-dashed border-grid/40 bg-canvas/60 p-4">
        <div className="flex items-center justify-between">
          <span className="section-label font-bold uppercase tracking-wider text-muted-foreground">
            {categoryLabel}
          </span>
        </div>
        <div className="py-8 text-center">
          <p className="text-sm font-medium text-muted-foreground">No {category} projects yet.</p>
          <p className="mt-1 text-xs text-muted-foreground/80">
            Use the form above to create a {category} project.
          </p>
        </div>
      </section>
    );
  }

  const projectTasks = tasks.filter((t) => t.projectId === project.id);
  const cleanDesc = cleanProjectDescription(project.description);

  return (
    <section className="min-w-0 border-2 border-grid bg-canvas p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="section-label font-bold uppercase tracking-wider text-muted-foreground">
              {categoryLabel}
            </span>
            <button
              type="button"
              onClick={async () => {
                try {
                  await onSwitchCategory(project);
                } catch (cause) {
                  setActionError(cause instanceof Error ? cause.message : String(cause));
                }
              }}
              className="border border-grid/40 bg-muted/40 px-1.5 py-0.5 text-[9px] font-mono uppercase tracking-wider text-muted-foreground hover:bg-muted hover:text-foreground"
              title={`Move to ${category === "college" ? "Personal" : "College"}`}
            >
              Move to {category === "college" ? "Personal" : "College"}
            </button>
          </div>
          <h2 className="font-display text-2xl lg:text-3xl uppercase">{project.name}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {cleanDesc || "No description"} · {project.status}{" "}
            {project.syncStatus && project.syncStatus !== "synced"
              ? `· [${project.syncStatus.toUpperCase()}]`
              : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              if (!rename.trim()) return;
              try {
                await onRename(project.id, rename.trim(), project.revision);
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
              className="w-24 bg-transparent px-2 text-xs outline-none"
            />
            <button className="px-2 text-xs font-semibold">Save</button>
          </form>
          <button
            aria-label={`Delete ${project.name}`}
            onClick={async () => {
              if (!confirm(`Delete ${project.name}?`)) return;
              try {
                await onDelete(project.id);
                setActionError(null);
              } catch (cause) {
                setActionError(cause instanceof Error ? cause.message : String(cause));
              }
            }}
            className="border border-grid px-2 hover:bg-muted"
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      </div>

      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!taskTitle.trim() || isSubmittingTask) return;
          setIsSubmittingTask(true);
          try {
            await onAddTask(taskTitle.trim(), project.id);
            setTaskTitle("");
            setActionError(null);
          } catch (cause) {
            setActionError(cause instanceof Error ? cause.message : String(cause));
          } finally {
            setIsSubmittingTask(false);
          }
        }}
        className="mt-4 flex border-2 border-grid"
      >
        <input
          aria-label="Task title"
          value={taskTitle}
          onChange={(event) => setTaskTitle(event.target.value)}
          placeholder={`Add task to ${project.name}`}
          className="flex-1 bg-transparent px-3 py-2 text-sm outline-none"
        />
        <button
          disabled={isSubmittingTask}
          className="border-l-2 border-grid px-3 text-xs font-black uppercase hover:bg-muted disabled:opacity-50"
        >
          Add task
        </button>
      </form>

      <h3 className="mt-4 text-xs font-black uppercase tracking-wider">Project Tasks</h3>
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
      <ul className="mt-3 divide-y divide-grid/20">
        {projectTasks.map((task) => (
          <li
            key={task.id}
            onClick={() => void onToggleTask(task.id).catch(() => {})}
            className="flex cursor-pointer items-center gap-2 py-2"
          >
            <button
              aria-label={`${task.done ? "Reopen" : "Complete"} ${task.title}`}
              onClick={(event) => {
                event.stopPropagation();
                void onToggleTask(task.id).catch(() => {});
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
                className={`ml-2 px-1 py-0.5 text-[8px] font-mono uppercase tracking-wider ${
                  task.syncStatus === "conflict" || task.syncStatus === "failed"
                    ? "bg-block-red text-white"
                    : task.syncStatus === "syncing"
                      ? "bg-block-blue text-white"
                      : "bg-block-yellow text-black"
                }`}
              >
                {task.syncStatus === "pending" ? "Queued" : task.syncStatus}
              </span>
            )}
          </li>
        ))}
      </ul>
      {!projectTasks.length && (
        <p className="py-4 text-sm text-muted-foreground">
          No tasks are associated with this project.
        </p>
      )}

      {/* Development & GitHub Repository Context */}
      <ProjectDevelopmentSection project={project} />
    </section>
  );
}

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
  const [newCategory, setNewCategory] = useState<ProjectCategory>("college");
  const [selectedCollegeId, setSelectedCollegeId] = useState<string>("");
  const [selectedPersonalId, setSelectedPersonalId] = useState<string>("");
  const [actionError, setActionError] = useState<string | null>(null);

  const collegeProjects = useMemo(
    () => projects.filter((p) => getProjectCategory(p) === "college"),
    [projects],
  );
  const personalProjects = useMemo(
    () => projects.filter((p) => getProjectCategory(p) === "personal"),
    [projects],
  );

  const activeCollege = useMemo(() => {
    return (
      collegeProjects.find((p) => p.id === selectedCollegeId) ??
      collegeProjects.find((p) => p.id === selectedProjectId) ??
      collegeProjects[0]
    );
  }, [collegeProjects, selectedCollegeId, selectedProjectId]);

  const activePersonal = useMemo(() => {
    return (
      personalProjects.find((p) => p.id === selectedPersonalId) ??
      personalProjects.find((p) => p.id === selectedProjectId) ??
      personalProjects[0]
    );
  }, [personalProjects, selectedPersonalId, selectedProjectId]);

  const submitProject = async (event: FormEvent) => {
    event.preventDefault();
    if (!projectName.trim()) return;
    setActionError(null);
    try {
      const categoryTag = newCategory === "college" ? "[College]" : "[Personal]";
      const project = await createProject({
        name: projectName.trim(),
        description: categoryTag,
      });
      if (newCategory === "college") {
        setSelectedCollegeId(project.id);
      } else {
        setSelectedPersonalId(project.id);
      }
      setSelectedProjectId(project.id);
      setProjectName("");
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const handleRename = async (projectId: string, newName: string, revision: number) => {
    await updateProject(projectId, { name: newName }, revision);
  };

  const handleDelete = async (projectId: string) => {
    await deleteProject(projectId);
    if (selectedCollegeId === projectId) setSelectedCollegeId("");
    if (selectedPersonalId === projectId) setSelectedPersonalId("");
  };

  const handleSwitchCategory = async (project: BackendProject) => {
    const currentCategory = getProjectCategory(project);
    const nextCategory: ProjectCategory = currentCategory === "college" ? "personal" : "college";
    const tag = nextCategory === "college" ? "[College]" : "[Personal]";
    const raw = project.description || "";
    const stripped = raw.replace(/\[(college|personal)\]/gi, "").trim();
    const nextDescription = stripped ? `${tag} ${stripped}` : tag;
    await updateProject(project.id, { description: nextDescription }, project.revision);
    if (nextCategory === "college") {
      setSelectedCollegeId(project.id);
    } else {
      setSelectedPersonalId(project.id);
    }
    setSelectedProjectId(project.id);
  };

  const handleAddTask = async (title: string, projectId: string) => {
    await addTask(title, projectId);
  };

  const handleToggleTask = async (taskId: string) => {
    await toggleTask(taskId);
  };

  return (
    <main className="flex flex-col h-full min-h-0 min-w-0 overflow-y-auto bg-grid p-1 border-l-[5px] border-grid">
      <section className="bg-canvas p-5 min-h-full shrink-0">
        <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
          <div>
            <span className="section-label text-muted-foreground">Workspace</span>
            <h1 className="mt-1 font-display text-4xl uppercase">Projects</h1>
          </div>
          <form onSubmit={submitProject} className="flex border-2 border-grid">
            <select
              aria-label="Project category"
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value as ProjectCategory)}
              className="border-r-2 border-grid bg-canvas px-2 text-xs font-bold uppercase tracking-wider outline-none cursor-pointer"
            >
              <option value="college">College</option>
              <option value="personal">Personal</option>
            </select>
            <input
              aria-label="Project name"
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="New project name"
              className="bg-transparent px-3 py-2 text-sm outline-none"
            />
            <button className="border-l-2 border-grid px-3 text-xs font-black uppercase hover:bg-muted">
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
          <div className="grid gap-5 lg:grid-cols-[340px_minmax(0,1fr)]">
            <nav
              aria-label="Projects"
              className="border-2 border-grid divide-y-2 divide-grid bg-canvas self-start shadow-sm"
            >
              {/* College section */}
              <div>
                <div className="bg-muted/30 px-4 pt-4 pb-3">
                  <div className="flex items-center justify-between">
                    <span className="font-display text-sm font-black uppercase tracking-widest text-muted-foreground">
                      College
                    </span>
                    <span className="font-mono text-xs font-semibold text-muted-foreground">
                      {collegeProjects.length}
                    </span>
                  </div>
                  <div className="mt-2 border-b border-grid/30" />
                </div>
                <div className="divide-y divide-grid/20">
                  {collegeProjects.length === 0 ? (
                    <p className="px-4 py-3.5 text-xs italic text-muted-foreground">
                      No college projects
                    </p>
                  ) : (
                    collegeProjects.map((project) => {
                      const isSelected = project.id === activeCollege?.id;
                      return (
                        <button
                          key={project.id}
                          type="button"
                          onClick={() => {
                            setSelectedCollegeId(project.id);
                            setSelectedProjectId(project.id);
                          }}
                          className={`block w-full px-4 py-4 text-left transition-colors ${
                            isSelected ? "bg-muted font-bold" : "hover:bg-muted/40"
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-lg font-bold leading-snug">{project.name}</span>
                            {project.syncStatus && project.syncStatus !== "synced" && (
                              <span
                                className={`shrink-0 px-1.5 py-0.5 text-[8px] font-mono uppercase tracking-wider ${
                                  project.syncStatus === "conflict" ||
                                  project.syncStatus === "failed"
                                    ? "bg-block-red text-white"
                                    : project.syncStatus === "syncing"
                                      ? "bg-block-blue text-white"
                                      : "bg-block-yellow text-black"
                                }`}
                              >
                                {project.syncStatus === "pending" ? "Queued" : project.syncStatus}
                              </span>
                            )}
                          </div>
                          <span className="mt-1 block truncate font-mono text-[10px] text-muted-foreground">
                            {project.id}
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>

              {/* Personal section */}
              <div>
                <div className="bg-muted/30 px-4 pt-4 pb-3">
                  <div className="flex items-center justify-between">
                    <span className="font-display text-sm font-black uppercase tracking-widest text-muted-foreground">
                      Personal
                    </span>
                    <span className="font-mono text-xs font-semibold text-muted-foreground">
                      {personalProjects.length}
                    </span>
                  </div>
                  <div className="mt-2 border-b border-grid/30" />
                </div>
                <div className="divide-y divide-grid/20">
                  {personalProjects.length === 0 ? (
                    <p className="px-4 py-3.5 text-xs italic text-muted-foreground">
                      No personal projects
                    </p>
                  ) : (
                    personalProjects.map((project) => {
                      const isSelected = project.id === activePersonal?.id;
                      return (
                        <button
                          key={project.id}
                          type="button"
                          onClick={() => {
                            setSelectedPersonalId(project.id);
                            setSelectedProjectId(project.id);
                          }}
                          className={`block w-full px-4 py-4 text-left transition-colors ${
                            isSelected ? "bg-muted font-bold" : "hover:bg-muted/40"
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-lg font-bold leading-snug">{project.name}</span>
                            {project.syncStatus && project.syncStatus !== "synced" && (
                              <span
                                className={`shrink-0 px-1.5 py-0.5 text-[8px] font-mono uppercase tracking-wider ${
                                  project.syncStatus === "conflict" ||
                                  project.syncStatus === "failed"
                                    ? "bg-block-red text-white"
                                    : project.syncStatus === "syncing"
                                      ? "bg-block-blue text-white"
                                      : "bg-block-yellow text-black"
                                }`}
                              >
                                {project.syncStatus === "pending" ? "Queued" : project.syncStatus}
                              </span>
                            )}
                          </div>
                          <span className="mt-1 block truncate font-mono text-[10px] text-muted-foreground">
                            {project.id}
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            </nav>

            <div className="flex min-w-0 flex-col gap-4">
              <ProjectWindow
                category="college"
                categoryLabel="College Project"
                project={activeCollege}
                tasks={tasks}
                onAddTask={handleAddTask}
                onToggleTask={handleToggleTask}
                onRename={handleRename}
                onDelete={handleDelete}
                onSwitchCategory={handleSwitchCategory}
                conflictError={conflictError}
                clearConflict={clearConflict}
                error={error}
                clearError={clearError}
                refetchTasks={refetchTasks}
                enableDemoMode={enableDemoMode}
                setActionError={setActionError}
              />

              <ProjectWindow
                category="personal"
                categoryLabel="Personal Project"
                project={activePersonal}
                tasks={tasks}
                onAddTask={handleAddTask}
                onToggleTask={handleToggleTask}
                onRename={handleRename}
                onDelete={handleDelete}
                onSwitchCategory={handleSwitchCategory}
                conflictError={conflictError}
                clearConflict={clearConflict}
                error={error}
                clearError={clearError}
                refetchTasks={refetchTasks}
                enableDemoMode={enableDemoMode}
                setActionError={setActionError}
              />
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
