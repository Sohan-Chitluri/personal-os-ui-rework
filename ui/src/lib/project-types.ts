import type { EntitySyncStatus } from "./task-types";

export type BackendProject = {
  id: string;
  owner_id: string;
  name: string;
  description: string | null;
  status: string;
  revision: number;
  created_at: string;
  updated_at: string;
  syncStatus?: EntitySyncStatus | undefined;
  conflictId?: string | null | undefined;
};

export type ProjectListResponse = { items: BackendProject[]; total: number };
export type ProjectCreatePayload = { name: string; description?: string | null };
export type ProjectUpdatePayload = Partial<ProjectCreatePayload> & { status?: string };
