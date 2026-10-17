import { Link, useRouterState } from "@tanstack/react-router";
import { projects } from "@/lib/blockday-data";
import { useBlockday } from "@/lib/blockday-store";
import { toneBg } from "@/components/blockday-ui";

// Per-page slab arrangement: [yellow grow, blue grow, red grow, tree grow]. The
// sidebar blocks animate between these so each page "recomposes" the wall.
const arrangements: Record<string, [number, number, number, number]> = {
  "/": [1, 1, 0, 7],
  "/flow": [2, 3, 1, 4],
  "/projects": [1, 0, 2, 6],
  "/focus": [4, 1, 3, 2],
  "/stats": [1, 4, 0, 3],
  "/diary": [0, 2, 3, 4],
};

export function AppSidebar() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const { tasks } = useBlockday();
  const [y, b, r, tree] = arrangements[path] ?? arrangements["/"]!;
  const roots = projects.filter((p) => !p.parent);
  const count = (id: string) => tasks.filter((t) => !t.done && t.projectId === id).length;

  return (
    <aside className="sidebar-wall">
      <div className="sidebar-cell bg-canvas p-4" style={{ flexGrow: tree }}>
        <div className="mb-3 flex items-center justify-between">
          <span className="section-label">Projects</span>
          <Link to="/projects" className="ghost-chip">+ New</Link>
        </div>
        <ul className="space-y-0.5">
          {roots.map((root) => (
            <li key={root.id}>
              <Link to="/projects" className="tree-row">
                <span className={`project-swatch ${toneBg[root.tone]}`} />
                <span className="flex-1">{root.name}</span>
                <span className="tree-count">{count(root.id) || ""}</span>
              </Link>
              <ul>
                {projects.filter((p) => p.parent === root.id).map((p) => (
                  <li key={p.id}>
                    <Link to="/projects" className="tree-row pl-7">
                      <span className={`project-swatch ${toneBg[p.tone]}`} />
                      <span className="flex-1 truncate">{p.name}</span>
                      <span className="tree-count">{count(p.id) || ""}</span>
                    </Link>
                  </li>
                ))}
              </ul>
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
