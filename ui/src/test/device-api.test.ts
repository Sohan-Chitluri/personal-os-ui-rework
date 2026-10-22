import { afterEach, describe, expect, it, vi } from "vitest";
import { DeviceApiClient } from "@/lib/device-api";

afterEach(() => vi.restoreAllMocks());

describe("device API client", () => {
  it("lists devices with authorization header", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }))
    );
    const client = new DeviceApiClient("https://api.example.test/api/v1", () => "access-token-123");

    await expect(client.listDevices()).resolves.toEqual({ items: [], total: 0 });

    const [url, options] = fetchSpy.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/devices");
    const headers = new Headers(options?.headers);
    expect(headers.get("Authorization")).toBe("Bearer access-token-123");
    expect(headers.get("Accept")).toBe("application/json");
  });

  it("revokes device via POST", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    const client = new DeviceApiClient("https://api.example.test/api/v1", () => "access-token-123");

    await expect(client.revokeDevice("dev_test_456")).resolves.toBeUndefined();

    const [url, options] = fetchSpy.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/devices/dev_test_456/revoke");
    expect(options?.method).toBe("POST");
  });

  it("executes capability operation on device", async () => {
    const opMock = {
      id: "op_01JABC",
      device_id: "dev_test_456",
      capability: "system.ping",
      status: "completed",
      result: { pong: true },
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(opMock), { status: 201 })
    );
    const client = new DeviceApiClient("https://api.example.test/api/v1", () => "access-token-123");

    const result = await client.executeOperation("dev_test_456", {
      capability: "system.ping",
      params: { echo: "hello" },
    });

    expect(result).toEqual(opMock);
    const [url, options] = fetchSpy.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/devices/dev_test_456/operations");
    expect(options?.method).toBe("POST");
    expect(JSON.parse(options?.body as string)).toEqual({
      capability: "system.ping",
      params: { echo: "hello" },
    });
  });

  it("handles errors and network failures properly", async () => {
    const client = new DeviceApiClient("https://api.example.test/api/v1", () => null);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Device offline" }), { status: 400 })
    );
    await expect(
      client.executeOperation("dev_offline", { capability: "system.ping" })
    ).rejects.toMatchObject({ status: 400, message: "Device offline" });

    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("Connection failed"));
    await expect(client.listDevices()).rejects.toMatchObject({
      status: 0,
      message: expect.stringContaining("Network failure"),
    });
  });

  it("retries operation via POST with optional force param", async () => {
    const opMock = {
      id: "op_01JABC",
      device_id: "dev_test_456",
      capability: "system.ping",
      status: "pending",
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify(opMock), { status: 200 }))
    );
    const client = new DeviceApiClient("https://api.example.test/api/v1", () => "access-token-123");

    const res1 = await client.retryOperation("dev_test_456", "op_01JABC");
    expect(res1).toEqual(opMock);
    expect(fetchSpy.mock.calls[0]![0]).toBe("https://api.example.test/api/v1/devices/dev_test_456/operations/op_01JABC/retry");

    await client.retryOperation("dev_test_456", "op_01JABC", true);
    expect(fetchSpy.mock.calls[1]![0]).toBe("https://api.example.test/api/v1/devices/dev_test_456/operations/op_01JABC/retry?force=true");
  });
});
