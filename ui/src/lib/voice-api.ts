/**
 * API client for LiveKit Voice token vending and health checks.
 */

import { getCognitoAccessToken } from "./auth";

export interface VoiceTokenResponse {
  livekit_url: string;
  livekit_token: string;
  conversation_id: string;
  room_name: string;
  expires_in: number;
}

export interface VoiceHealthResponse {
  status: string;
  livekit_configured: boolean;
  environment: string;
}

export class VoiceApiError extends Error {
  status: number;
  isRetryable: boolean;

  constructor(status: number, message: string) {
    super(message);
    this.name = "VoiceApiError";
    this.status = status;
    this.isRetryable = status === 502 || status === 503 || status === 504 || status === 0;
  }
}

export interface VoiceApiConfig {
  baseUrl?: string;
  getAuthToken?: () => string | null;
}

const DEFAULT_BASE_URL =
  typeof window !== "undefined" && window.location?.origin
    ? "/api/v1/voice"
    : "http://localhost:8000/api/v1/voice";

export class VoiceApiClient {
  private baseUrl: string;
  private getAuthToken: () => string | null;

  constructor(config?: VoiceApiConfig) {
    this.baseUrl = config?.baseUrl || DEFAULT_BASE_URL;
    this.getAuthToken = config?.getAuthToken || getCognitoAccessToken;
  }

  private getHeaders(): HeadersInit {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    const token = this.getAuthToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }

  private async handleResponse<T>(res: Response): Promise<T> {
    if (!res.ok) {
      let detail = `Voice API error (${res.status})`;
      try {
        const body = await res.json();
        if (body && typeof body.detail === "string") {
          detail = body.detail;
        } else if (body && typeof body.title === "string") {
          detail = body.title;
        }
      } catch {
        const text = await res.text().catch(() => "");
        if (text) detail = text;
      }
      throw new VoiceApiError(res.status, detail);
    }
    return res.json() as Promise<T>;
  }

  async getVoiceToken(conversationId?: string): Promise<VoiceTokenResponse> {
    const payload = conversationId ? { conversation_id: conversationId } : {};
    const res = await fetch(`${this.baseUrl}/token`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(payload),
    });
    return this.handleResponse<VoiceTokenResponse>(res);
  }

  async getHealth(): Promise<VoiceHealthResponse> {
    const res = await fetch(`${this.baseUrl}/health`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse<VoiceHealthResponse>(res);
  }
}

export const voiceApi = new VoiceApiClient();
