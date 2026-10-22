export interface BackendDevice {
  id: string;
  device_id: string;
  owner_id: string;
  name: string;
  platform: string;
  capabilities: Record<string, any>;
  status: "online" | "offline" | "degraded" | "stale";
  last_seen_at: string | null;
  registered_at: string;
  revoked_at: string | null;
  revision: number;
}

export interface DeviceListResponse {
  items: BackendDevice[];
  total: number;
}

export interface DeviceOperation {
  id: string;
  owner_id: string;
  device_id: string;
  capability: string;
  params: Record<string, any>;
  status: "pending" | "running" | "completed" | "failed" | "timeout" | "cancelled" | "outcome_unknown";
  result: any | null;
  error: { code: number; message: string; data?: any } | null;
  idempotency_key: string | null;
  started_at: string | null;
  finished_at: string | null;
  timeout_seconds: number;
  created_at: string;
  updated_at: string;
  revision: number;
}

export interface DeviceOperationListResponse {
  items: DeviceOperation[];
  total: number;
}

export interface DeviceOperationRequest {
  capability: string;
  params?: Record<string, any>;
  idempotency_key?: string;
  timeout_seconds?: number;
}
