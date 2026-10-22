import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  Activity,
  Check,
  HardDrive,
  Laptop,
  RefreshCw,
  ShieldAlert,
  Terminal,
  X,
} from "lucide-react";
import { deviceApiClient, DeviceApiError } from "@/lib/device-api";
import type { BackendDevice, DeviceOperation } from "@/lib/device-types";

export const Route = createFileRoute("/devices")({
  head: () => ({ meta: [{ title: "Devices — Personal OS" }] }),
  component: DevicesPage,
});

export function DevicesPage() {
  const [devices, setDevices] = useState<BackendDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);

  // Operations state
  const [operations, setOperations] = useState<DeviceOperation[]>([]);
  const [executing, setExecuting] = useState(false);
  const [activeOp, setActiveOp] = useState<DeviceOperation | null>(null);

  const loadDevices = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await deviceApiClient.listDevices();
      setDevices(res.items);
      if (res.items.length > 0 && !selectedDeviceId) {
        setSelectedDeviceId(res.items[0]?.device_id ?? null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const loadOperations = async (deviceId: string) => {
    try {
      const res = await deviceApiClient.listOperations(deviceId);
      setOperations(res.items);
    } catch {
      // Non-blocking
    }
  };

  useEffect(() => {
    void loadDevices();
  }, []);

  useEffect(() => {
    if (selectedDeviceId) {
      void loadOperations(selectedDeviceId);
    }
  }, [selectedDeviceId]);

  const selectedDevice = devices.find((d) => d.device_id === selectedDeviceId);

  const handleRevoke = async (deviceId: string) => {
    if (
      !window.confirm(
        "Are you sure you want to revoke this companion device? It will be disconnected immediately.",
      )
    ) {
      return;
    }
    try {
      await deviceApiClient.revokeDevice(deviceId);
      await loadDevices();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleRunCapability = async (capability: string, params: Record<string, unknown> = {}) => {
    if (!selectedDeviceId) return;
    setExecuting(true);
    setError(null);
    try {
      const op = await deviceApiClient.executeOperation(selectedDeviceId, {
        capability,
        params,
        timeout_seconds: 15,
      });
      setActiveOp(op);
      await loadOperations(selectedDeviceId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setExecuting(false);
    }
  };

  const statusBadge = (status: BackendDevice["status"], isRevoked: boolean) => {
    if (isRevoked) {
      return (
        <span className="border border-red-700 bg-red-700/20 px-2 py-0.5 text-[10px] font-black uppercase tracking-wider text-red-500">
          Revoked
        </span>
      );
    }
    switch (status) {
      case "online":
        return (
          <span className="border border-emerald-600 bg-emerald-600/20 px-2 py-0.5 text-[10px] font-black uppercase tracking-wider text-emerald-600">
            Online
          </span>
        );
      case "degraded":
        return (
          <span className="border border-yellow-600 bg-yellow-600/20 px-2 py-0.5 text-[10px] font-black uppercase tracking-wider text-yellow-600">
            Degraded
          </span>
        );
      case "stale":
        return (
          <span className="border border-amber-600 bg-amber-600/20 px-2 py-0.5 text-[10px] font-black uppercase tracking-wider text-amber-600">
            Stale
          </span>
        );
      default:
        return (
          <span className="border border-grid/40 bg-muted px-2 py-0.5 text-[10px] font-black uppercase tracking-wider text-muted-foreground">
            Offline
          </span>
        );
    }
  };

  return (
    <main className="flex flex-col h-full min-h-0 min-w-0 overflow-y-auto bg-grid p-1 border-l-[5px] border-grid">
      <section className="bg-canvas p-5 min-h-full shrink-0">
        <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
          <div>
            <span className="section-label text-muted-foreground">Local Companion Control</span>
            <h1 className="mt-1 font-display text-4xl uppercase">Connected Devices</h1>
          </div>
          <button
            onClick={loadDevices}
            disabled={loading}
            className="flex items-center gap-1.5 border-2 border-grid bg-canvas px-3 py-2 text-xs font-black uppercase hover:bg-muted"
          >
            <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
        </header>

        {error && (
          <div
            role="alert"
            className="mb-4 flex items-center justify-between border-2 border-block-red bg-block-red/10 p-3 text-sm text-foreground"
          >
            <span>{error}</span>
            <button onClick={() => setError(null)}>
              <X className="size-4" />
            </button>
          </div>
        )}

        <div className="grid gap-3 lg:grid-cols-[300px_minmax(0,1fr)]">
          {/* Device list sidebar */}
          <div className="border-2 border-grid bg-canvas">
            <div className="border-b-2 border-grid bg-muted/40 p-3">
              <span className="text-xs font-black uppercase tracking-wider">
                Registered Machines
              </span>
            </div>
            {devices.length === 0 && !loading && (
              <div className="p-4 text-xs text-muted-foreground">
                No companion devices registered yet. Follow the pairing instructions below to
                connect your laptop.
              </div>
            )}
            <ul className="divide-y divide-grid/20">
              {devices.map((dev) => (
                <li key={dev.device_id}>
                  <button
                    onClick={() => setSelectedDeviceId(dev.device_id)}
                    className={`block w-full p-3 text-left transition-colors ${
                      selectedDeviceId === dev.device_id
                        ? "bg-muted font-bold"
                        : "hover:bg-muted/50"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-2 truncate text-sm">
                        <Laptop className="size-4 shrink-0 text-muted-foreground" />
                        {dev.name}
                      </span>
                      {statusBadge(dev.status, !!dev.revoked_at)}
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-muted-foreground truncate">
                      {dev.device_id} ({dev.platform})
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Device details & operations workspace */}
          <div className="space-y-4">
            {selectedDevice ? (
              <div className="border-2 border-grid bg-canvas p-5">
                <div className="flex flex-wrap items-start justify-between gap-2 border-b-2 border-grid pb-4">
                  <div>
                    <h2 className="font-display text-2xl uppercase">{selectedDevice.name}</h2>
                    <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                      ID: {selectedDevice.device_id}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {statusBadge(selectedDevice.status, !!selectedDevice.revoked_at)}
                    {!selectedDevice.revoked_at && (
                      <button
                        onClick={() => handleRevoke(selectedDevice.device_id)}
                        className="border border-block-red px-2.5 py-1 text-xs font-bold uppercase text-block-red hover:bg-block-red hover:text-white"
                      >
                        Revoke
                      </button>
                    )}
                  </div>
                </div>

                {/* Device Metadata */}
                <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-2 border-2 border-grid bg-muted/20 p-3 text-xs">
                  <div>
                    <span className="font-mono text-[10px] text-muted-foreground uppercase block">
                      Platform
                    </span>
                    <span className="font-bold">{selectedDevice.platform}</span>
                  </div>
                  <div>
                    <span className="font-mono text-[10px] text-muted-foreground uppercase block">
                      Revision
                    </span>
                    <span className="font-bold">{selectedDevice.revision}</span>
                  </div>
                  <div>
                    <span className="font-mono text-[10px] text-muted-foreground uppercase block">
                      Registered
                    </span>
                    <span className="truncate block font-mono text-[11px]">
                      {new Date(selectedDevice.registered_at).toLocaleDateString()}
                    </span>
                  </div>
                  <div>
                    <span className="font-mono text-[10px] text-muted-foreground uppercase block">
                      Last Seen
                    </span>
                    <span className="truncate block font-mono text-[11px]">
                      {selectedDevice.last_seen_at
                        ? new Date(selectedDevice.last_seen_at).toLocaleTimeString()
                        : "Never"}
                    </span>
                  </div>
                </div>

                {/* Capability Dispatch Actions */}
                {!selectedDevice.revoked_at && (
                  <div className="mt-5">
                    <h3 className="section-label mb-2">Execute Local Capability</h3>
                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={() => handleRunCapability("system.ping", { echo: "Web UI test" })}
                        disabled={executing || selectedDevice.status === "offline"}
                        className="flex items-center gap-1.5 border-2 border-grid bg-block-blue px-3 py-2 text-xs font-black uppercase text-on-primary hover:opacity-90 disabled:opacity-50"
                      >
                        <Activity className="size-3.5" /> Ping Diagnostic
                      </button>
                      <button
                        onClick={() => handleRunCapability("filesystem.discover")}
                        disabled={executing || selectedDevice.status === "offline"}
                        className="flex items-center gap-1.5 border-2 border-grid bg-block-yellow px-3 py-2 text-xs font-black uppercase text-block-ink hover:opacity-90 disabled:opacity-50"
                      >
                        <HardDrive className="size-3.5" /> Discover Projects
                      </button>
                      <button
                        onClick={() => handleRunCapability("process.list", { limit: 15 })}
                        disabled={executing || selectedDevice.status === "offline"}
                        className="flex items-center gap-1.5 border-2 border-grid bg-canvas px-3 py-2 text-xs font-black uppercase text-foreground hover:bg-muted disabled:opacity-50"
                      >
                        <Terminal className="size-3.5" /> Inspect Processes
                      </button>
                    </div>
                    {selectedDevice.status === "offline" && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        Device is currently offline. Start the companion daemon locally to enable
                        capability execution.
                      </p>
                    )}
                  </div>
                )}

                {/* Active/Latest Operation Result */}
                {activeOp && (
                  <div className="mt-5 border-2 border-grid bg-muted/10 p-4">
                    <div className="flex items-center justify-between border-b border-grid/30 pb-2">
                      <span className="text-xs font-black uppercase">
                        Operation: {activeOp.capability}
                      </span>
                      <span
                        className={`px-2 py-0.5 text-[10px] font-black uppercase ${
                          activeOp.status === "completed"
                            ? "bg-emerald-600 text-white"
                            : activeOp.status === "failed" || activeOp.status === "timeout"
                              ? "bg-block-red text-white"
                              : activeOp.status === "outcome_unknown"
                                ? "bg-amber-600 text-white"
                                : "bg-block-yellow text-block-ink animate-pulse"
                        }`}
                      >
                        {activeOp.status.replace("_", " ")}
                      </span>
                    </div>
                    <div className="mt-2 text-xs">
                      {activeOp.error && (
                        <div className="text-block-red font-mono">
                          Error [{activeOp.error.code}]: {activeOp.error.message}
                        </div>
                      )}
                      {activeOp.result && (
                        <pre className="max-h-60 overflow-y-auto rounded bg-black/80 p-3 font-mono text-[11px] text-green-400">
                          {JSON.stringify(activeOp.result, null, 2)}
                        </pre>
                      )}
                    </div>
                  </div>
                )}

                {/* Operations History */}
                {operations.length > 0 && (
                  <div className="mt-5">
                    <h3 className="section-label mb-2">Recent Execution Audit</h3>
                    <div className="border border-grid divide-y divide-grid/20 max-h-48 overflow-y-auto text-xs">
                      {operations.slice(0, 10).map((op) => (
                        <div
                          key={op.id}
                          className="flex items-center justify-between p-2 hover:bg-muted/30"
                        >
                          <div>
                            <span className="font-bold">{op.capability}</span>
                            <span className="ml-2 font-mono text-[10px] text-muted-foreground">
                              {new Date(op.created_at).toLocaleTimeString()}
                            </span>
                          </div>
                          <span
                            className={`px-1.5 py-0.5 text-[9px] font-black uppercase ${
                              op.status === "completed"
                                ? "text-emerald-600"
                                : op.status === "failed"
                                  ? "text-block-red"
                                  : "text-muted-foreground"
                            }`}
                          >
                            {op.status}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="border-2 border-dashed border-grid p-8 text-center text-muted-foreground">
                Select a device from the list or pair a new machine.
              </div>
            )}

            {/* Pairing & Setup Instructions Card */}
            <div className="border-2 border-grid bg-canvas p-5">
              <h3 className="font-display text-xl uppercase">Pair a Local Machine</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                The Personal OS Local Companion daemon runs on your machine and communicates
                securely via outbound WebSocket.
              </p>
              <div className="mt-3 space-y-2 font-mono text-xs">
                <div className="rounded border border-grid bg-muted/40 p-3">
                  <p className="text-[10px] text-muted-foreground uppercase mb-1">
                    1. Register device credentials
                  </p>
                  <code>
                    pos-companion register --cloud-url http://localhost:8000 --user-token
                    &lt;token&gt;
                  </code>
                </div>
                <div className="rounded border border-grid bg-muted/40 p-3">
                  <p className="text-[10px] text-muted-foreground uppercase mb-1">
                    2. Start companion daemon
                  </p>
                  <code>pos-companion run</code>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
