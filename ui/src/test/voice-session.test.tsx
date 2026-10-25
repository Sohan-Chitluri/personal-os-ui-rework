import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceSession } from "@/components/voice-session";
import type { VoiceApiClient, VoiceTokenResponse } from "@/lib/voice-api";
import { RaphaelChatPage } from "@/routes/raphael";
import type { RaphaelApiClient } from "@/lib/raphael-api";

// Mock livekit-client Room implementation for jsdom environment
const mockLocalParticipant = {
  identity: "user-test-1",
  setMicrophoneEnabled: vi.fn().mockResolvedValue(undefined),
};

const mockListeners: Record<string, ((...args: any[]) => void)[]> = {};

vi.mock("livekit-client", () => {
  class MockRoom {
    localParticipant = mockLocalParticipant;
    on = vi.fn().mockImplementation((event: string, callback: (...args: any[]) => void) => {
      mockListeners[event] = mockListeners[event] || [];
      mockListeners[event]!.push(callback);
    });
    removeAllListeners = vi.fn();
    connect = vi.fn().mockResolvedValue(undefined);
    disconnect = vi.fn().mockResolvedValue(undefined);
  }

  return {
    Room: MockRoom,
    RoomEvent: {
      TrackSubscribed: "trackSubscribed",
      TrackUnsubscribed: "trackUnsubscribed",
      ActiveSpeakersChanged: "activeSpeakersChanged",
      ConnectionStateChanged: "connectionStateChanged",
      TranscriptionReceived: "transcriptionReceived",
    },
    Track: {
      Kind: { Audio: "audio", Video: "video" },
    },
    ConnectionState: {
      Connected: "connected",
      Connecting: "connecting",
      Disconnected: "disconnected",
      Reconnecting: "reconnecting",
    },
  };
});

describe("VoiceSession Component", () => {
  const mockTokenData: VoiceTokenResponse = {
    livekit_url: "wss://livekit.test",
    livekit_token: "jwt-token-123",
    conversation_id: "conv-100",
    room_name: "pos-voice-conv-100",
    expires_in: 900,
  };

  const mockApi: Partial<VoiceApiClient> = {
    getVoiceToken: vi.fn().mockResolvedValue(mockTokenData),
  };

  const originalGetUserMedia = navigator.mediaDevices?.getUserMedia;

  beforeEach(() => {
    vi.clearAllMocks();
    Object.keys(mockListeners).forEach((k) => delete mockListeners[k]);

    // Mock getUserMedia success by default
    const mockTrack = { stop: vi.fn() };
    const mockStream = { getTracks: () => [mockTrack] };
    Object.defineProperty(navigator, "mediaDevices", {
      writable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue(mockStream),
      },
    });
  });

  afterEach(() => {
    if (originalGetUserMedia) {
      navigator.mediaDevices.getUserMedia = originalGetUserMedia;
    }
  });

  it("requests microphone permission, connects to LiveKit, and enters listening state", async () => {
    render(
      <VoiceSession
        conversationId="conv-100"
        api={mockApi as VoiceApiClient}
        autoStart={true}
      />
    );

    await waitFor(() => {
      expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalled();
      expect(mockApi.getVoiceToken).toHaveBeenCalledWith("conv-100");
    });

    await waitFor(() => {
      expect(screen.getByText(/Listening \(Barge-in Active\)/i)).toBeInTheDocument();
    });

    expect(mockLocalParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(true);
    expect(screen.getByTestId("voice-mute-button")).toBeInTheDocument();
    expect(screen.getByTestId("voice-end-button")).toBeInTheDocument();
  });

  it("handles denied microphone permission with actionable error message", async () => {
    const permError = new Error("Permission denied");
    permError.name = "NotAllowedError";
    navigator.mediaDevices.getUserMedia = vi.fn().mockRejectedValue(permError);

    render(
      <VoiceSession
        conversationId="conv-100"
        api={mockApi as VoiceApiClient}
        autoStart={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(
        screen.getByText(/Microphone permission was denied/i)
      ).toBeInTheDocument();
    });
  });

  it("toggles mute state when clicking Mute button", async () => {
    render(
      <VoiceSession
        conversationId="conv-100"
        api={mockApi as VoiceApiClient}
        autoStart={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Listening \(Barge-in Active\)/i)).toBeInTheDocument();
    });

    const muteBtn = screen.getByTestId("voice-mute-button");
    fireEvent.click(muteBtn);

    await waitFor(() => {
      expect(mockLocalParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(false);
      expect(screen.getByText(/Microphone Muted/i)).toBeInTheDocument();
      expect(screen.getByText(/Unmute/i)).toBeInTheDocument();
    });
  });

  it("disconnects room and invokes onEnd callback when ending call", async () => {
    const onEnd = vi.fn();
    render(
      <VoiceSession
        conversationId="conv-100"
        api={mockApi as VoiceApiClient}
        onEnd={onEnd}
        autoStart={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId("voice-end-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("voice-end-button"));

    await waitFor(() => {
      expect(onEnd).toHaveBeenCalledTimes(1);
    });
  });

  it("displays actionable error banner when backend token request fails", async () => {
    const failingApi: Partial<VoiceApiClient> = {
      getVoiceToken: vi.fn().mockRejectedValue(new Error("503 not configured")),
    };

    render(
      <VoiceSession
        conversationId="conv-100"
        api={failingApi as VoiceApiClient}
        autoStart={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(
        screen.getByText(/LiveKit voice server is not configured in staging/i)
      ).toBeInTheDocument();
      expect(screen.getByText(/Try Again/i)).toBeInTheDocument();
    });
  });
});

describe("RaphaelChatPage Voice Toggle Integration", () => {
  const mockChatApi: Partial<RaphaelApiClient> = {
    listConversations: vi.fn().mockResolvedValue([]),
    getActiveConversation: vi.fn().mockResolvedValue({
      id: "conv-active-1",
      title: "Active Chat",
      status: "active",
      created_at: "2026-10-19T10:00:00Z",
      updated_at: "2026-10-19T10:00:00Z",
      messages: [],
    }),
    getConversation: vi.fn().mockResolvedValue({
      id: "conv-active-1",
      title: "Active Chat",
      status: "active",
      created_at: "2026-10-19T10:00:00Z",
      updated_at: "2026-10-19T10:00:00Z",
      messages: [],
    }),
  };

  it("toggles voice session panel when clicking Voice button in header", async () => {
    render(<RaphaelChatPage api={mockChatApi as RaphaelApiClient} />);

    await waitFor(() => {
      expect(screen.getByTestId("raphael-voice-toggle")).toBeInTheDocument();
    });

    expect(screen.queryByTestId("voice-session-panel")).not.toBeInTheDocument();

    // Click to activate voice mode
    fireEvent.click(screen.getByTestId("raphael-voice-toggle"));

    await waitFor(() => {
      expect(screen.getByTestId("voice-session-panel")).toBeInTheDocument();
    });

    // Click again to deactivate
    fireEvent.click(screen.getByTestId("raphael-voice-toggle"));

    await waitFor(() => {
      expect(screen.queryByTestId("voice-session-panel")).not.toBeInTheDocument();
    });
  });
});
