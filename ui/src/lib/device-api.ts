import { getCognitoAccessToken } from "./auth";
import type {
  BackendDevice,
  DeviceListResponse,
  DeviceOperation,
  DeviceOperationListResponse,
  DeviceOperationRequest,
} from "./device-types";

export class DeviceApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "DeviceApiError";
  }
}

export class DeviceApiClient {
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
      throw new DeviceApiError(0, `Network failure: ${String(error)}`);
    }

    if (response.status === 204) return undefined as T;

    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const problem = (await response.json()) as { detail?: string; title?: string };
        message = problem.detail || problem.title || message;
      } catch {
        // Fallback to generic HTTP status code
      }
      throw new DeviceApiError(response.status, message);
    }
    return (await response.json()) as T;
  }

  listDevices(): Promise<DeviceListResponse> {
    return this.request("/devices");
  }

  getDevice(deviceId: string): Promise<BackendDevice> {
    return this.request(`/devices/${encodeURIComponent(deviceId)}`);
  }

  revokeDevice(deviceId: string): Promise<void> {
    return this.request(`/devices/${encodeURIComponent(deviceId)}/revoke`, {
      method: "POST",
    });
  }

  executeOperation(
    deviceId: string,
    payload: DeviceOperationRequest,
  ): Promise<DeviceOperation> {
    return this.request(`/devices/${encodeURIComponent(deviceId)}/operations`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  listOperations(deviceId: string): Promise<DeviceOperationListResponse> {
    return this.request(`/devices/${encodeURIComponent(deviceId)}/operations`);
  }

  getOperation(deviceId: string, operationId: string): Promise<DeviceOperation> {
    return this.request(
      `/devices/${encodeURIComponent(deviceId)}/operations/${encodeURIComponent(operationId)}`,
    );
  }

  retryOperation(
    deviceId: string,
    operationId: string,
    force = false,
  ): Promise<DeviceOperation> {
    const query = force ? "?force=true" : "";
    return this.request(
      `/devices/${encodeURIComponent(deviceId)}/operations/${encodeURIComponent(operationId)}/retry${query}`,
      { method: "POST" },
    );
  }
}

export const deviceApiClient = new DeviceApiClient();
