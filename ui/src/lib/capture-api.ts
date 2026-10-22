/**
 * Capture API client conforming to the Personal OS API contract.
 * Authority: API_CONTRACT.md and src/personal_os/api/routes/captures.py
 */

import type {
  BackendCapture,
  CaptureCreatePayload,
  CaptureListResponse,
  CaptureReceipt,
  ProcessingJobStatus,
} from "./capture-types";
import type { ProblemDetail } from "./task-types";
import { getCognitoAccessToken } from "./auth";

export class CaptureApiError extends Error {
  status: number;
  problem: ProblemDetail | undefined;

  constructor(status: number, message: string, problem?: ProblemDetail | undefined) {
    let formattedMessage = message;
    if (status === 401 && !message.toLowerCase().includes("unauthorized") && !message.includes("401")) {
      formattedMessage = `Unauthorized (401): ${message}`;
    } else if (status === 500 && !message.toLowerCase().includes("server error") && !message.includes("500")) {
      formattedMessage = `Server error (500): ${message}`;
    }
    super(formattedMessage);
    this.name = "CaptureApiError";
    this.status = status;
    this.problem = problem;
  }
}

export interface CaptureApiConfig {
  baseUrl?: string;
  getAuthToken?: () => string | null;
}

const DEFAULT_BASE_URL = typeof window !== "undefined" && window.location?.origin
  ? "/api/v1"
  : "http://localhost:8000/api/v1";

export class CaptureApiClient {
  private baseUrl: string;
  private getAuthToken: () => string | null;

  constructor(config?: CaptureApiConfig) {
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
    if (res.status === 204) {
      return undefined as unknown as T;
    }

    if (!res.ok) {
      let problem: ProblemDetail | undefined;
      let text = "";
      try {
        problem = (await res.json()) as ProblemDetail;
      } catch {
        text = await res.text().catch(() => "");
      }

      const message = problem?.detail || problem?.title || text || `HTTP error ${res.status}`;
      throw new CaptureApiError(res.status, message, problem);
    }

    return (await res.json()) as T;
  }

  /**
   * Submit raw capture with persist-first durability.
   */
  async submitCapture(payload: CaptureCreatePayload): Promise<CaptureReceipt> {
    const headers = this.getHeaders() as Record<string, string>;
    if (payload.idempotency_key) {
      headers["Idempotency-Key"] = payload.idempotency_key;
    }

    const res = await fetch(`${this.baseUrl}/captures`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    return this.handleResponse<CaptureReceipt>(res);
  }

  /**
   * List captures with optional status filter and pagination.
   */
  async listCaptures(params?: {
    status?: string;
    limit?: number;
    offset?: number;
  }): Promise<CaptureListResponse> {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.offset) query.set("offset", String(params.offset));

    const qs = query.toString();
    const url = `${this.baseUrl}/captures${qs ? `?${qs}` : ""}`;

    const res = await fetch(url, {
      method: "GET",
      headers: this.getHeaders(),
    });

    return this.handleResponse<CaptureListResponse>(res);
  }

  /**
   * Get single capture by ID.
   */
  async getCapture(captureId: string): Promise<BackendCapture> {
    const res = await fetch(`${this.baseUrl}/captures/${encodeURIComponent(captureId)}`, {
      method: "GET",
      headers: this.getHeaders(),
    });

    return this.handleResponse<BackendCapture>(res);
  }

  /**
   * Get capture processing job status by capture ID.
   */
  async getProcessingStatus(captureId: string): Promise<ProcessingJobStatus> {
    const res = await fetch(`${this.baseUrl}/captures/${encodeURIComponent(captureId)}/status`, {
      method: "GET",
      headers: this.getHeaders(),
    });

    return this.handleResponse<ProcessingJobStatus>(res);
  }

  /**
   * Soft-delete capture.
   */
  async deleteCapture(captureId: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/captures/${encodeURIComponent(captureId)}`, {
      method: "DELETE",
      headers: this.getHeaders(),
    });

    return this.handleResponse<void>(res);
  }
}

export const captureApi = new CaptureApiClient();
