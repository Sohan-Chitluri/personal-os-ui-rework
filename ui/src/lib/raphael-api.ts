/**
 * API client for Raphael conversations and Hermes agent communication.
 */

import type {
  CreateConversationPayload,
  DiarySessionMemory,
  ProblemDetail,
  RaphaelConversationDetail,
  RaphaelConversationSummary,
  SendMessagePayload,
  SendMessageResponse,
} from "./raphael-types";
import { getCognitoAccessToken } from "./auth";

export class RaphaelApiError extends Error {
  status: number;
  problem: ProblemDetail | undefined;
  isRetryable: boolean;

  constructor(status: number, message: string, problem?: ProblemDetail) {
    super(message);
    this.name = "RaphaelApiError";
    this.status = status;
    this.problem = problem;
    // 502, 503, 504 and network errors are recoverable via retry
    this.isRetryable = status === 502 || status === 503 || status === 504 || status === 0;
  }
}

export interface RaphaelApiConfig {
  baseUrl?: string;
  getAuthToken?: () => string | null;
}

const DEFAULT_BASE_URL =
  typeof window !== "undefined" && window.location?.origin
    ? "/api/v1/raphael"
    : "http://localhost:8000/api/v1/raphael";

export class RaphaelApiClient {
  private baseUrl: string;
  private getAuthToken: () => string | null;

  constructor(config?: RaphaelApiConfig) {
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
      let problem: ProblemDetail | undefined;
      let text = "";
      try {
        problem = (await res.json()) as ProblemDetail;
      } catch {
        text = await res.text().catch(() => "");
      }
      const message =
        problem?.detail || problem?.title || text || `HTTP error ${res.status}`;
      throw new RaphaelApiError(res.status, message, problem);
    }
    return (await res.json()) as T;
  }

  async listConversations(limit: number = 50, offset: number = 0): Promise<RaphaelConversationSummary[]> {
    const url = new URL(`${this.baseUrl}/conversations`, window.location?.origin || "http://localhost:8000");
    url.searchParams.set("limit", String(limit));
    url.searchParams.set("offset", String(offset));

    const res = await fetch(url.toString(), {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse<RaphaelConversationSummary[]>(res);
  }

  async createConversation(payload?: CreateConversationPayload): Promise<RaphaelConversationDetail> {
    const res = await fetch(`${this.baseUrl}/conversations`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(payload || {}),
    });
    return this.handleResponse<RaphaelConversationDetail>(res);
  }

  async getConversation(conversationId: string): Promise<RaphaelConversationDetail> {
    const res = await fetch(`${this.baseUrl}/conversations/${encodeURIComponent(conversationId)}`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse<RaphaelConversationDetail>(res);
  }

  async getActiveConversation(): Promise<RaphaelConversationDetail> {
    const res = await fetch(`${this.baseUrl}/active`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse<RaphaelConversationDetail>(res);
  }

  async resetActiveConversation(): Promise<RaphaelConversationDetail> {
    const res = await fetch(`${this.baseUrl}/reset`, {
      method: "POST",
      headers: this.getHeaders(),
    });
    return this.handleResponse<RaphaelConversationDetail>(res);
  }

  async getDiaryMemories(query: string = "", limit: number = 5): Promise<DiarySessionMemory[]> {
    const url = new URL(`${this.baseUrl}/memories`, window.location?.origin || "http://localhost:8000");
    if (query) {
      url.searchParams.set("q", query);
    }
    url.searchParams.set("limit", String(limit));

    const res = await fetch(url.toString(), {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse<DiarySessionMemory[]>(res);
  }

  async deleteDiaryMemory(captureId: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/memories/${encodeURIComponent(captureId)}`, {
      method: "DELETE",
      headers: this.getHeaders(),
    });
    if (!res.ok) {
      let problem: ProblemDetail | undefined;
      let text = "";
      try {
        problem = (await res.json()) as ProblemDetail;
      } catch {
        text = await res.text().catch(() => "");
      }
      const message =
        problem?.detail || problem?.title || text || `HTTP error ${res.status}`;
      throw new RaphaelApiError(res.status, message, problem);
    }
  }

  async finalizeConversation(conversationId: string): Promise<{ conversation_id: string; summary_status: string }> {
    const res = await fetch(`${this.baseUrl}/conversations/${encodeURIComponent(conversationId)}/finalize`, {
      method: "POST",
      headers: this.getHeaders(),
    });
    return this.handleResponse<{ conversation_id: string; summary_status: string }>(res);
  }

  async sendMessage(
    conversationId: string,
    payload: SendMessagePayload,
  ): Promise<SendMessageResponse> {
    const res = await fetch(
      `${this.baseUrl}/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: "POST",
        headers: this.getHeaders(),
        body: JSON.stringify(payload),
      },
    );
    return this.handleResponse<SendMessageResponse>(res);
  }
}

export const raphaelApi = new RaphaelApiClient();
