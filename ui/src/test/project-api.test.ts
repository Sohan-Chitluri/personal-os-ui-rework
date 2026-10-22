import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectApiClient } from "@/lib/project-api";

afterEach(() => vi.restoreAllMocks());

describe("project API client", () => {
  it("loads projects with the authenticated API contract", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0 })));
    const client = new ProjectApiClient("https://api.example.test/api/v1", () => "access-token");
    await expect(client.listProjects()).resolves.toEqual({ items: [], total: 0 });
    const [url, options] = fetchSpy.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/projects");
    const headers = new Headers(options?.headers);
    expect(headers.get("Authorization")).toBe("Bearer access-token");
    expect(headers.get("Accept")).toBe("application/json");
  });

  it("preserves authorization, validation, and conflict status details", async () => {
    const client = new ProjectApiClient("https://api.example.test/api/v1", () => null);
    for (const [status, detail] of [[401, "Sign in required"], [422, "Name is invalid"], [409, "Revision changed"]] as const) {
      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ detail }), { status }));
      await expect(client.createProject({ name: "Example" })).rejects.toMatchObject({ status, message: detail });
      vi.restoreAllMocks();
    }
  });

  it("reports network failures without converting them into empty project data", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("offline"));
    const client = new ProjectApiClient("https://api.example.test/api/v1");
    await expect(client.listProjects()).rejects.toMatchObject({ status: 0, message: expect.stringContaining("Network failure") });
  });
});
