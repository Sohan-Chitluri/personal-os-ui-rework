import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { VoiceApiClient, VoiceApiError } from "@/lib/voice-api";

describe("VoiceApiClient", () => {
  const mockToken = "mock-cognito-access-token";
  let client: VoiceApiClient;
  const originalFetch = global.fetch;

  beforeEach(() => {
    client = new VoiceApiClient({
      baseUrl: "http://api.test/api/v1/voice",
      getAuthToken: () => mockToken,
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("fetches voice token for active conversation", async () => {
    const mockResponse = {
      livekit_url: "wss://livekit.test",
      livekit_token: "jwt-token-123",
      conversation_id: "conv-1",
      room_name: "pos-voice-conv-1",
      expires_in: 900,
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => mockResponse,
    } as Response);

    const result = await client.getVoiceToken();

    expect(global.fetch).toHaveBeenCalledWith("http://api.test/api/v1/voice/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${mockToken}`,
      },
      body: JSON.stringify({}),
    });
    expect(result).toEqual(mockResponse);
  });

  it("fetches voice token for specific conversation ID", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        livekit_url: "wss://livekit.test",
        livekit_token: "jwt-token-456",
        conversation_id: "conv-specific-42",
        room_name: "pos-voice-conv-specific-42",
        expires_in: 900,
      }),
    } as Response);

    const result = await client.getVoiceToken("conv-specific-42");

    expect(global.fetch).toHaveBeenCalledWith("http://api.test/api/v1/voice/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${mockToken}`,
      },
      body: JSON.stringify({ conversation_id: "conv-specific-42" }),
    });
    expect(result.conversation_id).toBe("conv-specific-42");
  });

  it("throws VoiceApiError on non-ok HTTP response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: "LiveKit server credentials are not configured" }),
    } as Response);

    await expect(client.getVoiceToken()).rejects.toThrow(VoiceApiError);
    await expect(client.getVoiceToken()).rejects.toMatchObject({
      status: 503,
      message: "LiveKit server credentials are not configured",
      isRetryable: true,
    });
  });

  it("fetches voice service health status", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        livekit_configured: true,
        environment: "staging",
      }),
    } as Response);

    const health = await client.getHealth();
    expect(health.status).toBe("ok");
    expect(health.livekit_configured).toBe(true);
    expect(health.environment).toBe("staging");
  });
});
