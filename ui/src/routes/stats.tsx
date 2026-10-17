import { createFileRoute } from "@tanstack/react-router";
import { GridPanel, HeatMap, StatBlock, toneBg, toneText } from "@/components/blockday-ui";
import { dailyCompletions, focusMinutesByDay, projectById, projectShare, streaks } from "@/lib/blockday-data";

export const Route = createFileRoute("/stats")({
  head: () => ({
    meta: [
      { title: "Stats — Blockday" },
      { name: "description", content: "Completions, streaks, focus hours, and where the work went — as one composition." },
      { property: "og:title", content: "Stats — Blockday" },
      { property: "og:description", content: "Completions, streaks, focus hours, and where the work went — as one composition." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: StatsPage,
});

function StatsPage() {
  const total = dailyCompletions.reduce((a, b) => a + b, 0);
  const last7 = dailyCompletions.slice(-7).reduce((a, b) => a + b, 0);
  const { current, best } = streaks(dailyCompletions);
  const focusHours = Math.round(focusMinutesByDay.reduce((a, b) => a + b, 0) / 60);
  const bestDay = Math.max(...dailyCompletions);
  const shareTotal = projectShare.reduce((sum, p) => sum + p.done, 0);

  return (
    <main className="page-grid grid-cols-[minmax(0,1fr)_340px]">
      <section className="flex min-h-0 flex-col gap-[5px]">
        <div className="grid grid-cols-4 gap-[5px]">
          <StatBlock value={total} label="Done · 35 days" tone="red" />
          <StatBlock value={last7} label="Done this week" tone="blue" />
          <StatBlock value={current} label="Current streak" tone="yellow" />
          <StatBlock value={`${focusHours}h`} label="Focus hours" />
        </div>

        <GridPanel label="Completion heat map · 5 weeks">
          <HeatMap size="lg" />
          <div className="mt-3 flex justify-between text-[8px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
            <span>5 weeks ago</span><span>This week</span>
          </div>
        </GridPanel>

        <GridPanel label="Where the work went" className="flex-1">
          <div className="flex h-28 w-full overflow-hidden border-[3px] border-grid">
            {projectShare.map((p) => {
              const project = projectById(p.projectId);
              return (
                <div
                  key={p.projectId}
                  style={{ width: `${(p.done / shareTotal) * 100}%` }}
                  className={`flex min-w-0 flex-col justify-end p-2 transition-[width] duration-700 ${project ? toneBg[project.tone] : "bg-canvas"} ${project ? toneText[project.tone] : "text-foreground"}`}
                >
                  <p className="truncate text-[9px] font-black uppercase tracking-[0.1em]">{project?.name}</p>
                  <p className="font-display text-xl leading-none tabular-nums">{p.done}</p>
                </div>
              );
            })}
          </div>
          <ul className="mt-4 grid grid-cols-3 gap-2">
            {projectShare.map((p) => {
              const project = projectById(p.projectId);
              return (
                <li key={p.projectId} className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.1em]">
                  <span className={`size-3 flex-shrink-0 border border-grid ${project ? toneBg[project.tone] : ""}`} />
                  <span className="truncate">{project?.name}</span>
                  <span className="ml-auto font-mono">{Math.round((p.done / shareTotal) * 100)}%</span>
                </li>
              );
            })}
          </ul>
        </GridPanel>
      </section>

      <aside className="flex min-h-0 flex-col gap-[5px]">
        <div className="grid grid-cols-2 gap-[5px]">
          <StatBlock value={best} label="Best streak" tone="red" />
          <StatBlock value={bestDay} label="Best day" tone="yellow" />
        </div>
        <GridPanel label="Streak watch">
          <p className="font-display text-6xl leading-none tabular-nums">{current}<span className="text-2xl"> days</span></p>
          <p className="mt-3 text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
            {current >= 3 ? "Keep the wall growing." : "Finish one block today to extend it."}
          </p>
        </GridPanel>
        <GridPanel label="Reading the map" className="flex-1">
          <ul className="space-y-2.5 text-[10px] font-bold uppercase tracking-[0.12em]">
            <li><span className="mr-2 inline-block size-3 bg-block-red align-middle" /> Heavy day</li>
            <li><span className="mr-2 inline-block size-3 bg-block-blue align-middle" /> Solid day</li>
            <li><span className="mr-2 inline-block size-3 bg-block-yellow align-middle" /> Light day</li>
            <li><span className="mr-2 inline-block size-3 bg-muted align-middle" /> Nothing done</li>
          </ul>
        </GridPanel>
      </aside>
    </main>
  );
}
