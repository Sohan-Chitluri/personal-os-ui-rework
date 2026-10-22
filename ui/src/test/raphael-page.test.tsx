import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    expect(screen.getByText("CORDIC")).toBeInTheDocument();
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
});
