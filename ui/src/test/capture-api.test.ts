import { afterEach, describe, expect, it, vi } from "vitest";
import { CaptureApiClient, CaptureApiError } from "@/lib/capture-api";

afterEach(() => vi.restoreAllMocks());

describe("capture API client", () => {
  it("submits raw capture with idempotency key and auth headers", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          capture_id: "cap-123",
          persisted: true,
          timestamp: "2026-10-19T00:00:00Z",
          status: "pending",
          error: null,
        }),
        { status: 201 }
      )
    );

    const client = new CaptureApiClient({
      baseUrl: "https://api.example.test/api/v1",
      getAuthToken: () => "mock-jwt-token",
    });

    const receipt = await client.submitCapture({
      content: "Draft note for project",
      source: "web",
      project_id: "proj-1",
      idempotency_key: "idem-abc-123",
    });

    expect(receipt.capture_id).toBe("cap-123");
    expect(receipt.status).toBe("pending");
    expect(receipt.persisted).toBe(true);

    const [url, options] = fetchSpy.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/captures");
    expect(options?.method).toBe("POST");

    const headers = options?.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer mock-jwt-token");
    expect(headers["Idempotency-Key"]).toBe("idem-abc-123");
  });

  it("lists captures with status filter and pagination", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            {
              id: "cap-1",
              owner_id: "user-1",
              content: "Sample capture",
              source: "web",
              content_type: "text",
              status: "processed",
              revision: 1,
              created_at: "2026-10-19T00:00:00Z",
              updated_at: "2026-10-19T00:00:00Z",
              task_ids: ["task-1"],
            },
          ],
          total: 1,
        }),
        { status: 200 }
      )
    );

    const client = new CaptureApiClient({
      baseUrl: "https://api.example.test/api/v1",
      getAuthToken: () => null,
    });

    const res = await client.listCaptures({ status: "processed", limit: 50, offset: 10 });
    expect(res.total).toBe(1);
    expect(res.items[0]?.id).toBe("cap-1");

    const [url] = fetchSpy.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/captures?status=processed&limit=50&offset=10");
  });

  it("fetches processing job status", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "job-1",
          capture_id: "cap-1",
          status: "failed",
          attempt: 3,
          max_attempts: 3,
          error: "LLM extraction timeout",
          task_ids: [],
          created_at: "2026-10-19T00:00:00Z",
          updated_at: "2026-10-19T00:05:00Z",
        }),
        { status: 200 }
      )
    );

    const client = new CaptureApiClient({ baseUrl: "https://api.example.test/api/v1" });
    const status = await client.getProcessingStatus("cap-1");

    expect(status.status).toBe("failed");
    expect(status.error).toBe("LLM extraction timeout");
    expect(fetchSpy).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/captures/cap-1/status",
      expect.anything()
    );
  });

  it("handles 401 and 422 API errors with CaptureApiError", async () => {
    const client = new CaptureApiClient({ baseUrl: "https://api.example.test/api/v1" });

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Unauthorized credentials" }), { status: 401 })
    );

    await expect(client.submitCapture({ content: "test" })).rejects.toThrow(CaptureApiError);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Field required" }), { status: 422 })
    );

    await expect(client.submitCapture({ content: "" })).rejects.toThrow(CaptureApiError);
  });
});
