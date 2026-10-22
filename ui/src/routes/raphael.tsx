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
  MessageSquare,
  Plus,
  RefreshCw,
  Send,
  User as UserIcon,
} from "lucide-react";
import { QueryClientContext } from "@tanstack/react-query";
import { raphaelApi, type RaphaelApiClient } from "@/lib/raphael-api";
import type {
  RaphaelConversationDetail,
  RaphaelConversationSummary,
  RaphaelMessage,
} from "@/lib/raphael-types";
import { cleanMessageContent, parseProjectResult } from "@/lib/raphael-utils";

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
  const [loadingList, setLoadingList] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [inputContent, setInputContent] = useState("");
  const [lastPrompt, setLastPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [retryablePayload, setRetryablePayload] = useState<{
    content: string;
    clientMessageId: string;
  } | null>(null);

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

  const selectConversation = useCallback(
    async (id: string) => {
      setLoadingMessages(true);
      setError(null);
      setRetryablePayload(null);
      try {
        const detail = await api.getConversation(id);
        setActiveConversation(detail);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoadingMessages(false);
      }
    },
    [api],
  );

  // Load conversation list on mount
  useEffect(() => {
    let mounted = true;
    async function loadConversations() {
      setLoadingList(true);
      setError(null);
      try {
        const list = await api.listConversations();
        if (!mounted) return;
        setConversations(list);
        if (list.length > 0) {
          await selectConversation(list[0]!.id);
        } else {
          const initial = await api.createConversation({ title: "Welcome to Raphael" });
          if (!mounted) return;
          setConversations([initial]);
          setActiveConversation(initial);
        }
      } catch (err) {
        if (!mounted) return;
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (mounted) setLoadingList(false);
      }
    }
    void loadConversations();
    return () => {
      mounted = false;
    };
  }, [api, selectConversation]);

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
        if (/I have created the task/i.test(assistantContent)) {
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
  }

  return (
    <main className="page-grid grid-cols-1 min-h-0 max-h-[calc(100vh-4.5rem)] overflow-hidden bg-grid p-1">
      <div className="grid h-full max-h-[calc(100vh-4.5rem)] min-h-0 grid-cols-1 gap-1 overflow-hidden lg:grid-cols-[280px_minmax(0,1fr)]">
        {/* Left column: Conversations sidebar */}
        <section className="flex flex-col h-full min-h-0 overflow-hidden border-2 border-grid bg-canvas p-3">
          <div className="mb-3 flex shrink-0 items-center justify-between border-b-2 border-grid pb-2">
            <div>
              <span className="section-label text-muted-foreground">Conversations</span>
              <h2 className="font-display text-xl uppercase tracking-tight">Raphael</h2>
            </div>
            <button
              type="button"
              onClick={() => void handleStartNewConversation()}
              className="flex items-center gap-1 border-2 border-grid bg-canvas px-2.5 py-1 text-xs font-black uppercase transition-colors hover:bg-muted"
              title="Start a new conversation"
            >
              <Plus className="size-3.5" />
              <span>New</span>
            </button>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto">
            {loadingList && (
              <p className="py-4 text-xs text-muted-foreground">Loading conversations…</p>
            )}
            {!loadingList && conversations.length === 0 && (
              <p className="py-4 text-xs text-muted-foreground">No conversations yet.</p>
            )}
            <ul className="space-y-1">
              {conversations.map((conv) => {
                const isActive = activeConversation?.id === conv.id;
                return (
                  <li key={conv.id}>
                    <button
                      type="button"
                      onClick={() => void selectConversation(conv.id)}
                      className={`flex w-full items-start gap-2 border border-grid/40 p-2.5 text-left transition-colors ${
                        isActive ? "bg-muted font-bold shadow-sm" : "hover:bg-panel/60"
                      }`}
                    >
                      <MessageSquare className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs">{conv.title}</div>
                        <div className="mt-0.5 truncate font-mono text-[9px] text-muted-foreground">
                          {new Date(conv.created_at).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </div>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        </section>

        {/* Right column: Chat area */}
        <section className="flex flex-col h-full min-h-0 overflow-hidden border-2 border-grid bg-canvas">
          {/* Chat Header */}
          <header className="shrink-0 flex items-center justify-between border-b-2 border-grid px-4 py-2.5">
            <div className="flex items-center gap-2">
              <span className="grid size-6 place-items-center border border-grid bg-block-blue text-white">
                <Bot className="size-3.5" />
              </span>
              <div>
                <h1 className="font-display text-sm uppercase tracking-wider">
                  {activeConversation?.title || "Raphael"}
                </h1>
                <span className="flex items-center gap-1 font-mono text-[9px] text-muted-foreground">
                  <span className="size-1.5 rounded-full bg-emerald-500" />
                  Hermes Agent Runtime
                </span>
              </div>
            </div>

            <div className="font-mono text-[10px] text-muted-foreground">
              {activeConversation ? `ID: ${activeConversation.id}` : ""}
            </div>
          </header>

          {/* Messages Viewport */}
          <div
            ref={messagesContainerRef}
            data-testid="raphael-messages-container"
            className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4"
          >
            {loadingMessages && (
              <div className="flex justify-center py-8">
                <p className="font-mono text-xs text-muted-foreground">Loading message history…</p>
              </div>
            )}

            {!loadingMessages && activeConversation?.messages.length === 0 && (
              <div className="mx-auto my-12 max-w-md border-2 border-dashed border-grid/50 p-6 text-center">
                <span className="mx-auto mb-2 grid size-10 place-items-center border-2 border-grid bg-panel">
                  <Bot className="size-5" />
                </span>
                <h3 className="font-display text-base uppercase">Ask Raphael</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  Raphael is the conversational interface for Personal OS. Type a message below to
                  begin chatting with the Hermes runtime.
                </p>
              </div>
            )}

            {activeConversation?.messages.map((message) => {
              const isUser = message.role === "user";
              const cleanedContent = isUser
                ? message.content
                : cleanMessageContent(message.content);
              const projectResult = !isUser ? parseProjectResult(cleanedContent) : null;

              return (
                <div
                  key={message.id}
                  className={`flex gap-2.5 ${isUser ? "justify-end" : "justify-start"}`}
                >
                  {!isUser && (
                    <div className="grid size-7 shrink-0 place-items-center border border-grid bg-panel">
                      <Bot className="size-4" />
                    </div>
                  )}

                  <div
                    className={`max-w-[82%] border-2 p-3 text-sm ${
                      isUser
                        ? "border-grid bg-block-blue text-white shadow-sm"
                        : "border-grid bg-panel text-foreground"
                    }`}
                  >
                    <div className="mb-1 flex items-center justify-between gap-4 font-mono text-[9px] opacity-75">
                      <span>{isUser ? "You" : "Raphael"}</span>
                      <span>
                        {new Date(message.created_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </div>

                    <div className="whitespace-pre-wrap break-words leading-relaxed">
                      {cleanedContent}
                    </div>

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

          {/* Message Input Box */}
          <form
            onSubmit={onFormSubmit}
            className="shrink-0 flex border-t-2 border-grid bg-canvas p-2.5"
          >
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
        </section>
      </div>
    </main>
  );
}
