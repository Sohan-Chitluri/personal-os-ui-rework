import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { GridPanel, toneBg, toneText } from "@/components/blockday-ui";
import { advancements, projectById, projects, workspace } from "@/lib/blockday-data";

export const Route = createFileRoute("/projects")({
  head: () => ({
    meta: [
      { title: "Projects — Blockday" },
      { name: "description", content: "Nested projects, featured blocks, and a workspace of ideas, notes and drafts." },
      { property: "og:title", content: "Projects — Blockday" },
      { property: "og:description", content: "Nested projects, featured blocks, and a workspace of ideas, notes and drafts." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: ProjectsPage,
});

function ProjectsPage() {
  const [selectedId, setSelectedId] = useState("blockday");
  const featured = projects.filter((p) => p.active).slice(0, 3);
  const selected = projectById(selectedId) ?? projects[0]!;
  const ws = workspace[selected.id] ?? { ideas: [], notes: [], drafts: [] };
  const roots = projects.filter((p) => !p.parent);

  return (
    <main className="page-grid grid-cols-[220px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col gap-[5px]">
        <GridPanel label="Jump to">
          <ul className="space-y-0.5">
            {roots.map((root) => (
              <li key={root.id}>
                <button onClick={() => setSelectedId(root.id)} className={`tree-row tree-row-compact w-full text-left ${root.id === selected.id ? "bg-muted" : ""}`}>
                  <span className={`project-swatch ${toneBg[root.tone]}`} />
                  <span className="flex-1 truncate">{root.name}</span>
                  <span className="font-mono text-[9px] text-muted-foreground">{root.progress}%</span>
                </button>
                <ul>
                  {projects.filter((p) => p.parent === root.id).map((p) => (
                    <li key={p.id}>
                      <button onClick={() => setSelectedId(p.id)} className={`tree-row tree-row-compact w-full pl-6 text-left ${p.id === selected.id ? "bg-muted" : ""}`}>
                        <span className={`project-swatch ${toneBg[p.tone]}`} />
                        <span className="flex-1 truncate">{p.name}</span>
                        <span className="font-mono text-[9px] text-muted-foreground">{p.progress}%</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </GridPanel>
        <GridPanel label="Recent progress" className="flex-1">
          <ul className="space-y-3">
            {advancements.map((a) => {
              const p = projectById(a.projectId);
              return (
                <li key={a.text} className="flex items-start gap-2 text-[11px] font-bold leading-snug">
                  <span className={`mt-0.5 size-3 flex-shrink-0 border border-grid ${p ? toneBg[p.tone] : ""}`} />
                  <span className="min-w-0">
                    {a.text}
                    <span className="ml-1.5 text-[9px] font-black uppercase tracking-[0.12em] text-muted-foreground">{a.when}</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </GridPanel>
      </aside>

      <section className="grid min-h-0 grid-rows-[auto_auto_minmax(0,1fr)] gap-[5px]">
        <div className="grid grid-cols-3 gap-[5px]">
          {featured.map((p) => (
            <button
              key={p.id}
              onClick={() => setSelectedId(p.id)}
              className={`stat-block min-h-40 text-left transition-transform hover:-translate-y-1 ${toneBg[p.tone]} ${toneText[p.tone]} ${p.id === selected.id ? "outline outline-4 -outline-offset-4 outline-grid" : ""}`}
            >
              <p className="font-display text-2xl uppercase leading-none">{p.name}</p>
              <p className="mt-2 text-[9px] font-black uppercase tracking-[0.16em] opacity-80">{p.summary}</p>
              <p className="mt-auto pt-4 font-display text-5xl leading-none tabular-nums">{p.progress}%</p>
              <p className="mt-1 text-[9px] font-black uppercase tracking-[0.18em]">complete</p>
            </button>
          ))}
        </div>

        <GridPanel label={`Workspace · ${selected.name}`}>
          <div className="flex items-center gap-4">
            <p className="min-w-0 flex-1 text-xs font-bold leading-relaxed">{selected.summary}</p>
            <div className="h-3 w-48 border-2 border-grid">
              <div className={`h-full ${toneBg[selected.tone]}`} style={{ width: `${selected.progress}%` }} />
            </div>
          </div>
        </GridPanel>

        <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.9fr)] gap-[5px]">
          <GridPanel label="Ideas">
            <ul className="space-y-3">
              {ws.ideas.map((idea, i) => (
                <li key={idea} className="flex items-baseline gap-3 text-sm font-bold leading-snug">
                  <span className="font-display text-lg text-muted-foreground">{String(i + 1).padStart(2, "0")}</span>
                  {idea}
                </li>
              ))}
              {!ws.ideas.length && <li className="text-xs text-muted-foreground">Nothing here yet.</li>}
            </ul>
          </GridPanel>
          <GridPanel label="Notes">
            <ul className="space-y-3">
              {ws.notes.map((note) => (
                <li key={note} className="border-l-[5px] border-grid pl-3 text-xs font-bold leading-relaxed">{note}</li>
              ))}
              {!ws.notes.length && <li className="text-xs text-muted-foreground">Nothing here yet.</li>}
            </ul>
          </GridPanel>
          <GridPanel label="Drafts">
            <div className="grid grid-cols-2 gap-2">
              {ws.drafts.map((tone, i) => (
                <div key={i} className={`flex aspect-square items-end border-[3px] border-grid p-1.5 text-[8px] font-black uppercase tracking-[0.12em] ${toneBg[tone]} ${toneText[tone]}`}>
                  Draft {String(i + 1).padStart(2, "0")}
                </div>
              ))}
              {!ws.drafts.length && <p className="col-span-2 text-xs text-muted-foreground">No drafts yet.</p>}
            </div>
          </GridPanel>
        </div>
      </section>
    </main>
  );
}
