"""Companion WebSocket connection manager and RPC session broker (M2-B/M2-C/M4).

Manages active WebSocket connections from local companion daemons, tracks presence,
and provides bidirectional RPC dispatch from the cloud control plane to local companions.
Supports cross-process message routing, atomic operation claiming, reconciliation,
and ambiguous outcome recovery.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

from personal_os.companion.protocol import (
    IDEMPOTENT_CAPABILITIES,
    PresenceState,
    RPCErrorCode,
    RPCRequest,
    RPCResponse,
)

log = logging.getLogger("personal_os.companion_broker")

CHANNEL_RPC_DISPATCH = "companion_rpc_dispatch"
CHANNEL_RPC_REPLY = "companion_rpc_reply"


class DeviceOfflineError(Exception):
    """Raised when an RPC request is dispatched to an offline device."""


class CapabilityNotGrantedError(Exception):
    """Raised when an RPC request targets an ungranted capability."""


class AmbiguousOutcomeError(Exception):
    """Raised when an RPC outcome cannot be verified (e.g. disconnect mid-execution)."""

    def __init__(
        self,
        message: str,
        *,
        idempotent: bool = True,
        capability: str | None = None,
    ) -> None:
        super().__init__(message)
        self.idempotent = idempotent
        self.capability = capability


# ---------------------------------------------------------------------------
# Notification Bus (Cross-Process Wake-Up)
# ---------------------------------------------------------------------------


class NotificationBus:
    """Abstract interface for inter-worker ephemeral wake-up events."""

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        raise NotImplementedError

    async def subscribe(
        self, channel: str, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        raise NotImplementedError


class AsyncMemoryNotificationBus(NotificationBus):
    """In-memory notification bus for single-worker or multi-worker simulation."""

    def __init__(self) -> None:
        self._subscribers: dict[
            str, list[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]]
        ] = {}
        self._lock = asyncio.Lock()

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        async with self._lock:
            callbacks = list(self._subscribers.get(channel, []))
        for cb in callbacks:
            asyncio.create_task(cb(message))

    async def subscribe(
        self, channel: str, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        async with self._lock:
            self._subscribers.setdefault(channel, []).append(callback)


class PostgresNotificationBus(NotificationBus):
    """PostgreSQL LISTEN/NOTIFY notification bus for multi-process worker coordination."""

    def __init__(self, dsn: str, session_factory: Any = None) -> None:
        self.dsn = dsn
        self._session_factory = session_factory
        self._subscribers: dict[
            str, list[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]]
        ] = {}
        self._listen_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._listen_task is None or self._listen_task.done():
            self._stop_event.clear()
            self._listen_task = asyncio.create_task(self._listen_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._listen_task:
            self._listen_task.cancel()
            await asyncio.gather(self._listen_task, return_exceptions=True)
            self._listen_task = None

    async def _listen_loop(self) -> None:
        raw_dsn = self.dsn.replace("postgresql+asyncpg://", "postgresql://")
        while not self._stop_event.is_set():
            conn = None
            try:
                import asyncpg  # type: ignore[import-untyped]

                conn = await asyncpg.connect(raw_dsn)

                def _handler(connection: Any, pid: int, channel: str, payload: str) -> None:
                    try:
                        msg = json.loads(payload)
                    except Exception:
                        return
                    callbacks = list(self._subscribers.get(channel, []))
                    for cb in callbacks:
                        asyncio.create_task(cb(msg))

                async with self._lock:
                    channels = list(self._subscribers.keys())
                for ch in channels:
                    await conn.add_listener(ch, _handler)

                while not self._stop_event.is_set():
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._stop_event.is_set():
                    log.warning("PostgresNotificationBus listener error: %s (retrying in 2s)", exc)
                    await asyncio.sleep(2.0)
            finally:
                if conn:
                    with contextlib.suppress(Exception):
                        await conn.close()

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        payload = json.dumps(message)
        if self._session_factory is not None:
            from sqlalchemy import text

            async with self._session_factory() as session:
                await session.execute(
                    text("SELECT pg_notify(:channel, :payload)"),
                    {"channel": channel, "payload": payload},
                )
                await session.commit()
        else:
            raw_dsn = self.dsn.replace("postgresql+asyncpg://", "postgresql://")
            import asyncpg

            conn = await asyncpg.connect(raw_dsn)
            try:
                await conn.execute("SELECT pg_notify($1, $2)", channel, payload)
            finally:
                await conn.close()

    async def subscribe(
        self, channel: str, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        async with self._lock:
            self._subscribers.setdefault(channel, []).append(callback)


# Shared default bus singleton
default_notification_bus = AsyncMemoryNotificationBus()


# ---------------------------------------------------------------------------
# WebSocket Session
# ---------------------------------------------------------------------------


class WebSocketSession:
    """Represents an active companion WebSocket connection."""

    def __init__(self, websocket: WebSocket) -> None:
        self.ws = websocket
        self.session_id = f"sess_{uuid4().hex[:12]}"
        self.device_id: str | None = None
        self.user_id: str | None = None
        self.authenticated = False
        self.presence: PresenceState = PresenceState.CONNECTING
        self.granted_capabilities: list[str] = []
        self.connected_at: datetime = datetime.now(UTC)
        self.last_seen_at: datetime = datetime.now(UTC)
        self.pending_requests: dict[str | int, asyncio.Future[RPCResponse]] = {}
        # Tracks requests that have been transmitted across the wire
        self.dispatched_requests: set[str | int] = set()

    async def send_response(self, response: RPCResponse) -> None:
        text = response.model_dump_json()
        await self.ws.send_text(text)

    async def send_request(self, request: RPCRequest) -> None:
        if request.id is not None:
            self.dispatched_requests.add(request.id)
        text = request.model_dump_json()
        await self.ws.send_text(text)

    async def send_error(
        self,
        req_id: str | int | None,
        code: int | RPCErrorCode,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        resp = RPCResponse.failure(req_id, code, message, data)
        await self.send_response(resp)

    def abort_pending_requests(self, reason: str = "Device disconnected") -> list[str | int]:
        """Cancel and fail any outstanding requests dispatched to this session.

        Returns a list of request IDs whose frames were already transmitted across the wire,
        representing ambiguous outcomes.
        """
        ambiguous_req_ids: list[str | int] = []
        for req_id, future in list(self.pending_requests.items()):
            if not future.done():
                if req_id in self.dispatched_requests:
                    ambiguous_req_ids.append(req_id)
                    future.set_exception(
                        AmbiguousOutcomeError(
                            f"Device disconnected after RPC frame was dispatched: {reason}",
                        )
                    )
                else:
                    future.set_exception(DeviceOfflineError(reason))
        self.pending_requests.clear()
        self.dispatched_requests.clear()
        return ambiguous_req_ids


# ---------------------------------------------------------------------------
# Companion Broker
# ---------------------------------------------------------------------------


class CompanionBroker:
    """Manages companion WebSocket sessions and coordinates cross-worker dispatch."""

    def __init__(
        self,
        bus: NotificationBus | None = None,
        worker_id: str | None = None,
        is_cluster_mode: bool = False,
    ) -> None:
        self._sessions: dict[tuple[str, str], WebSocketSession] = {}
        self._lock = asyncio.Lock()
        self.bus: NotificationBus = bus or default_notification_bus
        self.worker_id: str = worker_id or f"worker_{uuid4().hex[:8]}"
        self.is_cluster_mode = is_cluster_mode
        self._subscribed = False
        self._background_tasks: list[asyncio.Task] = []
        self._session_factory: Any | None = None

    async def register_session(
        self, user_id: str, device_id: str, session: WebSocketSession
    ) -> None:
        async with self._lock:
            key = (user_id, device_id)
            existing = self._sessions.get(key)
            if existing and existing is not session:
                log.info(
                    "Replacing existing session for user=%s device=%s",
                    user_id,
                    device_id,
                )
                existing.abort_pending_requests("Replaced by new connection")
            self._sessions[key] = session
            log.info("Registered companion session: user=%s device=%s", user_id, device_id)

    async def unregister_session(
        self, user_id: str, device_id: str, session: WebSocketSession
    ) -> list[str | int]:
        async with self._lock:
            key = (user_id, device_id)
            if self._sessions.get(key) is session:
                del self._sessions[key]
                ambiguous = session.abort_pending_requests("Device connection terminated")
                log.info("Unregistered companion session: user=%s device=%s", user_id, device_id)
                return ambiguous
            return []

    def get_session(self, user_id: str, device_id: str) -> WebSocketSession | None:
        return self._sessions.get((user_id, device_id))

    def is_online(self, user_id: str, device_id: str) -> bool:
        session = self.get_session(user_id, device_id)
        return (
            session is not None
            and session.authenticated
            and session.presence in (PresenceState.ONLINE, PresenceState.DEGRADED)
        )

    def active_device_ids(self) -> list[str]:
        """Return list of device IDs currently connected to this worker."""
        return [dev_id for (_, dev_id), s in self._sessions.items() if s.authenticated]

    async def dispatch_rpc(
        self,
        user_id: str,
        device_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: float = 15.0,
    ) -> RPCResponse:
        """Dispatch a JSON-RPC request to a locally connected companion and await its response."""
        session = self.get_session(user_id, device_id)
        if not session or not session.authenticated:
            raise DeviceOfflineError(f"Device '{device_id}' is offline or not authenticated")

        if method != "system.ping" and method not in session.granted_capabilities:
            raise CapabilityNotGrantedError(
                f"Capability '{method}' is not granted to device '{device_id}'"
            )

        req_id = f"srv_{uuid4().hex[:12]}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[RPCResponse] = loop.create_future()
        session.pending_requests[req_id] = future

        rpc_req = RPCRequest(id=req_id, method=method, params=params or {})

        try:
            await session.send_request(rpc_req)
            response = await asyncio.wait_for(future, timeout=timeout_seconds)
            return response
        except TimeoutError as exc:
            log.warning(
                "RPC request %s (%s) to device %s timed out after %ss",
                req_id,
                method,
                device_id,
                timeout_seconds,
            )
            raise TimeoutError(
                f"Capability execution timed out after {timeout_seconds} seconds"
            ) from exc
        finally:
            session.pending_requests.pop(req_id, None)
            session.dispatched_requests.discard(req_id)

    # -----------------------------------------------------------------------
    # Cross-Process Notification & Dispatch Hooks
    # -----------------------------------------------------------------------

    async def publish_dispatch(
        self,
        operation_id: str,
        owner_id: str,
        device_id: str,
        capability: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        """Publish wake-up notification on companion_rpc_dispatch channel."""
        message = {
            "op_id": operation_id,
            "owner_id": owner_id,
            "device_id": device_id,
            "capability": capability,
            "params": params or {},
            "timeout_seconds": timeout_seconds,
            "publisher_worker_id": self.worker_id,
        }
        await self.bus.publish(CHANNEL_RPC_DISPATCH, message)

    async def start_coordinator(self, session_factory: Any) -> None:
        """Start background coordination: notification listener, reconciliation, and reaper."""
        self._session_factory = session_factory

        if isinstance(self.bus, PostgresNotificationBus):
            if self.bus._session_factory is None:
                self.bus._session_factory = session_factory
            await self.bus.start()

        if not self._subscribed:
            await self.bus.subscribe(CHANNEL_RPC_DISPATCH, self._on_dispatch_notification)
            self._subscribed = True

        # Launch periodic reconciliation sweep (every 5 seconds)
        recon_task = asyncio.create_task(self._reconciliation_loop())
        self._background_tasks.append(recon_task)

        # Launch periodic stale reaper sweep (every 30 seconds)
        reaper_task = asyncio.create_task(self._reaper_loop())
        self._background_tasks.append(reaper_task)

    async def stop_coordinator(self) -> None:
        """Stop all background coordination loops."""
        if isinstance(self.bus, PostgresNotificationBus):
            await self.bus.stop()
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

    def clear(self) -> None:
        """Reset internal broker state (for test isolation)."""
        self._sessions.clear()
        self._background_tasks.clear()
        self._session_factory = None
        self._subscribed = False

    async def _on_dispatch_notification(self, message: dict[str, Any]) -> None:
        """Handle wake-up notification from another worker."""
        owner_id = message.get("owner_id")
        device_id = message.get("device_id")
        op_id = message.get("op_id")
        if not owner_id or not device_id or not op_id:
            return

        session = self.get_session(owner_id, device_id)
        if not session or not session.authenticated:
            # Device not connected to this worker; ignore
            return

        # Atomically claim and execute this operation
        if self._session_factory:
            asyncio.create_task(self._claim_and_execute(op_id, owner_id, device_id))

    async def _claim_and_execute(self, operation_id: str, owner_id: str, device_id: str) -> None:
        """Claim a specific pending operation atomically and dispatch to connected companion."""
        if not self._session_factory:
            return

        from personal_os.db.repositories.sqlalchemy import SqlDeviceOperationRepository

        async with self._session_factory() as db:
            op_repo = SqlDeviceOperationRepository(db)
            claimed = await op_repo.claim_pending(
                owner_id=owner_id,
                device_id=device_id,
                operation_id=operation_id,
            )
            if not claimed:
                return
            await db.commit()

            capability = claimed.capability
            params = claimed.params or {}
            timeout_seconds = claimed.timeout_seconds or 15

        # Execute dispatch outside the DB transaction
        is_idempotent = IDEMPOTENT_CAPABILITIES.get(capability, False)

        try:
            resp = await self.dispatch_rpc(
                owner_id,
                device_id,
                capability,
                params,
                timeout_seconds=float(timeout_seconds),
            )
            async with self._session_factory() as db:
                op_repo = SqlDeviceOperationRepository(db)
                if resp.error:
                    await op_repo.update_status(
                        operation_id,
                        owner_id,
                        status="failed",
                        error=resp.error.model_dump(),
                        finished_at=datetime.now(UTC),
                    )
                else:
                    await op_repo.update_status(
                        operation_id,
                        owner_id,
                        status="completed",
                        result=resp.result,
                        finished_at=datetime.now(UTC),
                    )
                await db.commit()
        except AmbiguousOutcomeError as exc:
            log.warning("Operation %s ended with ambiguous outcome: %s", operation_id, exc)
            async with self._session_factory() as db:
                op_repo = SqlDeviceOperationRepository(db)
                await op_repo.update_status(
                    operation_id,
                    owner_id,
                    status="outcome_unknown",
                    error={
                        "code": -32000,
                        "message": str(exc),
                        "data": {"idempotent": is_idempotent, "capability": capability},
                    },
                    finished_at=datetime.now(UTC),
                )
                await db.commit()
        except DeviceOfflineError as exc:
            log.warning("Device offline during dispatch for op %s: %s", operation_id, exc)
            async with self._session_factory() as db:
                op_repo = SqlDeviceOperationRepository(db)
                await op_repo.update_status(
                    operation_id,
                    owner_id,
                    status="failed",
                    error={"code": -32001, "message": str(exc)},
                    finished_at=datetime.now(UTC),
                )
                await db.commit()
        except Exception as exc:
            log.exception("Unexpected error executing op %s: %s", operation_id, exc)
            async with self._session_factory() as db:
                op_repo = SqlDeviceOperationRepository(db)
                await op_repo.update_status(
                    operation_id,
                    owner_id,
                    status="failed",
                    error={"code": -32603, "message": str(exc)},
                    finished_at=datetime.now(UTC),
                )
                await db.commit()

        # Notify waiting workers that operation has finalized
        await self.bus.publish(CHANNEL_RPC_REPLY, {"op_id": operation_id})

    async def _reconciliation_loop(self) -> None:
        """Periodic safety net: poll for un-dispatched pending operations for connected devices."""
        while True:
            try:
                await asyncio.sleep(5.0)
                if not self._session_factory:
                    continue

                active_ids = self.active_device_ids()
                if not active_ids:
                    continue

                from personal_os.db.repositories.sqlalchemy import SqlDeviceOperationRepository

                async with self._session_factory() as db:
                    op_repo = SqlDeviceOperationRepository(db)
                    pending_ops = await op_repo.list_pending_for_devices(active_ids)

                for op in pending_ops:
                    asyncio.create_task(self._claim_and_execute(op.id, op.owner_id, op.device_id))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("Reconciliation loop error: %s", exc)

    async def _reaper_loop(self) -> None:
        """Periodic safety sweep: reap orphaned 'running' operations to 'outcome_unknown'."""
        while True:
            try:
                await asyncio.sleep(30.0)
                if not self._session_factory:
                    continue

                from personal_os.db.repositories.sqlalchemy import SqlDeviceOperationRepository

                async with self._session_factory() as db:
                    op_repo = SqlDeviceOperationRepository(db)
                    reaped = await op_repo.reap_stale_running()
                    if reaped:
                        await db.commit()
                        log.info(
                            "Reaped %d stale in-flight operations to outcome_unknown",
                            len(reaped),
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("Reaper loop error: %s", exc)


# Default module-level broker singleton
default_companion_broker = CompanionBroker()


def get_companion_broker() -> CompanionBroker:
    return default_companion_broker
