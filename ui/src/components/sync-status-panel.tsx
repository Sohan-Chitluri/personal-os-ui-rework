import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useOptionalBlockday } from "@/lib/blockday-store";
import { getCognitoAccessToken } from "@/lib/auth";
import { IndexedDbOfflineOperationQueue } from "@/lib/offline-queue";
import {
  databaseNameForAccessToken,
  IndexedDbSyncPersistence,
  SyncCoordinator,
  type SyncCoordinatorState,
  type SyncStatus,
} from "@/lib/sync-coordinator";
import { syncApi, type SyncConflict } from "@/lib/sync-api";
import type { QueueEntry } from "@/lib/offline-queue";

const labels: Record<SyncStatus, string> = {
  pending: "Pending",
  syncing: "Syncing",
  synced: "Synced",
  conflict: "Conflict needs review",
  failed: "Sync failed",
};

export function SyncStatusPanel() {
  const blockday = useOptionalBlockday();
  const queryClient = useQueryClient();
  const [coordinator, setCoordinator] = useState<SyncCoordinator | null>(null);
  const [state, setState] = useState<SyncCoordinatorState>({ status: "synced", error: null });
  const [entries, setEntries] = useState<QueueEntry[]>([]);
  const [detail, setDetail] = useState<SyncConflict | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const token = getCognitoAccessToken();
  const enabled = Boolean(token) && !blockday?.isDemoMode;

  useEffect(() => {
    if (blockday?.coordinator) {
      setState({ status: blockday.syncStatus, error: blockday.syncError });
    }
  }, [blockday?.coordinator, blockday?.syncStatus, blockday?.syncError]);

  useEffect(() => {
    let disposed = false;
    let active: SyncCoordinator | null = null;
    let interval: number | null = null;
    let onOnline: (() => void) | null = null;
    if (!enabled || !token) {
      setCoordinator(null);
      setEntries([]);
      setState({ status: "synced", error: null });
      return;
    }

    if (blockday?.coordinator) {
      active = blockday.coordinator;
      setCoordinator(active);
      const refresh = async () => {
        if (!active || disposed) return;
        try {
          await active.syncNow();
        } catch {
          // Coordinator updates state internally
        }
        if (!disposed && active) {
          setEntries(await active.conflicts());
        }
      };
      void refresh();
      onOnline = () => void refresh();
      window.addEventListener("online", onOnline);
      interval = window.setInterval(() => void refresh(), 30_000);
      return () => {
        disposed = true;
        if (onOnline) window.removeEventListener("online", onOnline);
        if (interval !== null) window.clearInterval(interval);
      };
    }

    void databaseNameForAccessToken(token)
      .then((databaseName) => {
        if (disposed) return;
        const queue = new IndexedDbOfflineOperationQueue({
          database_name: databaseName,
          max_batch_size: 100,
        });
        const persistence = new IndexedDbSyncPersistence(databaseName);
        active = new SyncCoordinator(
          queue,
          persistence,
          syncApi,
          (next) => {
            if (!disposed) setState(next);
          },
          100,
          (items) => {
            if (items.some((item) => item.entity_type === "task")) {
              void queryClient.invalidateQueries({ queryKey: ["tasks"] });
            }
            if (items.some((item) => item.entity_type === "project")) {
              void queryClient.invalidateQueries({ queryKey: ["projects"] });
            }
          },
        );
        setCoordinator(active);
        const refresh = async () => {
          if (!active) return;
          let failed = false;
          try {
            await active.syncNow();
          } catch {
            // The state panel carries the actionable error; queued operations remain durable.
            failed = true;
          }
          if (!disposed && active) {
            if (!failed) await active.refreshStatus();
            setEntries(await active.conflicts());
          }
        };
        void refresh();
        onOnline = () => void refresh();
        window.addEventListener("online", onOnline);
        interval = window.setInterval(() => void refresh(), 30_000);
      })
      .catch((error: unknown) => {
        if (!disposed)
          setState({
            status: "failed",
            error: error instanceof Error ? error.message : String(error),
          });
      });
    return () => {
      disposed = true;
      if (onOnline) window.removeEventListener("online", onOnline);
      if (interval !== null) window.clearInterval(interval);
      active?.queue.close();
    };
  }, [enabled, token, queryClient, blockday?.coordinator]);

  const syncNow = async () => {
    if (!coordinator) return;
    try {
      await coordinator.syncNow();
      setEntries(await coordinator.conflicts());
    } catch {
      setEntries(await coordinator.conflicts());
    }
  };

  const inspect = async (entry: QueueEntry) => {
    const conflictId = entry.acknowledgement?.conflict_id;
    if (!conflictId || !coordinator) return;
    setDetailError(null);
    try {
      setDetail(await coordinator.inspectConflict(conflictId));
    } catch (error) {
      setDetailError(error instanceof Error ? error.message : String(error));
    }
  };

  if (!enabled) {
    return (
      <div className="border-b border-grid px-5 py-2 text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
        {blockday?.isDemoMode
          ? "Local demo · synchronization paused"
          : "Synchronization requires sign-in"}
      </div>
    );
  }

  return (
    <section
      aria-label="Synchronization status"
      className="border-b border-grid bg-panel px-5 py-2 text-xs"
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-black uppercase tracking-wide" role="status">
          Sync: {labels[state.status]}
        </span>
        {state.error && <span className="text-block-red">{state.error}</span>}
        <button
          type="button"
          onClick={() => void syncNow()}
          disabled={!coordinator || state.status === "syncing"}
          className="ml-auto border border-grid px-2 py-1 text-[10px] font-bold uppercase disabled:opacity-50"
        >
          Sync now
        </button>
      </div>
      {entries.length > 0 && (
        <ul className="mt-2 space-y-1" aria-label="Operations needing attention">
          {entries.map((entry) => (
            <li key={entry.operation_id} className="flex flex-wrap items-center gap-2">
              <span
                className={entry.status === "conflicted" ? "font-bold text-block-red" : "font-bold"}
              >
                {entry.status === "conflicted" ? "Conflict" : "Failed"}:{" "}
                {entry.operation?.entity_type} {entry.operation?.entity_id}
              </span>
              <span className="text-muted-foreground">{entry.last_error}</span>
              {entry.acknowledgement?.conflict_id && (
                <button
                  type="button"
                  onClick={() => void inspect(entry)}
                  className="underline underline-offset-2"
                >
                  Review versions
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {detail && (
        <div className="mt-3 border-l-2 border-block-red pl-3" aria-label="Conflict details">
          <p className="font-bold">
            Conflict {detail.conflict_id} · server revision {detail.remote_snapshot.revision}
          </p>
          <div className="mt-1 grid gap-2 md:grid-cols-2">
            <pre className="overflow-auto whitespace-pre-wrap text-[10px]">
              <strong>Local version</strong>
              {"\n"}
              {JSON.stringify(detail.local_payload, null, 2)}
            </pre>
            <pre className="overflow-auto whitespace-pre-wrap text-[10px]">
              <strong>Server version</strong>
              {"\n"}
              {JSON.stringify(detail.remote_snapshot, null, 2)}
            </pre>
          </div>
          <p className="mt-1 text-muted-foreground">
            Choose edits manually, then submit a revision-checked resolution.
          </p>
        </div>
      )}
      {detailError && (
        <p role="alert" className="mt-2 text-block-red">
          Unable to load conflict details: {detailError}
        </p>
      )}
    </section>
  );
}
