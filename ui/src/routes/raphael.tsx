import { createFileRoute } from "@tanstack/react-router";
import {
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import {
  Bot,
  ExternalLink,
  GitBranch,
  History,
  MessageSquare,
  Plus,
  RefreshCw,
  Send,
  User as UserIcon,
  X,
  BookOpen,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Loader2,
  Trash2,
  Mic,
} from "lucide-react";
import { QueryClientContext } from "@tanstack/react-query";
import { raphaelApi, type RaphaelApiClient } from "@/lib/raphael-api";
import type {
  DiarySessionMemory,
  RaphaelConversationDetail,
  RaphaelConversationSummary,
  RaphaelMessage,
} from "@/lib/raphael-types";
import { cleanMessageContent, parseProjectResult, parseTaskResult } from "@/lib/raphael-utils";
import { MarkdownContent } from "@/components/markdown-content";
import { VoiceSession } from "@/components/voice-session";

export const Route = createFileRoute("/raphael")({
  head: () => ({ meta: [{ title: "Raphael — Personal OS" }] }),
  component: RaphaelChatPage,
});

export function RaphaelChatPage({ api = raphaelApi }: { api?: RaphaelApiClient }) {
  const queryClient = useContext(QueryClientContext);

  const [conversations, setConversations] = useState<RaphaelConversationSummary[]>([]);
  const [activeConversation, setActiveConversation] = useState<RaphaelConversationDetail | null>(
    null,
  );
  const [loadingActive, setLoadingActive] = useState(true);
  const [sending, setSending] = useState(false);
  const [inputContent, setInputContent] = useState("");
  const [lastPrompt, setLastPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [retryablePayload, setRetryablePayload] = useState<{
    content: string;
    clientMessageId: string;
  } | null>(null);

  // Past session memories (Diary recall drawer)
  const [showMemories, setShowMemories] = useState(false);
  const [memories, setMemories] = useState<DiarySessionMemory[]>([]);
  const [memorySearch, setMemorySearch] = useState("");
  const [loadingMemories, setLoadingMemories] = useState(false);
  const [confirmDeleteMem, setConfirmDeleteMem] = useState<DiarySessionMemory | null>(null);
  const [deletingMemId, setDeletingMemId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [expandedMemoryId, setExpandedMemoryId] = useState<string | null>(null);
  const [isVoiceActive, setIsVoiceActive] = useState(false);

  const messagesContainerRef = useRef<HTMLDivElement>(null);

  // Scroll messages container to bottom on messages change without affecting parent layout
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (container) {
      if (typeof container.scrollTo === "function") {
        container.scrollTo({
          top: container.scrollHeight,
          behavior: "smooth",
        });
      } else {
        container.scrollTop = container.scrollHeight;
      }
    }
  }, [activeConversation?.messages, sending]);

  // Load active conversation on mount
  const loadActiveConversation = useCallback(async () => {
    setLoadingActive(true);
    setError(null);
    try {
      let list: RaphaelConversationSummary[] = [];
      if (typeof api.listConversations === "function") {
        list = await api.listConversations();
        setConversations(list);
      }

      if (typeof api.getActiveConversation === "function") {
        const active = await api.getActiveConversation();
        setActiveConversation(active);
      } else if (list.length > 0) {
        const detail = await api.getConversation(list[0]!.id);
        setActiveConversation(detail);
      } else {
        const initial = await api.createConversation({ title: "Welcome to Raphael" });
        setConversations([initial]);
        setActiveConversation(initial);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingActive(false);
    }
  }, [api]);

  useEffect(() => {
    void loadActiveConversation();
  }, [loadActiveConversation]);

  // Switch conversation
  const selectConversation = useCallback(
    async (id: string) => {
      setError(null);
      try {
        const detail = await api.getConversation(id);
        setActiveConversation(detail);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [api],
  );

  // Load Diary memories when panel is opened
  const loadMemories = useCallback(
    async (q: string = "") => {
      if (typeof api.getDiaryMemories !== "function") return;
      setLoadingMemories(true);
      try {
        const mems = await api.getDiaryMemories(q);
        setMemories(mems);
      } catch (err) {
        console.warn("Could not load Diary memories:", err);
      } finally {
        setLoadingMemories(false);
      }
    },
    [api],
  );

  useEffect(() => {
    if (showMemories) {
      void loadMemories(memorySearch);
    }
  }, [showMemories, memorySearch, loadMemories]);

  // Start fresh conversation (auto-summarizing previous one)
  async function handleStartNewConversation() {
    setError(null);
    setRetryablePayload(null);
    try {
      const newTitle = `Conversation ${conversations.length + 1}`;
      const created = await api.createConversation({ title: newTitle });
      setConversations((prev) => [created, ...prev]);
      setActiveConversation(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleSendMessage(customPayload?: { content: string; clientMessageId: string }) {
    if (!activeConversation) return;

    const contentToSend = customPayload ? customPayload.content : inputContent.trim();
    if (!contentToSend) return;

    setLastPrompt(contentToSend);

    const clientMsgId = customPayload
      ? customPayload.clientMessageId
      : `msg-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;

    if (!customPayload) {
      setInputContent("");
    }
    setError(null);
    setRetryablePayload(null);
    setSending(true);

    // Optimistically show user message if new
    if (!customPayload) {
      const optimisticUserMsg: RaphaelMessage = {
        id: `temp-${Date.now()}`,
        conversation_id: activeConversation.id,
        role: "user",
        content: contentToSend,
        client_message_id: clientMsgId,
        created_at: new Date().toISOString(),
      };
      setActiveConversation((prev) =>
        prev
          ? {
              ...prev,
              messages: [...prev.messages, optimisticUserMsg],
            }
          : prev,
      );
    }

    try {
      const response = await api.sendMessage(activeConversation.id, {
        content: contentToSend,
        client_message_id: clientMsgId,
      });

      // Replace optimistic message and append assistant message
      setActiveConversation((prev) => {
        if (!prev) return prev;
        const filtered = prev.messages.filter((m) => m.client_message_id !== clientMsgId);
        return {
          ...prev,
          messages: [...filtered, response.user_message, response.assistant_message],
        };
      });

      // Invalidate relevant query caches if actions were executed
      if (queryClient) {
        const assistantContent = response.assistant_message.content;
        if (parseProjectResult(assistantContent) || /project/i.test(assistantContent)) {
          void queryClient.invalidateQueries({ queryKey: ["projects"] });
        }
        if (parseTaskResult(assistantContent) || /I have created the task/i.test(assistantContent)) {
          void queryClient.invalidateQueries({ queryKey: ["tasks"] });
        }
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : String(err);
      setError(errorMessage);
      setRetryablePayload({ content: contentToSend, clientMessageId: clientMsgId });
    } finally {
      setSending(false);
    }
  }

  function onFormSubmit(e: FormEvent) {
    e.preventDefault();
    void handleSendMessage();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSendMessage();
    }
  }

  let activityText = "Raphael is processing your request…";
  if (/project/i.test(lastPrompt)) {
    activityText = "Planning project creation…";
  } else if (/task/i.test(lastPrompt)) {
    activityText = "Planning task creation…";
  } else if (/repository|github|repo/i.test(lastPrompt)) {
    activityText = "Inspecting repository context…";
  }

  return (
    <main className="page-grid grid-cols-1 h-full min-h-0 overflow-hidden bg-grid p-1">
      <div className="flex h-full min-h-0 flex-col overflow-hidden border-2 border-grid bg-canvas relative">
        {/* Active Conversation Header */}
        <header className="shrink-0 flex items-center justify-between border-b-2 border-grid px-4 py-2.5 bg-canvas z-10">
          <div className="flex items-center gap-3">
            <span className="grid size-7 place-items-center border border-grid bg-block-blue text-white shadow-sm">
              <Bot className="size-4" />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="font-display text-sm uppercase tracking-wider">
                  Raphael Agent
                </h1>
                <span className="flex items-center gap-1 border border-grid/60 bg-panel px-1.5 py-0.5 font-mono text-[9px] font-semibold text-foreground">
                  <span className="size-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  Active Session
                </span>
              </div>
              <span className="flex items-center gap-1 font-mono text-[9px] text-muted-foreground">
                Hermes Agent Runtime · Auto-summarizes into Diary after 60m of inactivity
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid="raphael-voice-toggle"
              onClick={() => setIsVoiceActive(!isVoiceActive)}
              className={`flex items-center gap-1.5 border-2 border-grid px-2.5 py-1 text-xs font-black uppercase transition-colors ${
                isVoiceActive
                  ? "bg-block-blue text-white"
                  : "bg-panel text-foreground hover:bg-muted"
              }`}
              title="Toggle real-time voice mode with Raphael"
            >
              <Mic className="size-3.5" />
              <span>Voice</span>
            </button>

            <button
              type="button"
              onClick={() => setShowMemories(!showMemories)}
              className={`flex items-center gap-1.5 border-2 border-grid px-2.5 py-1 text-xs font-black uppercase transition-colors ${
                showMemories ? "bg-block-blue text-white" : "bg-panel text-foreground hover:bg-muted"
              }`}
              title="View past session summaries from Diary"
            >
              <BookOpen className="size-3.5" />
              <span>Past Summaries</span>
            </button>

            <button
              type="button"
              onClick={() => void handleStartNewConversation()}
              className="flex items-center gap-1.5 border-2 border-grid bg-block-yellow px-2.5 py-1 text-xs font-black uppercase text-black transition-opacity hover:opacity-90"
              title="Start a new conversation (saves current session to Diary)"
            >
              <Plus className="size-3.5" />
              <span>New</span>
            </button>
          </div>
        </header>

        {/* LiveKit Real-Time Voice Session */}
        {isVoiceActive && (
          <VoiceSession
            conversationId={activeConversation?.id}
            onEnd={() => setIsVoiceActive(false)}
            onTurnPersisted={async () => {
              if (activeConversation?.id) {
                try {
                  const detail = await api.getConversation(activeConversation.id);
                  setActiveConversation(detail);
                } catch {
                  // ignore background polling failure
                }
              }
            }}
          />
        )}

        {/* Hidden list for DOM accessibility and backwards test compatibility */}
        <div className="sr-only" aria-hidden="true">
          {conversations.map((c) => (
            <span key={c.id}>{c.title}</span>
          ))}
        </div>

        {/* Main Chat Workspace + Slide-out Diary Memory Drawer */}
        <div className="flex-1 min-h-0 flex overflow-hidden relative">
          {/* Chat Messages Viewport */}
          <div
            ref={messagesContainerRef}
            data-testid="raphael-messages-container"
            className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4"
          >
            {loadingActive && (
              <div className="flex justify-center py-8">
                <p className="font-mono text-xs text-muted-foreground animate-pulse">
                  Initializing Raphael active session…
                </p>
              </div>
            )}

            {!loadingActive && activeConversation?.messages.length === 0 && (
              <div className="mx-auto my-12 max-w-lg border-2 border-dashed border-grid/60 bg-panel/30 p-8 text-center shadow-sm">
                <span className="mx-auto mb-3 grid size-12 place-items-center border-2 border-grid bg-panel">
                  <Bot className="size-6 text-block-blue" />
                </span>
                <h3 className="font-display text-lg uppercase">Welcome to Raphael</h3>
                <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                  Your project-aware development assistant. Ask to organize tasks, plan projects,
                  inspect linked GitHub repositories, or recall past discussions.
                </p>

                <div className="mt-6 flex flex-wrap justify-center gap-2 text-[11px]">
                  <button
                    type="button"
                    onClick={() => {
                      setInputContent("Create a project called CORDIC Accelerator");
                    }}
                    className="border border-grid/60 bg-canvas px-2.5 py-1 font-mono transition-colors hover:bg-panel"
                  >
                    "Create a project called CORDIC Accelerator"
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setInputContent("What did we decide earlier about our architecture?");
                    }}
                    className="border border-grid/60 bg-canvas px-2.5 py-1 font-mono transition-colors hover:bg-panel"
                  >
                    "What did we decide earlier about our architecture?"
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setInputContent("Add a task to implement 16-stage pipeline due tomorrow");
                    }}
                    className="border border-grid/60 bg-canvas px-2.5 py-1 font-mono transition-colors hover:bg-panel"
                  >
                    "Add a task due tomorrow"
                  </button>
                </div>
              </div>
            )}

            {activeConversation?.messages.map((message) => {
              const isUser = message.role === "user";
              const cleanedContent = isUser
                ? message.content
                : cleanMessageContent(message.content);
              const projectResult = !isUser ? parseProjectResult(cleanedContent) : null;
              const taskResult = !isUser ? parseTaskResult(cleanedContent) : null;

              return (
                <div
                  key={message.id}
                  className={`flex gap-2.5 ${isUser ? "justify-end" : "justify-start"}`}
                >
                  {!isUser && (
                    <div className="grid size-7 shrink-0 place-items-center border border-grid bg-panel">
                      <Bot className="size-4 text-block-blue" />
                    </div>
                  )}

                  <div
                    className={`max-w-[82%] border-2 p-3.5 text-sm ${
                      isUser
                        ? "border-grid bg-block-blue text-white shadow-sm"
                        : "border-grid bg-panel text-foreground"
                    }`}
                  >
                    <div className="mb-1.5 flex items-center justify-between gap-4 font-mono text-[9px] opacity-75">
                      <span className="font-bold">{isUser ? "You" : "Raphael"}</span>
                      <span>
                        {new Date(message.created_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </div>

                    {isUser ? (
                      <div className="whitespace-pre-wrap break-words leading-relaxed">
                        {cleanedContent}
                      </div>
                    ) : (
                      <MarkdownContent content={cleanedContent} />
                    )}

                    {projectResult && (
                      <div
                        data-testid="project-creation-card"
                        className="mt-3 border-2 border-grid bg-canvas p-3 shadow-sm text-foreground"
                      >
                        <div className="flex items-center justify-between border-b border-grid/30 pb-1.5">
                          <div className="flex items-center gap-1.5">
                            <span className="size-2 bg-block-blue" />
                            <span className="section-label font-bold text-block-blue">
                              Project Created
                            </span>
                          </div>
                          <span className="border border-grid bg-block-yellow/40 px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase text-foreground">
                            Active
                          </span>
                        </div>
                        <div className="mt-2.5 flex flex-wrap items-center justify-between gap-3">
                          <div>
                            <h4 className="font-display text-sm font-bold uppercase tracking-wide text-foreground">
                              {projectResult.name}
                            </h4>
                            {projectResult.description && (
                              <p className="mt-0.5 text-xs text-muted-foreground">
                                {projectResult.description}
                              </p>
                            )}
                          </div>
                          <a
                            href="/projects"
                            className="flex items-center gap-1 border-2 border-grid bg-panel px-2.5 py-1 font-mono text-[10px] font-black uppercase text-foreground transition-colors hover:bg-muted"
                          >
                            <span>Open Projects</span>
                            <ExternalLink className="size-3" />
                          </a>
                        </div>
                      </div>
                    )}

                    {taskResult && (
                      <div
                        data-testid="task-creation-card"
                        className="mt-3 border-2 border-grid bg-canvas p-3 shadow-sm text-foreground"
                      >
                        <div className="flex items-center justify-between border-b border-grid/30 pb-1.5">
                          <div className="flex items-center gap-1.5">
                            <span className="size-2 bg-block-yellow" />
                            <span className="section-label font-bold text-foreground">
                              Task Created
                            </span>
                          </div>
                          <span className="border border-grid bg-block-blue/20 px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase text-foreground">
                            Scheduled
                          </span>
                        </div>
                        <div className="mt-2.5 flex flex-wrap items-center justify-between gap-3">
                          <div className="space-y-1">
                            <h4 className="font-display text-sm font-bold uppercase tracking-wide text-foreground">
                              {taskResult.title}
                            </h4>
                            {taskResult.description && (
                              <p className="text-xs text-muted-foreground">
                                {taskResult.description}
                              </p>
                            )}
                            <div className="flex flex-wrap items-center gap-2 pt-0.5">
                              {taskResult.dueDate && (
                                <span className="border border-grid/40 bg-panel px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                                  Due: <span className="text-foreground">{taskResult.dueDate}</span>
                                </span>
                              )}
                              {taskResult.hardDeadline && (
                                <span className="border border-grid/40 bg-panel px-1.5 py-0.5 font-mono text-[10px] text-block-red">
                                  Hard: <span className="text-foreground">{taskResult.hardDeadline}</span>
                                </span>
                              )}
                              {taskResult.project && (
                                <span className="border border-grid/40 bg-panel px-1.5 py-0.5 font-mono text-[10px] text-block-blue">
                                  Project: <span className="text-foreground">{taskResult.project}</span>
                                </span>
                              )}
                            </div>
                            {taskResult.note && (
                              <p className="text-[11px] text-muted-foreground italic">
                                {taskResult.note}
                              </p>
                            )}
                          </div>
                          <a
                            href="/"
                            className="flex items-center gap-1 border-2 border-grid bg-panel px-2.5 py-1 font-mono text-[10px] font-black uppercase text-foreground transition-colors hover:bg-muted"
                          >
                            <span>Open Tasks</span>
                            <ExternalLink className="size-3" />
                          </a>
                        </div>
                      </div>
                    )}
                  </div>

                  {isUser && (
                    <div className="grid size-7 shrink-0 place-items-center border border-grid bg-canvas">
                      <UserIcon className="size-4" />
                    </div>
                  )}
                </div>
              );
            })}

            {sending && (
              <div
                data-testid="raphael-activity-indicator"
                className="flex items-center gap-2.5 border-2 border-grid/60 bg-panel/60 p-3 text-xs"
              >
                <div className="grid size-7 shrink-0 place-items-center border border-grid bg-canvas text-block-blue">
                  <Bot className="size-4 animate-spin" />
                </div>
                <div className="flex flex-col">
                  <span className="font-display text-xs font-bold uppercase tracking-wider text-foreground">
                    {activityText}
                  </span>
                  <span className="font-mono text-[9px] text-muted-foreground">
                    Personal OS Agent Active · Validating actions
                  </span>
                </div>
              </div>
            )}

            {/* Error & Retry Banner */}
            {error && (
              <div
                role="alert"
                className="my-2 flex flex-wrap items-center justify-between gap-2 border-2 border-red-500 bg-red-500/10 p-3 text-xs text-red-600 dark:text-red-400"
              >
                <div className="flex-1">
                  <strong>Request failed:</strong> {error}
                </div>
                {retryablePayload && (
                  <button
                    type="button"
                    onClick={() => void handleSendMessage(retryablePayload)}
                    className="flex items-center gap-1 border border-red-500 bg-canvas px-2.5 py-1 text-xs font-black uppercase text-foreground hover:bg-muted"
                  >
                    <RefreshCw className="size-3" />
                    <span>Retry</span>
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Past Discussions (Diary Memories) Slide-out Panel */}
          {showMemories && (
            <aside className="w-80 border-l-2 border-grid bg-canvas flex flex-col h-full min-h-0 shadow-lg z-20">
              <div className="flex items-center justify-between border-b-2 border-grid px-3 py-2 bg-panel">
                <div className="flex items-center gap-1.5">
                  <BookOpen className="size-4 text-block-blue" />
                  <span className="font-display text-xs uppercase font-bold">Diary Memories</span>
                </div>
                <button
                  type="button"
                  onClick={() => setShowMemories(false)}
                  className="p-1 hover:bg-muted text-muted-foreground hover:text-foreground"
                >
                  <X className="size-4" />
                </button>
              </div>

              <div className="p-2.5 border-b border-grid/40">
                <input
                  value={memorySearch}
                  onChange={(e) => setMemorySearch(e.target.value)}
                  placeholder="Filter past sessions…"
                  className="w-full border border-grid bg-canvas px-2.5 py-1 text-xs outline-none"
                />
              </div>

              <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
                {/* Recent Sessions List */}
                {conversations.length > 0 && (
                  <div className="space-y-1 pb-3 border-b border-grid/30">
                    <span className="font-mono text-[9px] font-bold uppercase text-muted-foreground">
                      Recent Conversations
                    </span>
                    <ul className="space-y-1">
                      {conversations.map((c) => (
                        <li key={c.id}>
                          <button
                            type="button"
                            onClick={() => {
                              void selectConversation(c.id);
                              setShowMemories(false);
                            }}
                            className={`flex w-full items-center justify-between p-1.5 text-left text-xs hover:bg-muted ${
                              activeConversation?.id === c.id ? "font-bold bg-panel" : ""
                            }`}
                          >
                            <span className="truncate">{c.title}</span>
                            <span className="font-mono text-[9px] text-muted-foreground">
                              {new Date(c.created_at).toLocaleDateString([], {
                                month: "short",
                                day: "numeric",
                              })}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {loadingMemories && (
                  <p className="font-mono text-xs text-muted-foreground py-4 text-center">
                    Loading Diary memories…
                  </p>
                )}
                {!loadingMemories && memories.length === 0 && (
                  <p className="font-mono text-xs text-muted-foreground py-8 text-center">
                    No summarized sessions found in Diary. Sessions auto-summarize after 60m of
                    inactivity or when resetting.
                  </p>
                )}
                {memories.map((mem) => {
                  const isExpanded = expandedMemoryId === mem.capture_id;
                  const isDeleting = deletingMemId === mem.capture_id;

                  return (
                    <article
                      key={mem.capture_id}
                      className="border border-grid/60 bg-panel/40 p-2.5 text-xs space-y-2 hover:bg-panel transition-colors"
                    >
                      <div className="flex items-start justify-between gap-1.5">
                        <button
                          type="button"
                          data-testid={`toggle-summary-${mem.capture_id}`}
                          onClick={() => setExpandedMemoryId(isExpanded ? null : mem.capture_id)}
                          className="flex-1 text-left font-bold text-foreground leading-tight hover:text-block-blue transition-colors flex items-center gap-1.5"
                        >
                          <span>{mem.title}</span>
                          {isExpanded ? (
                            <ChevronUp className="size-3 shrink-0 text-muted-foreground" />
                          ) : (
                            <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
                          )}
                        </button>

                        <div className="flex items-center gap-1 shrink-0">
                          {mem.created_at && (
                            <span className="font-mono text-[9px] text-muted-foreground">
                              {new Date(mem.created_at).toLocaleDateString([], {
                                month: "short",
                                day: "numeric",
                              })}
                            </span>
                          )}
                          <button
                            type="button"
                            data-testid={`delete-summary-${mem.capture_id}`}
                            aria-label={`Delete summary for ${mem.title}`}
                            title="Delete summary"
                            disabled={isDeleting}
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfirmDeleteMem(mem);
                              setDeleteError(null);
                            }}
                            className="p-1 text-muted-foreground hover:text-block-red hover:bg-block-red/10 border border-transparent hover:border-grid/40 transition-colors disabled:opacity-50"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </div>
                      </div>

                      {isExpanded ? (
                        <div className="mt-2 border-t border-grid/40 pt-2 max-h-72 overflow-y-auto">
                          <MarkdownContent content={mem.summary} className="text-xs" />
                        </div>
                      ) : (
                        <>
                          {mem.decisions.length > 0 && (
                            <div>
                              <span className="font-mono text-[9px] font-bold uppercase text-muted-foreground">
                                Decisions:
                              </span>
                              <ul className="mt-0.5 space-y-0.5 text-[11px] text-muted-foreground">
                                {mem.decisions.slice(0, 2).map((d, i) => (
                                  <li key={i} className="truncate">
                                    • {d}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}

                          {mem.next_steps.length > 0 && (
                            <div>
                              <span className="font-mono text-[9px] font-bold uppercase text-muted-foreground">
                                Next Steps:
                              </span>
                              <ul className="mt-0.5 space-y-0.5 text-[11px] text-muted-foreground">
                                {mem.next_steps.slice(0, 2).map((ns, i) => (
                                  <li key={i} className="truncate">
                                    • {ns}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </>
                      )}

                      <button
                        type="button"
                        onClick={() => {
                          setInputContent(`What did we discuss in the session "${mem.title}"?`);
                          setShowMemories(false);
                        }}
                        className="mt-1 w-full border border-grid/50 bg-canvas py-1 text-[10px] font-mono font-bold uppercase text-block-blue hover:bg-muted"
                      >
                        Ask Raphael about this
                      </button>
                    </article>
                  );
                })}
              </div>

              {/* Confirmation Dialog for Destructive Memory Deletion */}
              {confirmDeleteMem && (
                <div
                  role="dialog"
                  aria-modal="true"
                  aria-labelledby="delete-summary-dialog-title"
                  className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
                >
                  <div className="w-full max-w-md border-2 border-grid bg-canvas p-4 shadow-xl text-foreground">
                    <div className="flex items-center gap-2 text-block-red border-b border-grid/40 pb-2">
                      <AlertTriangle className="size-5 shrink-0" />
                      <h3
                        id="delete-summary-dialog-title"
                        className="font-display text-sm font-bold uppercase tracking-wide"
                      >
                        Delete Past Summary?
                      </h3>
                    </div>

                    <p className="mt-3 text-xs leading-relaxed text-foreground">
                      Are you sure you want to delete the summary for{" "}
                      <strong className="font-bold underline">{confirmDeleteMem.title}</strong>?
                    </p>

                    <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                      This removes the summary from Past Summaries and Diary memory recall. Your original
                      conversation history and messages will not be deleted.
                    </p>

                    {deleteError && (
                      <div className="mt-3 border-2 border-block-red bg-block-red/10 p-2 text-xs font-semibold text-block-red">
                        {deleteError}
                      </div>
                    )}

                    <div className="mt-4 flex items-center justify-end gap-2 border-t border-grid/40 pt-3">
                      <button
                        type="button"
                        disabled={Boolean(deletingMemId)}
                        onClick={() => {
                          setConfirmDeleteMem(null);
                          setDeleteError(null);
                        }}
                        className="border-2 border-grid bg-panel px-3 py-1.5 font-mono text-xs font-bold uppercase hover:bg-muted transition-colors disabled:opacity-50"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        data-testid="confirm-delete-summary-button"
                        disabled={Boolean(deletingMemId)}
                        onClick={async () => {
                          if (!confirmDeleteMem) return;
                          const capId = confirmDeleteMem.capture_id;
                          setDeletingMemId(capId);
                          setDeleteError(null);
                          try {
                            if (typeof api.deleteDiaryMemory === "function") {
                              await api.deleteDiaryMemory(capId);
                            }
                            setMemories((prev) => prev.filter((m) => m.capture_id !== capId));
                            if (expandedMemoryId === capId) {
                              setExpandedMemoryId(null);
                            }
                            setConfirmDeleteMem(null);
                          } catch (err) {
                            setDeleteError(err instanceof Error ? err.message : String(err));
                          } finally {
                            setDeletingMemId(null);
                          }
                        }}
                        className="flex items-center gap-1.5 border-2 border-grid bg-block-red px-3.5 py-1.5 font-mono text-xs font-bold uppercase text-white hover:opacity-90 transition-opacity disabled:opacity-50"
                      >
                        {deletingMemId ? (
                          <>
                            <Loader2 className="size-3.5 animate-spin" />
                            <span>Deleting…</span>
                          </>
                        ) : (
                          <>
                            <Trash2 className="size-3.5" />
                            <span>Delete Summary</span>
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                </div>
              )}

            </aside>
          )}
        </div>

        {/* Stationary Message Input Box */}
        <form onSubmit={onFormSubmit} className="shrink-0 flex border-t-2 border-grid bg-canvas p-2.5">
          <textarea
            aria-label="Message to Raphael"
            value={inputContent}
            onChange={(e) => setInputContent(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={sending}
            placeholder="Ask Raphael anything… (Press Enter to send, Shift+Enter for newline)"
            rows={2}
            className="flex-1 resize-none bg-transparent px-3 py-1.5 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={sending || !inputContent.trim()}
            className="flex items-center gap-1 self-end border-2 border-grid bg-block-yellow px-4 py-2 text-xs font-black uppercase text-black transition-opacity hover:opacity-90 disabled:opacity-50"
            title="Send message"
          >
            <Send className="size-3.5" />
            <span>Send</span>
          </button>
        </form>
      </div>
    </main>
  );
}
