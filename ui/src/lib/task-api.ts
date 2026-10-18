/**
 * Task API client conforming to the Personal OS API contract.
 * Authority: API_CONTRACT.md and src/personal_os/api/routes/tasks.py
 */

import type {
  BackendTask,
  ProblemDetail,
  TaskCreatePayload,
  TaskListResponse,
  TaskUpdatePayload,
} from "./task-types";

export class TaskApiError extends Error {
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
    this.name = "TaskApiError";
    this.status = status;
    this.problem = problem;
  }
}

export class TaskConflictError extends TaskApiError {
  currentRevision: number | undefined;
  serverState: Record<string, unknown> | undefined;

  constructor(message: string, problem?: ProblemDetail | undefined) {
    super(409, message, problem);
    this.name = "TaskConflictError";
    this.currentRevision = problem?.extensions?.current_revision;
    this.serverState = problem?.extensions?.server_state;
  }
}

export interface TaskApiConfig {
  baseUrl?: string;
  getAuthToken?: () => string | null;
}

const DEFAULT_BASE_URL = typeof window !== "undefined" && window.location?.origin
  ? "/api/v1"
  : "http://localhost:8000/api/v1";

export class TaskApiClient {
  private baseUrl: string;
  private getAuthToken: () => string | null;

  constructor(config?: TaskApiConfig) {
    this.baseUrl = config?.baseUrl || DEFAULT_BASE_URL;
    this.getAuthToken = config?.getAuthToken || (() => "dev-user");
  }

  private getHeaders(expectedRevision?: number): HeadersInit {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };

    const token = this.getAuthToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    if (expectedRevision !== undefined && expectedRevision !== null) {
      headers["If-Match"] = String(expectedRevision);
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

      if (res.status === 409) {
        throw new TaskConflictError(message, problem);
      }

      throw new TaskApiError(res.status, message, problem);
    }

    return (await res.json()) as T;
  }

  /**
   * List tasks with optional project, status, limit, offset filters.
   */
  async listTasks(params?: {
    project_id?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }): Promise<TaskListResponse> {
    const query = new URLSearchParams();
    if (params?.project_id) query.set("project_id", params.project_id);
    if (params?.status) query.set("status", params.status);
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.offset) query.set("offset", String(params.offset));

    const qs = query.toString();
    const url = `${this.baseUrl}/tasks${qs ? `?${qs}` : ""}`;

    const res = await fetch(url, {
      method: "GET",
      headers: this.getHeaders(),
    });

    return this.handleResponse<TaskListResponse>(res);
  }

  /**
   * Get single task by ID.
   */
  async getTask(taskId: string): Promise<BackendTask> {
    const res = await fetch(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}`, {
      method: "GET",
      headers: this.getHeaders(),
    });

    return this.handleResponse<BackendTask>(res);
  }

  /**
   * Create task.
   */
  async createTask(payload: TaskCreatePayload): Promise<BackendTask> {
    const res = await fetch(`${this.baseUrl}/tasks`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(payload),
    });

    return this.handleResponse<BackendTask>(res);
  }

  /**
   * Update task with optional optimistic lock (If-Match).
   */
  async updateTask(
    taskId: string,
    payload: TaskUpdatePayload,
    expectedRevision?: number,
  ): Promise<BackendTask> {
    const res = await fetch(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}`, {
      method: "PATCH",
      headers: this.getHeaders(expectedRevision),
      body: JSON.stringify(payload),
    });

    return this.handleResponse<BackendTask>(res);
  }

  /**
   * Complete task (POST /api/v1/tasks/{task_id}/complete).
   */
  async completeTask(taskId: string, expectedRevision?: number): Promise<BackendTask> {
    const res = await fetch(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/complete`, {
      method: "POST",
      headers: this.getHeaders(expectedRevision),
    });

    return this.handleResponse<BackendTask>(res);
  }

  /**
   * Reopen task (PATCH /api/v1/tasks/{task_id} with status="open").
   */
  async reopenTask(taskId: string, expectedRevision?: number): Promise<BackendTask> {
    return this.updateTask(taskId, { status: "open" }, expectedRevision);
  }

  /**
   * Soft-delete task.
   *
   * Backend concurrency limitation:
   * The backend endpoint DELETE /api/v1/tasks/{task_id} does not currently parse
   * or enforce the If-Match revision header. We forward expectedRevision in the
   * request headers for forward compatibility, but conditional deletion is not
   * guaranteed server-side in the current API implementation.
   */
  async deleteTask(taskId: string, expectedRevision?: number): Promise<void> {
    const res = await fetch(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}`, {
      method: "DELETE",
      headers: this.getHeaders(expectedRevision),
    });

    return this.handleResponse<void>(res);
  }
}

export const taskApi = new TaskApiClient();
