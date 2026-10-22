/**
 * Capture types and contracts conforming to the Personal OS API contract.
 * Authority: API_CONTRACT.md and src/personal_os/api/schemas.py
 */

export interface BackendCapture {
  id: string;
  owner_id: string;
  project_id?: string | null;
  content: string;
  source: string;
  content_type: string;
  status: "pending" | "processing" | "processed" | "failed" | "dead" | string;
  idempotency_key?: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
  processing_status?: string | null;
  processing_job_id?: string | null;
  task_ids?: string[];
  extraction_error?: string | null;
}

export interface CaptureListResponse {
  items: BackendCapture[];
  total: number;
}

export interface CaptureCreatePayload {
  content: string;
  source?: "web" | "cli" | "voice" | "api" | string;
  content_type?: "text" | "audio" | string;
  project_id?: string | null;
  idempotency_key?: string | null;
}

export interface CaptureReceipt {
  capture_id: string;
  persisted: boolean;
  timestamp: string;
  status: string;
  error?: string | null;
}

export interface ProcessingJobStatus {
  id: string;
  capture_id: string;
  status: string;
  attempt: number;
  max_attempts: number;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  task_ids: string[];
  created_at: string;
  updated_at: string;
}
