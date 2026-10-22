import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RaphaelChatPage } from "@/routes/raphael";
import type { RaphaelApiClient } from "@/lib/raphael-api";
import type {
  RaphaelConversationDetail,
  RaphaelConversationSummary,
  SendMessageResponse,
} from "@/lib/raphael-types";

describe("RaphaelChatPage", () => {
  const initialConv: RaphaelConversationDetail = {
    id: "conv-100",
    title: "Test Conversation",
    status: "active",
    created_at: "2026-10-19T10:00:00Z",
    updated_at: "2026-10-19T10:00:00Z",
    messages: [
      {
        id: "msg-1",
        conversation_id: "conv-100",
        role: "user",
        content: "What tasks are open?",
        created_at: "2026-10-19T10:00:00Z",
      },
      {
        id: "msg-2",
        conversation_id: "conv-100",
        role: "assistant",
        content: "You have 3 open tasks.",
        created_at: "2026-10-19T10:00:02Z",
      },
    ],
  };

  const mockApi: Partial<RaphaelApiClient> = {
    listConversations: vi.fn().mockResolvedValue([
      {
        id: "conv-100",
        title: "Test Conversation",
        status: "active",
        created_at: "2026-10-19T10:00:00Z",
        updated_at: "2026-10-19T10:00:00Z",
      },
    ] as RaphaelConversationSummary[]),
    getConversation: vi.fn().mockResolvedValue(initialConv),
    createConversation: vi.fn().mockImplementation(async ({ title }: { title?: string }) => ({
      id: "conv-200",
      title: title || "New Chat",
      status: "active",
      created_at: "2026-10-19T10:05:00Z",
      updated_at: "2026-10-19T10:05:00Z",
      messages: [],
    })),
    sendMessage: vi.fn().mockResolvedValue({
      user_message: {
        id: "msg-3",
        conversation_id: "conv-100",
        role: "user",
        content: "Show projects",
        created_at: "2026-10-19T10:02:00Z",
      },
      assistant_message: {
        id: "msg-4",
        conversation_id: "conv-100",
        role: "assistant",
        content: "Here are your projects: Alpha, Beta.",
        created_at: "2026-10-19T10:02:02Z",
      },
    } as SendMessageResponse),
    getDiaryMemories: vi.fn().mockResolvedValue([
      {
        capture_id: "cap-1",
        title: "Architecture Planning",
        summary: "# Session Summary: Architecture Planning\n\nDecided to use PostgreSQL.",
        topics: ["Architecture"],
        decisions: ["Use PostgreSQL"],
        tasks: [],
        next_steps: ["Run migrations"],
        created_at: "2026-10-19T10:00:00Z",
      },
    ]),
    deleteDiaryMemory: vi.fn().mockResolvedValue(undefined),
  };

  it("renders conversation history with distinct user and assistant messages", async () => {
    render(<RaphaelChatPage api={mockApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getAllByText("Test Conversation").length).toBeGreaterThan(0);
    });

    expect(screen.getByText("What tasks are open?")).toBeInTheDocument();
    expect(screen.getByText("You have 3 open tasks.")).toBeInTheDocument();
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getAllByText("Raphael").length).toBeGreaterThan(0);
  });

  it("submits message and displays assistant response", async () => {
    render(<RaphaelChatPage api={mockApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getByText("What tasks are open?")).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/Ask Raphael anything/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(textarea, { target: { value: "Show projects" } });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(mockApi.sendMessage).toHaveBeenCalledWith(
        "conv-100",
        expect.objectContaining({
          content: "Show projects",
        }),
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Here are your projects: Alpha, Beta.")).toBeInTheDocument();
    });
  });

  it("shows error banner and allows retry on failure", async () => {
    const failingApi: Partial<RaphaelApiClient> = {
      ...mockApi,
      sendMessage: vi
        .fn()
        .mockRejectedValueOnce(new Error("Hermes agent timed out"))
        .mockResolvedValueOnce({
          user_message: {
            id: "msg-3",
            conversation_id: "conv-100",
            role: "user",
            content: "Retry me",
            created_at: "2026-10-19T10:02:00Z",
          },
          assistant_message: {
            id: "msg-4",
            conversation_id: "conv-100",
            role: "assistant",
            content: "Successfully answered after retry",
            created_at: "2026-10-19T10:02:02Z",
          },
        } as SendMessageResponse),
    };

    render(<RaphaelChatPage api={failingApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getByText("What tasks are open?")).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/Ask Raphael anything/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(textarea, { target: { value: "Retry me" } });
    fireEvent.click(sendButton);

    // Error banner appears
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByText(/Hermes agent timed out/i)).toBeInTheDocument();
    });

    // Retry button appears
    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    expect(retryBtn).toBeInTheDocument();

    // Click retry
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(screen.getByText("Successfully answered after retry")).toBeInTheDocument();
    });
  });

  it("creates a new conversation without deleting history", async () => {
    render(<RaphaelChatPage api={mockApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getAllByText("Test Conversation").length).toBeGreaterThan(0);
    });

    const newBtn = screen.getByRole("button", { name: /New/i });
    fireEvent.click(newBtn);

    await waitFor(() => {
      expect(mockApi.createConversation).toHaveBeenCalled();
    });

    // Both conversations exist in list
    await waitFor(() => {
      expect(screen.getAllByText("Conversation 2").length).toBeGreaterThan(0);
      expect(screen.getAllByText("Test Conversation").length).toBeGreaterThan(0);
    });
  });

  it("hides internal model reasoning and renders structured project creation card", async () => {
    const rawReasoningAndProject =
      "┌─ Reasoning ──────────────────────────────────────────────────────────────────┐\n" +
      "│ The user wants to add a project named 'cordic' to their project list.         │\n" +
      "│ Looking at the available tools and instructions:                             │\n" +
      "│ - We can output an action block:                                             │\n" +
      "│   ```action                                                                  │\n" +
      '│   {"action": "create_project", "name": "CORDIC", "description": null}         │\n' +
      "│   ```                                                                        │\n" +
      "│ Let's do that to create the project.                                         │\n" +
      "└──────────────────────────────────────────────────────────────────────────────┘\n" +
      "I have created the project **CORDIC**.\n\nAdding the CORDIC project to your list now...";

    const projectCreationApi: Partial<RaphaelApiClient> = {
      ...mockApi,
      getConversation: vi.fn().mockResolvedValue({
        id: "conv-cordic",
        title: "Project Creation",
        status: "active",
        created_at: "2026-10-19T10:00:00Z",
        updated_at: "2026-10-19T10:00:00Z",
        messages: [
          {
            id: "msg-1",
            conversation_id: "conv-cordic",
            role: "user",
            content: "hey i just wanted to add a project into the list named cordic",
            created_at: "2026-10-19T10:00:00Z",
          },
          {
            id: "msg-2",
            conversation_id: "conv-cordic",
            role: "assistant",
            content: rawReasoningAndProject,
            created_at: "2026-10-19T10:00:05Z",
          },
        ],
      }),
      listConversations: vi.fn().mockResolvedValue([
        {
          id: "conv-cordic",
          title: "Project Creation",
          status: "active",
          created_at: "2026-10-19T10:00:00Z",
          updated_at: "2026-10-19T10:00:00Z",
        },
      ]),
    };

    render(<RaphaelChatPage api={projectCreationApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getAllByText("Project Creation").length).toBeGreaterThan(0);
    });

    // 1. Internal model reasoning is NOT displayed in the UI
    expect(screen.queryByText(/The user wants to add a project/i)).toBeNull();
    expect(screen.queryByText(/Looking at the available tools/i)).toBeNull();
    expect(screen.queryByText(/┌─ Reasoning/i)).toBeNull();

    // 2. Clean confirmation text IS rendered
    expect(screen.getByText(/I have created the project/i)).toBeInTheDocument();
    expect(screen.getByText(/Adding the CORDIC project to your list now/i)).toBeInTheDocument();

    // 3. Structured project creation result card IS rendered
    const projectCard = screen.getByTestId("project-creation-card");
    expect(projectCard).toBeInTheDocument();
    expect(within(projectCard).getByText("CORDIC")).toBeInTheDocument();
    expect(screen.getByText("Project Created")).toBeInTheDocument();
    expect(screen.getByText("Open Projects")).toBeInTheDocument();
  });

  it("shows concise activity state when planning project creation", async () => {
    let resolveSendMessage: (value: SendMessageResponse) => void;
    const pendingPromise = new Promise<SendMessageResponse>((resolve) => {
      resolveSendMessage = resolve;
    });

    const delayedApi: Partial<RaphaelApiClient> = {
      ...mockApi,
      sendMessage: vi.fn().mockReturnValue(pendingPromise),
    };

    render(<RaphaelChatPage api={delayedApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getByText("What tasks are open?")).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/Ask Raphael anything/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(textarea, { target: { value: "create a project named CORDIC" } });
    fireEvent.click(sendButton);

    // Activity state indicator is shown with concise project planning text
    await waitFor(() => {
      const activityIndicator = screen.getByTestId("raphael-activity-indicator");
      expect(activityIndicator).toBeInTheDocument();
      expect(screen.getByText("Planning project creation…")).toBeInTheDocument();
    });

    // Does NOT render raw model reasoning
    expect(screen.queryByText(/Reasoning/i)).toBeNull();

    // Resolve the promise
    resolveSendMessage!({
      user_message: {
        id: "msg-u-1",
        conversation_id: "conv-100",
        role: "user",
        content: "create a project named CORDIC",
        created_at: "2026-10-19T10:02:00Z",
      },
      assistant_message: {
        id: "msg-a-1",
        conversation_id: "conv-100",
        role: "assistant",
        content: "I have created the project **CORDIC**.",
        created_at: "2026-10-19T10:02:02Z",
      },
    });

    await waitFor(() => {
      expect(screen.queryByTestId("raphael-activity-indicator")).toBeNull();
      expect(screen.getByTestId("project-creation-card")).toBeInTheDocument();
    });
  });

  it("ensures message container is scrollable and layout is properly contained", async () => {
    render(<RaphaelChatPage api={mockApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getByText("What tasks are open?")).toBeInTheDocument();
    });

    const messagesContainer = screen.getByTestId("raphael-messages-container");
    expect(messagesContainer).toHaveClass("overflow-y-auto");
    expect(messagesContainer).toHaveClass("min-h-0");
  });

  it("renders assistant messages formatted with rich Markdown", async () => {
    const mdConv: RaphaelConversationDetail = {
      ...initialConv,
      messages: [
        {
          id: "m-user-md",
          conversation_id: "conv-100",
          role: "user",
          content: "Explain the architecture plan",
          created_at: "2026-10-19T10:00:00Z",
        },
        {
          id: "m-asst-md",
          conversation_id: "conv-100",
          role: "assistant",
          content:
            "### Compute & Infrastructure (Week 1)\nWe chose **Postgres** for persistence.\n- Step 1: `alembic upgrade head`\n- Step 2: verify connections",
          created_at: "2026-10-19T10:00:02Z",
        },
      ],
    };

    const mdApi: Partial<RaphaelApiClient> = {
      ...mockApi,
      getConversation: vi.fn().mockResolvedValue(mdConv),
    };

    const { container } = render(<RaphaelChatPage api={mdApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getByText("Explain the architecture plan")).toBeInTheDocument();
    });

    // Heading level 3 rendered
    const heading = screen.getByRole("heading", { level: 3 });
    expect(heading).toHaveTextContent("Compute & Infrastructure (Week 1)");

    // Bold text rendered in strong tag
    const strongEl = screen.getByText("Postgres");
    expect(strongEl.tagName).toBe("STRONG");

    // Inline code rendered in code tag
    const codeEl = screen.getByText("alembic upgrade head");
    expect(codeEl.tagName).toBe("CODE");

    // List items rendered
    const listItems = container.querySelectorAll("li");
    expect(listItems.length).toBe(2);
  });

  it("supports deleting a past session summary with confirmation and updates the drawer", async () => {
    render(<RaphaelChatPage api={mockApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getByText("What tasks are open?")).toBeInTheDocument();
    });

    // Open Past Summaries drawer
    const memoriesButton = screen.getByRole("button", { name: /Past Summaries/i });
    fireEvent.click(memoriesButton);

    await waitFor(() => {
      expect(screen.getByText("Architecture Planning")).toBeInTheDocument();
    });

    // Click delete trash button
    const deleteBtn = screen.getByTestId("delete-summary-cap-1");
    fireEvent.click(deleteBtn);

    // Confirmation dialog is shown
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(screen.getByText("Delete Past Summary?")).toBeInTheDocument();
      expect(screen.getByText(/Your original conversation history and messages will not be deleted/i)).toBeInTheDocument();
    });

    // Cancel first
    const cancelBtn = screen.getByRole("button", { name: /Cancel/i });
    fireEvent.click(cancelBtn);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Architecture Planning")).toBeInTheDocument();

    // Click delete again and confirm
    fireEvent.click(screen.getByTestId("delete-summary-cap-1"));
    const confirmBtn = screen.getByTestId("confirm-delete-summary-button");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(mockApi.deleteDiaryMemory).toHaveBeenCalledWith("cap-1");
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.queryByText("Architecture Planning")).toBeNull();
    });
  });

  it("handles deleting a summary that is currently open/expanded", async () => {
    render(<RaphaelChatPage api={mockApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getByText("What tasks are open?")).toBeInTheDocument();
    });

    // Open Past Summaries drawer
    const memoriesButton = screen.getByRole("button", { name: /Past Summaries/i });
    fireEvent.click(memoriesButton);

    await waitFor(() => {
      expect(screen.getByText("Architecture Planning")).toBeInTheDocument();
    });

    // Expand the summary
    const summaryTitleBtn = screen.getByTestId("toggle-summary-cap-1");
    fireEvent.click(summaryTitleBtn);

    // Markdown summary preview is rendered
    await waitFor(() => {
      expect(screen.getByText(/Decided to use PostgreSQL/i)).toBeInTheDocument();
    });

    // Delete while open
    fireEvent.click(screen.getByTestId("delete-summary-cap-1"));
    fireEvent.click(screen.getByTestId("confirm-delete-summary-button"));

    await waitFor(() => {
      expect(mockApi.deleteDiaryMemory).toHaveBeenCalledWith("cap-1");
      expect(screen.queryByText("Architecture Planning")).toBeNull();
      expect(screen.queryByText(/Decided to use PostgreSQL/i)).toBeNull();
    });
  });

  it("renders structured task creation card with title, project, due date, description, and open link", async () => {
    const rawTaskResponse =
      "I have created the task **Submit WWTP BOM** (ID: `task-999`).\n" +
      "- Due: 2026-10-23 18:00 UTC\n" +
      "- Project: WWTP\n" +
      "- Description: Soft deadline. Submit to Somya Sir.";

    const taskCreationApi: Partial<RaphaelApiClient> = {
      ...mockApi,
      getConversation: vi.fn().mockResolvedValue({
        id: "conv-wwtp",
        title: "WWTP Task Discussion",
        status: "active",
        created_at: "2026-10-19T10:00:00Z",
        updated_at: "2026-10-19T10:00:00Z",
        messages: [
          {
            id: "msg-1",
            conversation_id: "conv-wwtp",
            role: "user",
            content: "Add WWTP BOM to Blockday",
            created_at: "2026-10-19T10:00:00Z",
          },
          {
            id: "msg-2",
            conversation_id: "conv-wwtp",
            role: "assistant",
            content: rawTaskResponse,
            created_at: "2026-10-19T10:00:05Z",
          },
        ],
      }),
      listConversations: vi.fn().mockResolvedValue([
        {
          id: "conv-wwtp",
          title: "WWTP Task Discussion",
          status: "active",
          created_at: "2026-10-19T10:00:00Z",
          updated_at: "2026-10-19T10:00:00Z",
        },
      ]),
    };

    render(<RaphaelChatPage api={taskCreationApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getByTestId("task-creation-card")).toBeInTheDocument();
    });

    const taskCard = screen.getByTestId("task-creation-card");
    expect(within(taskCard).getByText("Submit WWTP BOM")).toBeInTheDocument();
    expect(within(taskCard).getByText("Soft deadline. Submit to Somya Sir.")).toBeInTheDocument();
    expect(within(taskCard).getByText("2026-10-23 18:00 UTC")).toBeInTheDocument();
    expect(within(taskCard).getByText("WWTP")).toBeInTheDocument();
    expect(within(taskCard).getByText("Open Tasks")).toBeInTheDocument();
    expect(within(taskCard).getByText("Task Created")).toBeInTheDocument();
  });
});

