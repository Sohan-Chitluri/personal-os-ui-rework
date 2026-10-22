import { Link, useRouterState } from "@tanstack/react-router";
import { useBlockday } from "@/lib/blockday-store";
import { toneBg } from "@/components/blockday-ui";

// Per-page slab arrangement: [yellow grow, blue grow, red grow, tree grow]. The
// sidebar blocks animate between these so each page "recomposes" the wall.
const arrangements: Record<string, [number, number, number, number]> = {
  "/": [1, 1, 0, 6],
  "/flow": [2, 1, 0, 6],
  "/projects": [1, 0, 1, 7],
  "/focus": [2, 1, 1, 6],
  "/stats": [1, 2, 0, 6],
  "/diary": [0, 1, 2, 6],
  "/devices": [1, 1, 1, 6],
  "/raphael": [1, 1, 1, 6],
};

export function AppSidebar() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const { tasks, projects, projectsLoading, selectedProjectId, setSelectedProjectId } =
    useBlockday();
  const [y, b, r, tree] = arrangements[path] ?? arrangements["/"]!;
  const count = (id: string) => tasks.filter((t) => !t.done && t.projectId === id).length;

  return (
    <aside className="sidebar-wall">
      <div
        className="sidebar-cell bg-canvas p-4 flex flex-col min-h-[180px]"
        style={{ flexGrow: tree }}
      >
        <div className="mb-3 flex items-center justify-between shrink-0">
          <span className="section-label">Projects</span>
          <Link to="/projects" className="ghost-chip">
            + New
          </Link>
        </div>
        {projectsLoading && <p className="text-xs text-muted-foreground">Loading…</p>}
        {!projectsLoading && !projects.length && (
          <p className="text-xs text-muted-foreground">No projects</p>
        )}
        <ul className="space-y-0.5 flex-1 min-h-0 overflow-y-auto">
          {projects.map((project) => (
            <li key={project.id}>
              <Link
                to="/projects"
                onClick={() => setSelectedProjectId(project.id)}
                className={`tree-row ${selectedProjectId === project.id && path === "/projects" ? "bg-muted font-bold" : ""}`}
              >
                <span className={`project-swatch ${toneBg.neutral}`} />
                <span className="flex-1 truncate">{project.name}</span>
                <span className="tree-count">{count(project.id) || ""}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
      <div className="sidebar-cell bg-block-yellow" style={{ flexGrow: y }} />
      <div className="sidebar-cell bg-block-red" style={{ flexGrow: r }} />
      <div className="sidebar-cell bg-block-blue" style={{ flexGrow: b }} />
    </aside>
  );
}
