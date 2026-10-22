import { getCognitoAccessToken, handleUnauthorized } from "./auth";
import type {
  BackendProject,
  ProjectCreatePayload,
  ProjectListResponse,
  ProjectUpdatePayload,
} from "./project-types";

export class ProjectApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ProjectApiError";
  }
}

export class ProjectApiClient {
  constructor(
    private readonly baseUrl = "/api/v1",
    private readonly getAuthToken: () => string | null = getCognitoAccessToken,
  ) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    const token = this.getAuthToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    } catch (error) {
      throw new ProjectApiError(0, `Network failure: ${String(error)}`);
    }
    if (response.status === 204) return undefined as T;
    if (!response.ok) {
      if (response.status === 401) {
        handleUnauthorized();
      }
      let message = `HTTP ${response.status}`;
      try {
        const problem = (await response.json()) as { detail?: string; title?: string };
        message = problem.detail || problem.title || message;
      } catch {
        // Keep the HTTP status when the server did not return problem JSON.
      }
      throw new ProjectApiError(response.status, message);
    }
    return (await response.json()) as T;
  }

  listProjects(): Promise<ProjectListResponse> {
    return this.request("/projects");
  }

  getProject(id: string): Promise<BackendProject> {
    return this.request(`/projects/${encodeURIComponent(id)}`);
  }

  createProject(payload: ProjectCreatePayload): Promise<BackendProject> {
    return this.request("/projects", { method: "POST", body: JSON.stringify(payload) });
  }

  updateProject(id: string, payload: ProjectUpdatePayload, revision?: number): Promise<BackendProject> {
    const headers = new Headers();
    if (revision !== undefined) headers.set("If-Match", String(revision));
    return this.request(`/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });
  }

  deleteProject(id: string): Promise<void> {
    return this.request(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
  }
}

export const projectApi = new ProjectApiClient();
