"""Companion WebSocket hub and JSON-RPC 2.0 session broker (M2-B).

Implements /ws/companion:
- Authenticated handshake and capability negotiation
- Heartbeat tracking and presence management
- JSON-RPC 2.0 framing and error formatting
- Disconnection cleanup and logging
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from personal_os.api.companion_broker import (
    WebSocketSession,
    get_companion_broker,
)
from personal_os.companion.protocol import (
    DEFAULT_CAPABILITIES,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_STALE_TIMEOUT_SECONDS,
    IDEMPOTENT_CAPABILITIES,
    PROTOCOL_VERSION,
    HandshakeParams,
    HandshakeResult,
    HeartbeatParams,
    HeartbeatResult,
    PresenceState,
    RPCErrorCode,
    RPCRequest,
    RPCResponse,
)
from personal_os.config import get_settings
from personal_os.db import models as m
from personal_os.db.session import build_engine, build_session_factory
from personal_os.services.devices import DeviceAuthError, build_device_service

log = logging.getLogger("personal_os.companion_ws")
router = APIRouter(tags=["Companion WebSocket"])


@router.websocket("/ws/companion")
async def companion_websocket_endpoint(websocket: WebSocket) -> None:
    """Companion daemon WebSocket endpoint supporting JSON-RPC 2.0."""
    await websocket.accept()
    session = WebSocketSession(websocket)
    settings = getattr(websocket.app.state, "settings", None) or get_settings()
    broker = getattr(websocket.app.state, "companion_broker", None) or get_companion_broker()
    session_factory = getattr(websocket.app.state, "db_session_factory", None)
    if session_factory is None:
        engine = build_engine(settings)
        session_factory = build_session_factory(engine)

    log.info("WebSocket connected: session_id=%s", session.session_id)

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg_data = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                await session.send_error(
                    None,
                    RPCErrorCode.PARSE_ERROR,
                    f"Parse error: {exc}",
                )
                continue

            # Check if this message is a response to an outstanding server-to-companion RPC
            if isinstance(msg_data, dict) and ("result" in msg_data or "error" in msg_data):
                req_id = msg_data.get("id")
                if req_id and req_id in session.pending_requests:
                    try:
                        rpc_resp = RPCResponse.model_validate(msg_data)
                        future = session.pending_requests.pop(req_id, None)
                        if future and not future.done():
                            future.set_result(rpc_resp)
                        continue
                    except Exception as exc:
                        log.warning("Malformed RPC response received: %s", exc)
                        continue

            try:
                rpc_req = RPCRequest.model_validate(msg_data)
            except Exception as exc:
                await session.send_error(
                    msg_data.get("id") if isinstance(msg_data, dict) else None,
                    RPCErrorCode.INVALID_REQUEST,
                    f"Invalid JSON-RPC request: {exc}",
                )
                continue

            # --------------------------------------------------------------
            # Handshake phase
            # --------------------------------------------------------------
            if not session.authenticated:
                if rpc_req.method != "auth.handshake":
                    await session.send_error(
                        rpc_req.id,
                        RPCErrorCode.UNAUTHORIZED,
                        "Unauthorized: First message must be 'auth.handshake'",
                    )
                    await websocket.close(code=4401, reason="Unauthorized")
                    break

                try:
                    handshake_params = HandshakeParams.model_validate(rpc_req.params)
                except Exception as exc:
                    await session.send_error(
                        rpc_req.id,
                        RPCErrorCode.INVALID_PARAMS,
                        f"Malformed handshake parameters: {exc}",
                    )
                    await websocket.close(code=4400, reason="Invalid handshake params")
                    break

                if handshake_params.protocol_version != PROTOCOL_VERSION:
                    await session.send_error(
                        rpc_req.id,
                        RPCErrorCode.VERSION_MISMATCH,
                        f"Unsupported protocol version: '{handshake_params.protocol_version}'",
                        {"supported_versions": [PROTOCOL_VERSION]},
                    )
                    await websocket.close(code=4400, reason="Version mismatch")
                    break

                # Authenticate device token with DB
                async with session_factory() as db:
                    dev_service = build_device_service(db, settings)
                    try:
                        claims = dev_service.verify_device_token(handshake_params.token)
                    except DeviceAuthError as exc:
                        log.warning("Companion auth failed: %s", exc)
                        await session.send_error(
                            rpc_req.id,
                            RPCErrorCode.UNAUTHORIZED,
                            f"Authentication failed: {exc}",
                            {"revoked": True},
                        )
                        await websocket.close(code=4401, reason="Authentication failed")
                        break

                    token_device_id = claims.get("device_id")
                    token_user_id = claims.get("user_id")

                    if token_device_id != handshake_params.device_id:
                        await session.send_error(
                            rpc_req.id,
                            RPCErrorCode.FORBIDDEN,
                            "Device ID mismatch between handshake and credentials",
                        )
                        await websocket.close(code=4403, reason="Device ID mismatch")
                        break

                    device = await dev_service.repo.get_by_device_id(
                        handshake_params.device_id, owner_id=token_user_id
                    )
                    if not device or device.revoked_at is not None:
                        await session.send_error(
                            rpc_req.id,
                            RPCErrorCode.UNAUTHORIZED,
                            "Device revoked or not found",
                            {"revoked": True},
                        )
                        await websocket.close(code=4401, reason="Device revoked")
                        break

                    # Negotiate capabilities
                    requested = set(handshake_params.supported_capabilities)
                    available = set(DEFAULT_CAPABILITIES.keys())
                    granted = list(requested.intersection(available))

                    # Update presence to online
                    await dev_service.record_heartbeat(
                        device_id=device.device_id,
                        owner_id=device.owner_id,
                        status="online",
                        timestamp=datetime.now(UTC),
                    )
                    await db.commit()

                    session.authenticated = True
                    session.device_id = device.device_id
                    session.user_id = device.owner_id
                    session.presence = PresenceState.ONLINE
                    session.granted_capabilities = granted
                    await broker.register_session(session.user_id, session.device_id, session)

                    if getattr(broker, "_session_factory", None) is None:
                        broker._session_factory = session_factory

                    # Trigger recovery/dispatch for queued pending ops on reconnect
                    try:
                        dev_id = device.device_id
                        pending_ops = await dev_service.op_repo.list_pending_for_devices([dev_id])
                        for pop in pending_ops:
                            if hasattr(broker, "_claim_and_execute"):
                                asyncio.create_task(
                                    broker._claim_and_execute(pop.id, pop.owner_id, pop.device_id)
                                )
                    except Exception as exc:
                        log.warning("Failed to trigger pending ops on reconnect: %s", exc)

                    result = HandshakeResult(
                        authenticated=True,
                        session_id=session.session_id,
                        device_id=device.device_id,
                        user_id=device.owner_id,
                        protocol_version=PROTOCOL_VERSION,
                        granted_capabilities=granted,
                        heartbeat_interval_seconds=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
                        stale_timeout_seconds=DEFAULT_STALE_TIMEOUT_SECONDS,
                    )
                    await session.send_response(
                        RPCResponse.success(rpc_req.id, result.model_dump())
                    )
                    log.info(
                        "Companion authenticated: device_id=%s user_id=%s capabilities=%s",
                        device.device_id,
                        device.owner_id,
                        granted,
                    )
                    continue

            # --------------------------------------------------------------
            # Authenticated message handling
            # --------------------------------------------------------------
            method = rpc_req.method
            req_id = rpc_req.id

            if not session.authenticated or not session.device_id or not session.user_id:
                await session.send_error(
                    req_id,
                    RPCErrorCode.UNAUTHORIZED,
                    "Session is not authenticated",
                )
                continue

            device_id = session.device_id
            user_id = session.user_id

            if method == "device.heartbeat":
                try:
                    hb_params = HeartbeatParams.model_validate(rpc_req.params)
                except Exception as exc:
                    await session.send_error(
                        req_id,
                        RPCErrorCode.INVALID_PARAMS,
                        f"Malformed heartbeat params: {exc}",
                    )
                    continue

                async with session_factory() as db:
                    dev_service = build_device_service(db, settings)
                    dev = await dev_service.repo.get_by_device_id(device_id, owner_id=user_id)
                    if not dev or dev.revoked_at is not None:
                        await session.send_error(
                            req_id,
                            RPCErrorCode.UNAUTHORIZED,
                            "Device revoked during active session",
                        )
                        await websocket.close(code=4401, reason="Device revoked")
                        break

                    await dev_service.record_heartbeat(
                        device_id=device_id,
                        owner_id=user_id,
                        status=hb_params.status.value,
                        timestamp=hb_params.timestamp,
                    )
                    await db.commit()

                session.presence = hb_params.status
                hb_result = HeartbeatResult(
                    acknowledged=True,
                    server_timestamp=datetime.now(UTC),
                    status=session.presence,
                )
                await session.send_response(RPCResponse.success(req_id, hb_result.model_dump()))

            elif method == "system.ping":
                echo = rpc_req.params.get("echo")
                await session.send_response(
                    RPCResponse.success(
                        req_id,
                        {
                            "pong": True,
                            "echo": echo,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    )
                )

            elif method in ("capabilities.list", "capabilities.manifest"):
                manifest = {
                    "protocol_version": PROTOCOL_VERSION,
                    "granted_capabilities": session.granted_capabilities,
                    "available_capabilities": {
                        k: v.model_dump() for k, v in DEFAULT_CAPABILITIES.items()
                    },
                }
                await session.send_response(RPCResponse.success(req_id, manifest))

            elif method in ("process.list", "filesystem.discover", "filesystem.stat"):
                # Gated by capability check
                cap_name = method
                if cap_name not in session.granted_capabilities:
                    await session.send_error(
                        req_id,
                        RPCErrorCode.FORBIDDEN,
                        f"Capability '{cap_name}' not granted in session",
                    )
                    continue

                # Echo request back or dispatch to device
                await session.send_response(
                    RPCResponse.success(
                        req_id,
                        {
                            "status": "dispatched",
                            "method": method,
                            "params": rpc_req.params,
                        },
                    )
                )

            else:
                await session.send_error(
                    req_id,
                    RPCErrorCode.METHOD_NOT_FOUND,
                    f"Method '{method}' not found or unsupported",
                )

    except WebSocketDisconnect:
        log.info(
            "Companion disconnected: session_id=%s device_id=%s",
            session.session_id,
            session.device_id,
        )
    except Exception as exc:
        log.error("Companion WebSocket exception: %s", exc, exc_info=True)
    finally:
        if session.authenticated and session.device_id and session.user_id:
            try:
                await broker.unregister_session(session.user_id, session.device_id, session)
            except Exception as exc:
                log.warning("Failed to unregister session from broker: %s", exc)
            try:
                async with session_factory() as db:
                    dev_service = build_device_service(db, settings)
                    await dev_service.repo.update_heartbeat(
                        device_id=session.device_id,
                        owner_id=session.user_id,
                        status="offline",
                        timestamp=datetime.now(UTC),
                    )

                    # Reconcile in-flight operations for disconnected session to outcome_unknown
                    stmt = select(m.DeviceOperation).where(
                        m.DeviceOperation.owner_id == session.user_id,
                        m.DeviceOperation.device_id == session.device_id,
                        m.DeviceOperation.status == "running",
                    )
                    running_ops = (await db.execute(stmt)).scalars().all()
                    now = datetime.now(UTC)
                    for op in running_ops:
                        is_idempotent = IDEMPOTENT_CAPABILITIES.get(op.capability, False)
                        op.status = "outcome_unknown"
                        op.error = {
                            "code": -32000,
                            "message": "Device disconnected mid-operation; outcome is ambiguous",
                            "data": {"idempotent": is_idempotent, "capability": op.capability},
                        }
                        op.finished_at = now
                        op.updated_at = now
                        op.revision += 1

                    await db.commit()
            except Exception as exc:
                log.warning(
                    "Failed to record offline status or reconcile running ops on disconnect: %s",
                    exc,
                )
