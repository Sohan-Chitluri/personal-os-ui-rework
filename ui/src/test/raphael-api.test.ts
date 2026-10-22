import { describe, it, expect, vi, beforeEach } from "vitest";
import { RaphaelApiClient, RaphaelApiError } from "../lib/raphael-api";

describe("RaphaelApiClient", () => {
  let mockFetch: ReturnType<typeof vi.fn>;
  let client: RaphaelApiClient;

  beforeEach(() => {
    mockFetch = vi.fn();
    global.fetch = mockFetch as unknown as typeof fetch;
    client = new RaphaelApiClient({
      baseUrl: "http://testserver/api/v1/raphael",
      getAuthToken: () => "mock-token-123",
    });
  });

  it("lists conversations with authorization headers", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [
        {
          id: "conv-1",
          title: "First Chat",
          status: "active",
          created_at: "2026-10-19T10:00:00Z",
          updated_at: "2026-10-19T10:00:00Z",
        },
      ],
    });

    const result = await client.listConversations();
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("conv-1");

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/raphael/conversations"),
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({
          Authorization: "Bearer mock-token-123",
        }),
      }),
    );
  });

  it("creates a new conversation", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: "conv-new",
        title: "New Chat",
        status: "active",
        created_at: "2026-10-19T10:00:00Z",
        updated_at: "2026-10-19T10:00:00Z",
        messages: [],
      }),
    });

    const result = await client.createConversation({ title: "New Chat" });
    expect(result.id).toBe("conv-new");
    expect(result.title).toBe("New Chat");
  });

  it("sends a message and receives assistant reply", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        user_message: {
          id: "m-user-1",
          conversation_id: "conv-1",
          role: "user",
          content: "Hello",
          client_message_id: "client-1",
          created_at: "2026-10-19T10:01:00Z",
        },
        assistant_message: {
          id: "m-asst-1",
          conversation_id: "conv-1",
          role: "assistant",
          content: "Hi there!",
          created_at: "2026-10-19T10:01:02Z",
        },
      }),
    });

    const response = await client.sendMessage("conv-1", {
      content: "Hello",
      client_message_id: "client-1",
    });

    expect(response.user_message.content).toBe("Hello");
    expect(response.assistant_message.content).toBe("Hi there!");
  });

  it("identifies 502/504 errors as retryable", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 504,
      json: async () => ({
        type: "https://api.pos-deku.com/errors/http_504",
        title: "HTTP Error",
        status: 504,
        detail: "Hermes agent timed out",
      }),
    });

    await expect(client.sendMessage("conv-1", { content: "Will timeout" })).rejects.toThrowError(
      RaphaelApiError,
    );

    try {
      await client.sendMessage("conv-1", { content: "Will timeout" });
    } catch (err) {
      if (err instanceof RaphaelApiError) {
        expect(err.status).toBe(504);
        expect(err.isRetryable).toBe(true);
      }
    }
  });

  it("deletes a session memory via DELETE /memories/{captureId}", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 204,
    });

    await client.deleteDiaryMemory("cap-test-123");

    expect(mockFetch).toHaveBeenCalledWith(
      "http://testserver/api/v1/raphael/memories/cap-test-123",
      expect.objectContaining({
        method: "DELETE",
        headers: expect.objectContaining({
          Authorization: "Bearer mock-token-123",
        }),
      }),
    );
  });
});

