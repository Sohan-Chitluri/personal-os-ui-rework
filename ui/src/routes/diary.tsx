import { createFileRoute } from "@tanstack/react-router";
import { Check, ChevronDown, Plus, RefreshCw, Trash2, AlertTriangle, Loader2 } from "lucide-react";
import { useEffect, useState, useMemo } from "react";
import { GridPanel, StatBlock } from "@/components/blockday-ui";
import { MarkdownContent } from "@/components/markdown-content";
import { useBlockday } from "@/lib/blockday-store";
import { captureApi, CaptureApiError } from "@/lib/capture-api";
import type { BackendCapture } from "@/lib/capture-types";
import { diary as mockDiary } from "@/lib/blockday-data";

export const Route = createFileRoute("/diary")({
  head: () => ({
    meta: [
      { title: "Diary & Captures — Personal OS" },
      {
        name: "description",
        content: "Durable capture inbox, transcripts, LLM task extraction, and processing status.",
      },
      { property: "og:title", content: "Diary & Captures — Personal OS" },
      {
        property: "og:description",
        content: "Durable capture inbox, transcripts, LLM task extraction, and processing status.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: DiaryPage,
});

const statusStyles: Record<string, string> = {
  pending: "bg-block-yellow text-block-ink",
  processing: "bg-block-blue text-on-primary animate-pulse",
  processed: "bg-foreground text-background",
  failed: "bg-block-red text-on-primary",
  dead: "bg-block-red text-on-primary font-black",
};

export function DiaryPage() {
  const { projects, tasks } = useBlockday();
  const [captures, setCaptures] = useState<BackendCapture[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  // New capture submission state
  const [newContent, setNewContent] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [submitFeedback, setSubmitFeedback] = useState<{
    type: "success" | "warning" | "error";
    message: string;
  } | null>(null);

  const [openId, setOpenId] = useState<string | null>(null);

  const loadCaptures = async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    setApiError(null);

    try {
      const res = await captureApi.listCaptures({ limit: 100 });
      setCaptures(res.items);
      if (res.items.length > 0 && !openId) {
        setOpenId(res.items[0]?.id ?? null);
      }
    } catch (err: unknown) {
      const msg = err instanceof CaptureApiError ? err.message : String(err);
      setApiError(msg);
      // If backend is unreachable or not yet populated, synthesize fallback demo items
      if (captures.length === 0) {
        const fallbackCaptures: BackendCapture[] = mockDiary.map((item, idx) => ({
          id: item.id,
          owner_id: "demo-user",
          project_id: null,
          content: `${item.title}\n\n${item.transcript}`,
          source: item.kind === "Voice capture" ? "voice" : "web",
          content_type: "text",
          status: "processed",
          revision: 1,
          created_at: new Date(Date.now() - idx * 86400000).toISOString(),
          updated_at: new Date(Date.now() - idx * 86400000).toISOString(),
          task_ids: item.tasks.map((_, tIdx) => `demo-task-${idx}-${tIdx}`),
          extraction_error: null,
        }));
        setCaptures(fallbackCaptures);
        setOpenId(fallbackCaptures[0]?.id ?? null);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadCaptures();
  }, []);

  const handleCreateCapture = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newContent.trim() || submitting) return;

    setSubmitting(true);
    setSubmitFeedback(null);

    try {
      const idempotencyKey = crypto.randomUUID();
      const receipt = await captureApi.submitCapture({
        content: newContent.trim(),
        project_id: selectedProjectId || null,
        source: "web",
        idempotency_key: idempotencyKey,
      });

      if (receipt.error) {
        setSubmitFeedback({
          type: "warning",
          message: `Capture persisted (ID: ${receipt.capture_id}). Queue notice: ${receipt.error}`,
        });
      } else {
        setSubmitFeedback({
          type: "success",
          message: `Capture persisted with status: ${receipt.status} (ID: ${receipt.capture_id.slice(0, 12)}...)`,
        });
      }

      setNewContent("");
      await loadCaptures(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setSubmitFeedback({
        type: "error",
        message: `Failed to submit capture: ${msg}`,
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteCapture = async (captureId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm("Delete this capture entry?")) return;

    try {
      await captureApi.deleteCapture(captureId);
      setCaptures((prev) => prev.filter((c) => c.id !== captureId));
      if (openId === captureId) setOpenId(null);
    } catch (err: unknown) {
      alert(`Failed to delete capture: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // Group captures by calendar date
  const groupedCaptures = useMemo(() => {
    const groups: { day: string; entries: BackendCapture[] }[] = [];
    for (const entry of captures) {
      const day = entry.created_at
        ? new Date(entry.created_at).toLocaleDateString(undefined, {
            weekday: "short",
            month: "short",
            day: "numeric",
          })
        : "Recent";
      const last = groups.at(-1);
      if (last && last.day === day) {
        last.entries.push(entry);
      } else {
        groups.push({ day, entries: [entry] });
      }
    }
    return groups;
  }, [captures]);

  const totalExtractedTasks = useMemo(() => {
    return captures.reduce((sum, c) => sum + (c.task_ids?.length || 0), 0);
  }, [captures]);

  const failedCount = useMemo(() => {
    return captures.filter((c) => c.status === "failed" || c.status === "dead").length;
  }, [captures]);

  const projectMap = useMemo(() => {
    return new Map(projects.map((p) => [p.id, p]));
  }, [projects]);

  const taskTitleMap = useMemo(() => {
    return new Map(tasks.map((t) => [t.id, t.title]));
  }, [tasks]);

  return (
    <main className="page-grid grid-cols-[minmax(0,1fr)_320px]">
      <section className="flex min-h-0 flex-1 flex-col gap-[5px] overflow-hidden">
        {/* Quick Capture Panel */}
        <GridPanel label="Durable Capture Inbox" className="shrink-0">
          <form onSubmit={handleCreateCapture} className="flex flex-col gap-3">
            <textarea
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              placeholder="Record a thought, voice transcription, meeting notes, or decision for LLM task extraction..."
              rows={3}
              className="w-full resize-none border-2 border-grid bg-canvas p-2.5 text-xs font-mono leading-relaxed placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground"
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-black uppercase tracking-[0.14em] text-muted-foreground">
                  Project:
                </span>
                <select
                  value={selectedProjectId}
                  onChange={(e) => setSelectedProjectId(e.target.value)}
                  className="border-2 border-grid bg-canvas px-2 py-1 text-xs font-bold focus:outline-none"
                >
                  <option value="">(None / General Inbox)</option>
                  {projects.map((proj) => (
                    <option key={proj.id} value={proj.id}>
                      {proj.name}
                    </option>
                  ))}
                </select>
              </div>

              <button
                type="submit"
                disabled={!newContent.trim() || submitting}
                className="flex items-center gap-1.5 border-2 border-grid bg-block-ink px-4 py-1.5 text-xs font-black uppercase tracking-wider text-canvas hover:bg-block-blue disabled:opacity-40 transition-colors"
              >
                {submitting ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin" />
                    Persisting...
                  </>
                ) : (
                  <>
                    <Plus className="size-3.5" />
                    Submit Capture
                  </>
                )}
              </button>
            </div>

            {submitFeedback && (
              <div
                className={`p-2 border-2 border-grid text-xs font-bold ${
                  submitFeedback.type === "success"
                    ? "bg-block-yellow/20 text-foreground"
                    : submitFeedback.type === "warning"
                      ? "bg-block-yellow/40 text-foreground"
                      : "bg-block-red text-on-primary"
                }`}
              >
                {submitFeedback.message}
              </div>
            )}
          </form>
        </GridPanel>

        {/* Backend API Notice if offline */}
        {apiError && (
          <div className="shrink-0 flex items-center justify-between border-2 border-grid bg-panel p-2.5 text-xs font-bold">
            <span className="flex items-center gap-2 text-muted-foreground">
              <AlertTriangle className="size-4 text-block-yellow" />
              API Connection note: {apiError}. Showing local demo records.
            </span>
            <button
              onClick={() => loadCaptures()}
              className="ghost-chip text-[9px] uppercase hover:bg-block-ink hover:text-canvas"
            >
              Retry
            </button>
          </div>
        )}

        {/* Captures Timeline Panel */}
        <GridPanel
          label={`Capture History & Extraction · ${captures.length}`}
          className="flex-1 min-h-0 flex flex-col overflow-hidden"
        >
          <div className="flex-1 min-h-0 overflow-y-auto space-y-4 pr-1">
            {loading && captures.length === 0 ? (
              <div className="flex h-full min-h-[180px] flex-col items-center justify-center text-muted-foreground">
                <Loader2 className="size-5 animate-spin" />
                <span className="mt-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                  Loading captures...
                </span>
              </div>
            ) : groupedCaptures.length === 0 ? (
              <div className="flex h-full min-h-[180px] flex-col items-center justify-center text-center p-8">
                <p className="font-display text-lg uppercase tracking-tight text-foreground">
                  No captures yet
                </p>
                <p className="mt-1 text-xs font-bold text-muted-foreground max-w-md">
                  Submit your first note above to trigger durable persistence and LLM task
                  extraction.
                </p>
              </div>
            ) : (
              groupedCaptures.map((group) => (
                <div key={group.day} className="grid grid-cols-[130px_minmax(0,1fr)] gap-[5px]">
                  <div className="flex items-start border-2 border-grid bg-canvas p-3">
                    <h2 className="font-display text-lg uppercase leading-tight">{group.day}</h2>
                  </div>

                  <div className="flex min-h-0 flex-col gap-[5px]">
                    {group.entries.map((entry) => {
                      const open = openId === entry.id;
                      const timeFormatted = entry.created_at
                        ? new Date(entry.created_at).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })
                        : "—";

                      const firstLine = entry.content.split("\n")[0] || "Untitled Capture";
                      const restContent = entry.content;
                      const project = entry.project_id ? projectMap.get(entry.project_id) : null;

                      return (
                        <article
                          key={entry.id}
                          className="border-2 border-grid bg-canvas p-3.5 transition-colors cursor-pointer hover:bg-panel/50"
                          onClick={() => setOpenId(open ? null : entry.id)}
                        >
                          <div className="flex items-center gap-3">
                            <span className="font-display text-2xl tabular-nums leading-none">
                              {timeFormatted}
                            </span>

                            <span
                              className={`diary-kind px-2 py-0.5 text-[9px] font-black uppercase tracking-wider ${
                                statusStyles[entry.status] || "bg-panel text-foreground"
                              }`}
                            >
                              {entry.status}
                            </span>

                            <span className="text-[9px] font-black uppercase tracking-wider text-muted-foreground border border-grid/40 px-1.5 py-0.5">
                              {entry.source}
                            </span>

                            <h3 className="min-w-0 flex-1 truncate text-[14px] font-bold">
                              {firstLine}
                            </h3>

                            {project && (
                              <span className="text-[9px] font-black uppercase text-block-blue border border-block-blue px-1.5 py-0.5">
                                {project.name}
                              </span>
                            )}

                            <button
                              type="button"
                              onClick={(e) => handleDeleteCapture(entry.id, e)}
                              title="Delete capture"
                              className="p-1 text-muted-foreground hover:text-block-red transition-colors"
                            >
                              <Trash2 className="size-3.5" />
                            </button>

                            <ChevronDown
                              className={`size-4 transition-transform duration-300 ${
                                open ? "rotate-180" : ""
                              }`}
                            />
                          </div>

                          {open ? (
                            <MarkdownContent
                              content={restContent}
                              className="mt-2 text-xs text-muted-foreground"
                            />
                          ) : (
                            <p className="mt-2 text-xs leading-relaxed text-muted-foreground line-clamp-2">
                              {restContent}
                            </p>
                          )}

                          {open && (
                            <div className="mt-4 flex flex-col gap-3 border-t-[3px] border-grid pt-4">
                              {/* Failure / Extraction Alert */}
                              {(entry.status === "failed" ||
                                entry.status === "dead" ||
                                entry.extraction_error) && (
                                <div className="border-2 border-block-red bg-block-red/10 p-3 text-xs">
                                  <p className="font-black uppercase tracking-wider text-block-red flex items-center gap-1.5">
                                    <AlertTriangle className="size-4" />
                                    Extraction Notice (Status: {entry.status})
                                  </p>
                                  <p className="mt-1 font-mono text-[11px] text-foreground">
                                    {entry.extraction_error ||
                                      "Semantic extraction could not extract structured tasks or exceeded retry limits. The capture remains durably saved in PostgreSQL."}
                                  </p>
                                </div>
                              )}

                              {/* Extracted Tasks Section */}
                              <div>
                                <p className="section-label mb-2 text-block-red">
                                  Extracted tasks ({entry.task_ids?.length || 0})
                                </p>
                                {entry.task_ids && entry.task_ids.length > 0 ? (
                                  <ul className="space-y-2">
                                    {entry.task_ids.map((tid) => {
                                      const title = taskTitleMap.get(tid) || tid;
                                      return (
                                        <li
                                          key={tid}
                                          className="flex items-start gap-2 text-[11px] font-bold leading-snug"
                                        >
                                          <span className="mt-0.5 flex size-3.5 flex-shrink-0 items-center justify-center border-2 border-grid bg-block-yellow/20">
                                            <Check className="size-2.5" strokeWidth={4} />
                                          </span>
                                          <span>{title}</span>
                                        </li>
                                      );
                                    })}
                                  </ul>
                                ) : (
                                  <p className="text-[11px] font-bold text-muted-foreground italic">
                                    {entry.status === "pending"
                                      ? "Queued for worker extraction..."
                                      : entry.status === "processing"
                                        ? "Worker currently processing..."
                                        : "No tasks extracted from this note."}
                                  </p>
                                )}
                              </div>

                              {/* Technical Metadata */}
                              <div className="flex flex-wrap items-center gap-4 text-[9px] font-mono text-muted-foreground border-t border-grid/20 pt-2">
                                <span>ID: {entry.id}</span>
                                <span>Revision: {entry.revision}</span>
                                {entry.idempotency_key && (
                                  <span>Idem: {entry.idempotency_key.slice(0, 8)}...</span>
                                )}
                              </div>
                            </div>
                          )}
                        </article>
                      );
                    })}
                  </div>
                </div>
              ))
            )}
          </div>
        </GridPanel>
      </section>

      {/* Right Sidebar Stats & Legend */}
      <aside className="flex min-h-0 flex-col gap-[5px]">
        <div className="grid grid-cols-2 gap-[5px]">
          <StatBlock value={captures.length} label="Captures" tone="blue" />
          <StatBlock value={totalExtractedTasks} label="Tasks found" tone="yellow" />
        </div>

        {failedCount > 0 && (
          <div className="border-2 border-block-red bg-block-red p-3 text-on-primary">
            <span className="font-display text-2xl font-black">{failedCount}</span>
            <span className="block text-[9px] font-black uppercase tracking-wider">
              Failed / Retrying
            </span>
          </div>
        )}

        <GridPanel label="Pipeline Status">
          <ul className="space-y-2.5 text-[10px] font-bold uppercase tracking-[0.12em]">
            <li className="flex items-center gap-2">
              <span className="size-3 bg-block-yellow" /> Pending dispatch
            </li>
            <li className="flex items-center gap-2">
              <span className="size-3 bg-block-blue" /> Extraction in flight
            </li>
            <li className="flex items-center gap-2">
              <span className="size-3 bg-foreground" /> Processed & stored
            </li>
            <li className="flex items-center gap-2">
              <span className="size-3 bg-block-red" /> Extraction failure
            </li>
          </ul>
        </GridPanel>

        <GridPanel label="Actions" className="flex-1 flex flex-col justify-between">
          <div className="flex flex-col gap-3">
            <button
              type="button"
              onClick={() => loadCaptures(true)}
              disabled={refreshing}
              className="flex items-center justify-center gap-2 border-2 border-grid p-2 text-xs font-black uppercase tracking-wider hover:bg-block-ink hover:text-canvas transition-colors"
            >
              <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />
              Refresh Captures
            </button>
            <p className="text-[11px] font-bold leading-relaxed text-muted-foreground">
              New captures are written directly to PostgreSQL with an outbox entry. The worker polls
              or receives SQS notifications to run LLM extraction.
            </p>
          </div>
        </GridPanel>
      </aside>
    </main>
  );
}
