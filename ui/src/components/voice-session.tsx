import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Loader2,
  Mic,
  MicOff,
  PhoneOff,
  Radio,
  RefreshCw,
  Volume2,
} from "lucide-react";
import {
  ConnectionState,
  Room,
  RoomEvent,
  Track,
  type RemoteTrack,
} from "livekit-client";
import { voiceApi, type VoiceApiClient } from "@/lib/voice-api";

export type VoiceState =
  | "idle"
  | "requesting_permission"
  | "connecting"
  | "listening"
  | "speaking"
  | "muted"
  | "error";

export interface VoiceSessionProps {
  conversationId?: string;
  api?: VoiceApiClient;
  onEnd?: () => void;
  onTurnPersisted?: () => void;
  autoStart?: boolean;
}

export function VoiceSession({
  conversationId,
  api = voiceApi,
  onEnd,
  onTurnPersisted,
  autoStart = true,
}: VoiceSessionProps) {
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [activeSpeaker, setActiveSpeaker] = useState<"user" | "agent" | null>(null);
  const [currentTranscript, setCurrentTranscript] = useState<string | null>(null);

  const roomRef = useRef<Room | null>(null);
  const audioContainerRef = useRef<HTMLDivElement>(null);
  const pollIntervalRef = useRef<number | null>(null);

  // Clean up audio tracks and disconnect room
  const cleanupRoom = useCallback(async () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    const room = roomRef.current;
    if (room) {
      try {
        room.removeAllListeners();
        await room.disconnect();
      } catch (err) {
        console.warn("Error disconnecting LiveKit room:", err);
      }
      roomRef.current = null;
    }
    if (audioContainerRef.current) {
      audioContainerRef.current.innerHTML = "";
    }
  }, []);

  // Connect to LiveKit Room and enable microphone
  const connectVoice = useCallback(async () => {
    await cleanupRoom();
    setErrorMessage(null);
    setCurrentTranscript(null);
    setVoiceState("requesting_permission");

    // 1. Request microphone permission explicitly
    try {
      if (typeof navigator !== "undefined" && navigator.mediaDevices?.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        // Stop initial test stream so LiveKit can take ownership
        stream.getTracks().forEach((t) => t.stop());
      }
    } catch (err: unknown) {
      const errName = err instanceof Error ? err.name : "";
      if (errName === "NotAllowedError" || errName === "PermissionDeniedError") {
        setVoiceState("error");
        setErrorMessage(
          "Microphone permission was denied. Please allow microphone access in your browser settings to speak with Raphael.",
        );
        return;
      } else if (errName === "NotFoundError" || errName === "DevicesNotFoundError") {
        setVoiceState("error");
        setErrorMessage("No microphone found on your system. Please connect an audio input device.");
        return;
      } else {
        setVoiceState("error");
        setErrorMessage(
          "Unable to access microphone: " + (err instanceof Error ? err.message : String(err)),
        );
        return;
      }
    }

    // 2. Fetch scoped LiveKit access token from Personal OS backend
    setVoiceState("connecting");
    let tokenData;
    try {
      tokenData = await api.getVoiceToken(conversationId);
    } catch (err: unknown) {
      setVoiceState("error");
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("503") || msg.includes("not configured")) {
        setErrorMessage(
          "LiveKit voice server is not configured in staging. Verify LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET.",
        );
      } else if (msg.includes("401")) {
        setErrorMessage("Authentication session expired. Please refresh the page to sign in again.");
      } else {
        setErrorMessage("Could not connect to voice service: " + msg);
      }
      return;
    }

    // 3. Initialize LiveKit Room instance
    const room = new Room({
      adaptiveStream: true,
      dynacast: true,
      audioCaptureDefaults: {
        autoGainControl: true,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });
    roomRef.current = room;

    // Handle remote audio subscription (agent speaking)
    room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
      if (track.kind === Track.Kind.Audio) {
        const element = track.attach();
        element.setAttribute("data-testid", "livekit-remote-audio");
        audioContainerRef.current?.appendChild(element);
      }
    });

    room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
      track.detach();
    });

    // Handle active speaker changes (natural turn detection / speaking status)
    room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
      const isAgentSpeaking = speakers.some((s) => s.identity !== room.localParticipant.identity);
      const isUserSpeaking = speakers.some((s) => s.identity === room.localParticipant.identity);

      if (isAgentSpeaking) {
        setActiveSpeaker("agent");
        setVoiceState("speaking");
      } else if (isUserSpeaking) {
        setActiveSpeaker("user");
        setVoiceState("listening");
      } else {
        setActiveSpeaker(null);
        setVoiceState(isMuted ? "muted" : "listening");
      }
    });

    // Connection state changes
    room.on(RoomEvent.ConnectionStateChanged, (state) => {
      if (state === ConnectionState.Disconnected) {
        setVoiceState("idle");
      } else if (state === ConnectionState.Reconnecting) {
        setVoiceState("connecting");
      } else if (state === ConnectionState.Connected) {
        setVoiceState(isMuted ? "muted" : "listening");
      }
    });

    // Handle transcription events from LiveKit if emitted
    // @ts-expect-error - Event type present in livekit-client 2.x
    room.on(RoomEvent.TranscriptionReceived, (segments) => {
      if (Array.isArray(segments) && segments.length > 0) {
        const text = segments.map((s) => s.text).join(" ");
        if (text) setCurrentTranscript(text);
      }
    });

    // 4. Connect and enable microphone track
    try {
      await room.connect(tokenData.livekit_url, tokenData.livekit_token);
      await room.localParticipant.setMicrophoneEnabled(true);
      setIsMuted(false);
      setVoiceState("listening");

      // Periodic check to sync newly persisted turns to main chat transcript
      if (onTurnPersisted) {
        pollIntervalRef.current = window.setInterval(() => {
          onTurnPersisted();
        }, 4000);
      }
    } catch (err: unknown) {
      console.error("LiveKit connection failure:", err);
      setVoiceState("error");
      setErrorMessage(
        "Could not establish WebRTC voice session with LiveKit server. Please verify network connectivity.",
      );
      await cleanupRoom();
    }
  }, [cleanupRoom, conversationId, api, isMuted, onTurnPersisted]);

  // Toggle microphone mute
  const toggleMute = useCallback(async () => {
    const room = roomRef.current;
    if (!room || !room.localParticipant) return;
    const nextMuted = !isMuted;
    try {
      await room.localParticipant.setMicrophoneEnabled(!nextMuted);
      setIsMuted(nextMuted);
      setVoiceState(nextMuted ? "muted" : "listening");
    } catch (err) {
      console.warn("Failed to toggle microphone state:", err);
    }
  }, [isMuted]);

  // End voice session
  const handleEndSession = useCallback(async () => {
    await cleanupRoom();
    setVoiceState("idle");
    if (onTurnPersisted) onTurnPersisted();
    if (onEnd) onEnd();
  }, [cleanupRoom, onEnd, onTurnPersisted]);

  useEffect(() => {
    if (autoStart) {
      void connectVoice();
    }
    return () => {
      void cleanupRoom();
    };
  }, [autoStart, connectVoice, cleanupRoom]);

  // Color & status badge mapping
  let statusBadge = (
    <span className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
      <Radio className="size-3 text-muted-foreground" />
      <span>Idle</span>
    </span>
  );

  if (voiceState === "requesting_permission") {
    statusBadge = (
      <span className="flex items-center gap-1.5 font-mono text-[10px] text-block-yellow animate-pulse">
        <Loader2 className="size-3 animate-spin" />
        <span>Requesting Microphone…</span>
      </span>
    );
  } else if (voiceState === "connecting") {
    statusBadge = (
      <span className="flex items-center gap-1.5 font-mono text-[10px] text-block-blue animate-pulse">
        <Loader2 className="size-3 animate-spin" />
        <span>Connecting to LiveKit Room…</span>
      </span>
    );
  } else if (voiceState === "speaking") {
    statusBadge = (
      <span className="flex items-center gap-1.5 font-mono text-[10px] text-block-blue font-bold">
        <span className="size-2 rounded-full bg-block-blue animate-ping" />
        <span>Raphael is Speaking…</span>
      </span>
    );
  } else if (voiceState === "listening") {
    statusBadge = (
      <span className="flex items-center gap-1.5 font-mono text-[10px] text-emerald-500 font-bold">
        <span className="size-2 rounded-full bg-emerald-500 animate-pulse" />
        <span>Listening (Barge-in Active)…</span>
      </span>
    );
  } else if (voiceState === "muted") {
    statusBadge = (
      <span className="flex items-center gap-1.5 font-mono text-[10px] text-amber-500 font-bold">
        <MicOff className="size-3 text-amber-500" />
        <span>Microphone Muted</span>
      </span>
    );
  }

  return (
    <section
      data-testid="voice-session-panel"
      aria-label="Voice Session with Raphael"
      className="border-b-2 border-grid bg-panel/75 p-3 flex flex-col gap-2 shrink-0 transition-all z-10"
    >
      {/* Hidden audio element container for remote agent speech */}
      <div ref={audioContainerRef} className="sr-only" aria-hidden="true" />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div
            className={`grid size-8 place-items-center border-2 border-grid transition-colors ${
              voiceState === "speaking"
                ? "bg-block-blue text-white"
                : voiceState === "listening"
                  ? "bg-emerald-600 text-white"
                  : voiceState === "muted"
                    ? "bg-amber-500 text-white"
                    : "bg-canvas text-foreground"
            }`}
          >
            {voiceState === "speaking" ? (
              <Volume2 className="size-4 animate-bounce" />
            ) : isMuted ? (
              <MicOff className="size-4" />
            ) : (
              <Mic className="size-4" />
            )}
          </div>

          <div className="flex flex-col">
            <div className="flex items-center gap-2">
              <span className="font-display text-xs uppercase font-bold tracking-wider">
                Raphael Real-Time Voice
              </span>
              <span className="border border-grid/50 bg-canvas px-1.5 py-0.2 font-mono text-[9px] text-muted-foreground uppercase">
                WebRTC
              </span>
            </div>
            {statusBadge}
          </div>
        </div>

        {/* Voice Session Controls */}
        <div className="flex items-center gap-2">
          {voiceState !== "error" && voiceState !== "requesting_permission" && (
            <button
              type="button"
              data-testid="voice-mute-button"
              onClick={() => void toggleMute()}
              disabled={voiceState === "connecting"}
              className={`flex items-center gap-1.5 border-2 border-grid px-2.5 py-1 text-xs font-mono font-bold uppercase transition-colors disabled:opacity-50 ${
                isMuted
                  ? "bg-block-red text-white"
                  : "bg-canvas text-foreground hover:bg-muted"
              }`}
              title={isMuted ? "Unmute microphone" : "Mute microphone"}
            >
              {isMuted ? <MicOff className="size-3.5" /> : <Mic className="size-3.5" />}
              <span>{isMuted ? "Unmute" : "Mute"}</span>
            </button>
          )}

          <button
            type="button"
            data-testid="voice-reconnect-button"
            onClick={() => void connectVoice()}
            className="flex items-center gap-1.5 border-2 border-grid bg-canvas px-2.5 py-1 text-xs font-mono font-bold uppercase text-foreground hover:bg-muted transition-colors"
            title="Reconnect voice session"
          >
            <RefreshCw className="size-3.5" />
            <span>Reconnect</span>
          </button>

          <button
            type="button"
            data-testid="voice-end-button"
            onClick={() => void handleEndSession()}
            className="flex items-center gap-1.5 border-2 border-grid bg-block-red px-2.5 py-1 text-xs font-mono font-bold uppercase text-white hover:opacity-90 transition-opacity"
            title="End voice session and return to text chat"
          >
            <PhoneOff className="size-3.5" />
            <span>End Call</span>
          </button>
        </div>
      </div>

      {/* Live Recognized Speech Transcript Indicator */}
      {currentTranscript && voiceState !== "error" && (
        <div className="mt-1 border border-grid/40 bg-canvas/80 px-2.5 py-1 text-[11px] font-mono text-muted-foreground flex items-center gap-2">
          <span className="font-bold text-foreground">Heard:</span>
          <span className="italic truncate">"{currentTranscript}"</span>
        </div>
      )}

      {/* Actionable Error Banner */}
      {errorMessage && (
        <div
          role="alert"
          data-testid="voice-error-banner"
          className="mt-1 flex flex-wrap items-center justify-between gap-2 border-2 border-block-red bg-block-red/10 p-2.5 text-xs text-block-red"
        >
          <div className="flex items-center gap-2">
            <AlertCircle className="size-4 shrink-0" />
            <span>{errorMessage}</span>
          </div>
          <button
            type="button"
            onClick={() => void connectVoice()}
            className="flex items-center gap-1 border border-block-red bg-canvas px-2 py-0.5 font-mono text-[10px] font-bold uppercase text-foreground hover:bg-muted"
          >
            <RefreshCw className="size-3" />
            <span>Try Again</span>
          </button>
        </div>
      )}
    </section>
  );
}
