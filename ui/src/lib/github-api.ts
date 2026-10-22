/**
 * GitHub integration and Project Repository API client.
 */

import { getCognitoAccessToken } from "./auth";

export interface GitHubAuthStatus {
  configured?: boolean;
  connected: boolean;
  username: string | null;
  scopes?: string[] | null;
  updated_at?: string | null;
  token_expired?: boolean | null;
  mode?: string;
  public_only?: boolean;
}

export interface GitHubAuthorizeResponse {
  authorization_url: string;
  state: string;
}

export interface GitHubRepositoryItem {
  id: string;
  name: string;
  full_name: string;
  owner: string;
  description: string;
  html_url: string;
  default_branch: string;
  updated_at?: string | null;
  private: boolean;
}

export interface ProjectRepositoryContext {
  repository: {
    id: string;
    full_name: string;
    name: string;
    owner: string;
    html_url: string;
    default_branch: string;
    last_synced_at?: string | null;
  };
  project_brief?: {
    purpose?: string;
    architecture_or_stack?: string[];
    setup?: string | null;
    key_capabilities?: string[];
    limitations?: string[];
  };
  milestones: Array<{
    id: string;
    number: number;
    title: string;
    description?: string | null;
    state: string;
    open_issues: number;
    closed_issues: number;
    due_on?: string | null;
  }>;
  issues: Array<{
    id: string;
    number: number;
    title: string;
    state: string;
    labels: string[];
    created_at?: string | null;
    updated_at?: string | null;
  }>;
  commits: Array<{
    sha: string;
    full_sha: string;
    message: string;
    author: string;
    date?: string | null;
  }>;
}

export class GitHubApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "GitHubApiError";
    this.status = status;
  }
}

export class GitHubApiClient {
  private baseUrl: string;

  constructor(baseUrl?: string) {
    this.baseUrl = baseUrl || (typeof window !== "undefined" && window.location?.origin
      ? "/api/v1"
      : "http://localhost:8000/api/v1");
  }

  private getHeaders(): HeadersInit {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    const token = getCognitoAccessToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }

  private async handleResponse<T>(res: Response): Promise<T> {
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const json = await res.json();
        detail = json.detail || json.message || detail;
      } catch {
        const text = await res.text().catch(() => "");
        if (text) detail = text;
      }
      throw new GitHubApiError(res.status, detail);
    }
    return (await res.json()) as T;
  }

  async getAuthStatus(): Promise<GitHubAuthStatus> {
    const res = await fetch(`${this.baseUrl}/github/status`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse<GitHubAuthStatus>(res);
  }

  async getAuthorizeUrl(params?: { redirectUri?: string; returnTo?: string }): Promise<GitHubAuthorizeResponse> {
    const url = new URL(
      `${this.baseUrl}/github/authorize`,
      typeof window !== "undefined" && window.location?.origin ? window.location.origin : "http://localhost:8000",
    );
    if (params?.redirectUri) url.searchParams.set("redirect_uri", params.redirectUri);
    if (params?.returnTo) url.searchParams.set("return_to", params.returnTo);
    const res = await fetch(url.toString(), {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse<GitHubAuthorizeResponse>(res);
  }

  async completeCallback(code: string, state: string, redirectUri?: string): Promise<GitHubAuthStatus> {
    const res = await fetch(`${this.baseUrl}/github/callback`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify({ code, state, redirect_uri: redirectUri }),
    });
    return this.handleResponse<GitHubAuthStatus>(res);
  }

  async connectAccount(token: string, scopes?: string): Promise<GitHubAuthStatus> {
    const res = await fetch(`${this.baseUrl}/github/connect`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify({ token, scopes }),
    });
    return this.handleResponse<GitHubAuthStatus>(res);
  }

  async disconnectAccount(): Promise<{ connected: boolean }> {
    const res = await fetch(`${this.baseUrl}/github/disconnect`, {
      method: "POST",
      headers: this.getHeaders(),
    });
    return this.handleResponse<{ connected: boolean }>(res);
  }

  async listRepositories(query?: string): Promise<GitHubRepositoryItem[]> {
    const url = new URL(`${this.baseUrl}/github/repositories`, window.location?.origin || "http://localhost:8000");
    if (query) {
      url.searchParams.set("q", query);
    }
    const res = await fetch(url.toString(), {
      method: "GET",
      headers: this.getHeaders(),
    });
    return this.handleResponse<GitHubRepositoryItem[]>(res);
  }

  async getProjectRepository(projectId: string): Promise<ProjectRepositoryContext | null> {
    const res = await fetch(`${this.baseUrl}/projects/${encodeURIComponent(projectId)}/repository`, {
      method: "GET",
      headers: this.getHeaders(),
    });
    if (res.status === 404) {
      return null;
    }
    return this.handleResponse<ProjectRepositoryContext>(res);
  }

  async associateProjectRepository(projectId: string, repository: string): Promise<ProjectRepositoryContext> {
    const res = await fetch(`${this.baseUrl}/projects/${encodeURIComponent(projectId)}/repository`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify({ repository }),
    });
    return this.handleResponse<ProjectRepositoryContext>(res);
  }

  async syncProjectRepository(projectId: string): Promise<ProjectRepositoryContext> {
    const res = await fetch(`${this.baseUrl}/projects/${encodeURIComponent(projectId)}/repository/sync`, {
      method: "POST",
      headers: this.getHeaders(),
    });
    return this.handleResponse<ProjectRepositoryContext>(res);
  }

  async disconnectProjectRepository(projectId: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/projects/${encodeURIComponent(projectId)}/repository`, {
      method: "DELETE",
      headers: this.getHeaders(),
    });
    if (!res.ok && res.status !== 404) {
      throw new GitHubApiError(res.status, `Failed to disconnect repository (${res.status})`);
    }
  }
}

export const gitHubApi = new GitHubApiClient();
