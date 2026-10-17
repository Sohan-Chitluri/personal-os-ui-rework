import { createFileRoute } from "@tanstack/react-router";
import { Check, ChevronDown } from "lucide-react";
import { useState } from "react";
import { GridPanel, StatBlock } from "@/components/blockday-ui";
import { diary } from "@/lib/blockday-data";

export const Route = createFileRoute("/diary")({
  head: () => ({
    meta: [
      { title: "Diary — Blockday" },
      { name: "description", content: "Voice notes and conversations, distilled into updates, decisions and tasks." },
      { property: "og:title", content: "Diary — Blockday" },
      { property: "og:description", content: "Voice notes and conversations, distilled into updates, decisions and tasks." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: DiaryPage,
});

const kindStyle = {
  "Voice capture": "bg-block-red text-on-primary",
  Conversation: "bg-block-blue text-on-primary",
} as const;

function DiaryPage() {
  const [openId, setOpenId] = useState<string | null>(diary[0]?.id ?? null);

  const groups: { day: string; entries: typeof diary }[] = [];
  for (const entry of diary) {
    const last = groups.at(-1);
    if (last && last.day === entry.day) last.entries.push(entry);
    else groups.push({ day: entry.day, entries: [entry] });
  }

  return (
    <main className="page-grid grid-cols-[minmax(0,1fr)_320px]">
      <section className="flex min-h-0 flex-col gap-[5px]">
        {groups.map((group) => (
          <div key={group.day} className={`grid grid-cols-[130px_minmax(0,1fr)] gap-[5px] ${group === groups.at(-1) ? "min-h-0 flex-1" : ""}`}>
            <GridPanel className="flex items-start">
              <h2 className="font-display text-lg uppercase leading-tight">{group.day}</h2>
            </GridPanel>
            <div className="flex min-h-0 flex-col gap-[5px]">
              {group.entries.map((entry) => {
                const open = openId === entry.id;
                return (
                  <article
                    key={entry.id}
                    className={`grid-panel animate-slide-panel cursor-pointer ${group === groups.at(-1) && entry === group.entries.at(-1) ? "min-h-0 flex-1" : ""}`}
                    onClick={() => setOpenId(open ? null : entry.id)}
                  >
                    <div className="flex items-center gap-3">
                      <span className="font-display text-2xl tabular-nums leading-none">{entry.time}</span>
                      <span className={`diary-kind ${kindStyle[entry.kind]}`}>{entry.kind}</span>
                      <h3 className="min-w-0 flex-1 truncate text-[15px] font-bold">{entry.title}</h3>
                      <span className="text-[9px] font-black uppercase tracking-[0.12em] text-muted-foreground">{entry.duration}</span>
                      <ChevronDown className={`size-4 transition-transform duration-300 ${open ? "rotate-180" : ""}`} />
                    </div>
                    <p className={`mt-2 text-xs leading-relaxed text-muted-foreground ${open ? "" : "line-clamp-2"}`}>{entry.transcript}</p>
                    {open && (
                      <div className="mt-4 grid grid-cols-3 gap-4 border-t-[3px] border-grid pt-4">
                        <div>
                          <p className="section-label mb-2 text-block-blue">Project updates</p>
                          <ul className="space-y-2">
                            {entry.updates.map((u) => (
                              <li key={u} className="flex items-start gap-2 text-[11px] font-bold leading-snug">
                                <span className="mt-0.5 size-2.5 flex-shrink-0 bg-block-blue" />{u}
                              </li>
                            ))}
                          </ul>
                        </div>
                        <div>
                          <p className="section-label mb-2">Decisions</p>
                          <ul className="space-y-2">
                            {entry.decisions.map((d) => (
                              <li key={d} className="flex items-start gap-2 text-[11px] font-bold leading-snug">
                                <span className="mt-0.5 size-2.5 flex-shrink-0 bg-block-yellow" />{d}
                              </li>
                            ))}
                          </ul>
                        </div>
                        <div>
                          <p className="section-label mb-2 text-block-red">Extracted tasks</p>
                          <ul className="space-y-2">
                            {entry.tasks.map((t) => (
                              <li key={t} className="flex items-start gap-2 text-[11px] font-bold leading-snug">
                                <span className="mt-0.5 flex size-3.5 flex-shrink-0 items-center justify-center border-2 border-grid"><Check className="size-2.5" strokeWidth={4} /></span>{t}
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </div>
        ))}
      </section>

      <aside className="flex min-h-0 flex-col gap-[5px]">
        <div className="grid grid-cols-2 gap-[5px]">
          <StatBlock value={diary.length} label="Captures" tone="blue" />
          <StatBlock value={diary.reduce((sum, e) => sum + e.tasks.length, 0)} label="Tasks found" tone="yellow" />
        </div>
        <GridPanel label="How to read">
          <ul className="space-y-2.5 text-[10px] font-bold uppercase tracking-[0.12em]">
            <li className="flex items-center gap-2"><span className="size-3 bg-block-red" /> Voice capture</li>
            <li className="flex items-center gap-2"><span className="size-3 bg-block-blue" /> Conversation</li>
            <li className="flex items-center gap-2"><span className="size-3 bg-block-yellow" /> Decision</li>
          </ul>
        </GridPanel>
        <GridPanel label="Notes" className="flex-1">
          <p className="text-xs font-bold leading-relaxed">Click an entry to unfold its transcript, project updates, decisions and the tasks pulled out of it.</p>
        </GridPanel>
      </aside>
    </main>
  );
}
