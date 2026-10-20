import { getCognitoAccessToken } from "./auth";
import {
  SYNC_PROTOCOL_VERSION,
  type OperationAcknowledgement,
  type SyncOperationEnvelope,
} from "./offline-queue";

export type SyncEntitySnapshot = {
  entity_type: "task" | "project" | "project_document";
  entity_id: string;
  revision: number;
  data: Record<string, unknown> | null;
  deleted: boolean;
};

export type SyncTombstone = {
  entity_type: SyncEntitySnapshot["entity_type"];
  entity_id: string;
  revision: number;
  sequence: number;
  deleted_at: string;
};

export type SyncChangeItem = {
  event_id: string;
  sequence: number;
  entity_type: SyncEntitySnapshot["entity_type"];
  entity_id: string;
  revision: number;
  snapshot: SyncEntitySnapshot | null;
  tombstone: SyncTombstone | null;
};

export type SyncChangePage = {
  protocol_version: 1;
  items: SyncChangeItem[];
  next_cursor: string;
  cursor_expires_at: string;
  has_more: boolean;
};

export type SyncConflict = {
  conflict_id: string;
  operation: SyncOperationEnvelope;
  base_revision: number | null;
  base_snapshot: SyncEntitySnapshot | null;
  local_payload: Record<string, unknown> | null;
  remote_snapshot: SyncEntitySnapshot;
  reason: string;
  status: "unresolved" | "resolved";
  created_at: string;
  resolution_operation_id: string | null;
};

export type SyncUploadResponse = {
  protocol_version: 1;
  acknowledgements: OperationAcknowledgement[];
};

export class SyncApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = "SyncApiError";
  }
}

export interface SyncApi {
  uploadOperations(operations: SyncOperationEnvelope[]): Promise<SyncUploadResponse>;
  getChanges(cursor: string | null, limit?: number): Promise<SyncChangePage>;
  getConflict(conflictId: string): Promise<SyncConflict>;
}

export class SyncApiClient implements SyncApi {
  constructor(
    private readonly baseUrl = "/api/v1",
    private readonly getAuthToken: () => string | null = getCognitoAccessToken,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  uploadOperations(operations: SyncOperationEnvelope[]): Promise<SyncUploadResponse> {
    if (operations.length < 1 || operations.length > 100) {
      throw new RangeError("sync upload batches must contain between 1 and 100 operations");
    }
    return this.request("/sync/operations", {
      method: "POST",
      body: JSON.stringify({ protocol_version: SYNC_PROTOCOL_VERSION, operations }),
    });
  }

  getChanges(cursor: string | null, limit = 100): Promise<SyncChangePage> {
    if (!Number.isInteger(limit) || limit < 1 || limit > 500) {
      throw new RangeError("sync change page limit must be between 1 and 500");
    }
    const query = new URLSearchParams({ limit: String(limit) });
    if (cursor !== null) query.set("cursor", cursor);
    return this.request(`/sync/changes?${query.toString()}`);
  }

  getConflict(conflictId: string): Promise<SyncConflict> {
    return this.request(`/sync/conflicts/${encodeURIComponent(conflictId)}`);
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = this.getAuthToken();
    if (!token) throw new SyncApiError(401, "Sign in before synchronizing.", "unauthorized");
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("Authorization", `Bearer ${token}`);
    if (init.body) headers.set("Content-Type", "application/json");
    let response: Response;
    const executeFetch = this.fetchImpl.bind(globalThis);
    try {
      response = await executeFetch(`${this.baseUrl}${path}`, { ...init, headers });
    } catch (error) {
      throw new SyncApiError(0, `Network failure: ${String(error)}`, "transport");
    }
    if (!response.ok) {
      let message = `Synchronization request failed (HTTP ${response.status}).`;
      let code: string | null = null;
      try {
        const problem = (await response.json()) as {
          detail?: string;
          title?: string;
          extensions?: { code?: string };
        };
        message = problem.detail || problem.title || message;
        code = problem.extensions?.code ?? null;
      } catch {
        // Keep a useful status message for non-problem responses.
      }
      throw new SyncApiError(response.status, message, code);
    }
    return (await response.json()) as T;
  }
}

export const syncApi = new SyncApiClient();
