"""Device management and presence service for Personal OS (M2-B).

Handles device registration, heartbeat presence tracking, credential generation,
and stale device expiration.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.api.companion_broker import AmbiguousOutcomeError, DeviceOfflineError
from personal_os.companion.protocol import IDEMPOTENT_CAPABILITIES, PresenceState
from personal_os.db.models import Device, DeviceOperation
from personal_os.db.repositories.sqlalchemy import (
    SqlDeviceOperationRepository,
    SqlDeviceRepository,
    StaleRevisionError,
)
from personal_os.services.interfaces import ServiceConflictError, ServiceNotFound

log = logging.getLogger(__name__)

DEFAULT_STALE_TIMEOUT_SECONDS = 60
DEFAULT_DEVICE_TOKEN_EXPIRY_SECONDS = 86400  # 24 hours
ALLOWED_CAPABILITIES = {
    "system.ping",
    "system.health",
    "capabilities.list",
    "process.list",
    "filesystem.discover",
    "filesystem.stat",
}


class DeviceAuthError(Exception):
    """Raised when device credentials are invalid or revoked."""


class DeviceService:
    """Domain service managing the local companion device registry and presence."""

    def __init__(
        self,
        session: AsyncSession,
        secret: str,
        *,
        allow_synthetic_tokens: bool = False,
    ) -> None:
        self.session = session
        self.repo = SqlDeviceRepository(session)
        self.op_repo = SqlDeviceOperationRepository(session)
        self.secret = secret
        self.allow_synthetic_tokens = allow_synthetic_tokens

    # ----------------------------------------------------------------------
    # Token helpers
    # ----------------------------------------------------------------------

    def create_device_token(
        self,
        device_id: str,
        user_id: str,
        capabilities: dict[str, Any] | None = None,
        expires_delta: timedelta = timedelta(seconds=DEFAULT_DEVICE_TOKEN_EXPIRY_SECONDS),
    ) -> str:
        """Create a signed JWT credential for a registered device."""
        now = datetime.now(UTC)
        payload = {
            "sub": device_id,
            "device_id": device_id,
            "user_id": user_id,
            "capabilities": capabilities or {},
            "token_use": "device",
            "iat": int(now.timestamp()),
            "exp": int((now + expires_delta).timestamp()),
        }
        return jwt.encode(payload, self.secret, algorithm="HS256")

    def verify_device_token(self, token: str) -> dict[str, Any]:
        """Verify and decode a device token.

        Supports synthetic test tokens 'device:<device_id>:<user_id>' for tests.
        """
        if token.startswith("device:") and self.allow_synthetic_tokens:
            parts = token.split(":")
            if len(parts) >= 3:
                return {
                    "device_id": parts[1],
                    "user_id": parts[2],
                    "token_use": "device",
                    "synthetic": True,
                }
            raise DeviceAuthError("Malformed synthetic device token")

        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                options={"require": ["sub", "device_id", "user_id", "exp", "token_use"]},
            )
            if payload.get("token_use") != "device":
                raise DeviceAuthError("Token is not a device credential")
            return payload
        except jwt.PyJWTError as exc:
            raise DeviceAuthError(f"Invalid or expired device token: {exc}") from exc

    # ----------------------------------------------------------------------
    # Device CRUD & Lifecycle
    # ----------------------------------------------------------------------

    async def register_device(
        self,
        owner_id: str,
        device_id: str,
        name: str,
        *,
        platform: str = "linux",
        capabilities: dict[str, Any] | None = None,
        public_key: str | None = None,
    ) -> tuple[Device, str]:
        """Register a new device or update existing device owned by user.

        Raises:
            ServiceConflictError: if device_id is already registered to a different user.
        """
        existing = await self.repo.get_by_device_id(device_id)
        if existing is not None and existing.owner_id != owner_id:
            raise ServiceConflictError("device", device_id, existing.revision)

        device = await self.repo.register(
            owner_id=owner_id,
            device_id=device_id,
            name=name,
            platform=platform,
            capabilities=capabilities,
            public_key=public_key,
        )

        token = self.create_device_token(
            device_id=device.device_id,
            user_id=owner_id,
            capabilities=device.capabilities,
        )
        return device, token

    async def get_device(self, device_id: str, owner_id: str) -> Device:
        """Get device by device_id ensuring owner scope."""
        device = await self.repo.get_by_device_id(device_id, owner_id=owner_id)
        if not device:
            raise ServiceNotFound(f"Device '{device_id}' not found")
        return device

    async def list_devices(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        include_revoked: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Device]:
        """List devices owned by user."""
        devices = await self.repo.list_for_owner(
            owner_id=owner_id,
            status=status,
            include_revoked=include_revoked,
            limit=limit,
            offset=offset,
        )
        return list(devices)

    async def update_device(
        self,
        device_id: str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        name: str | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> Device:
        """Update device metadata with optimistic revision lock."""
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if capabilities is not None:
            fields["capabilities"] = capabilities

        try:
            device = await self.repo.update(
                device_id=device_id,
                owner_id=owner_id,
                expected_revision=expected_revision,
                **fields,
            )
        except StaleRevisionError as exc:
            raise ServiceConflictError("device", device_id, exc.current_revision) from exc

        if not device:
            raise ServiceNotFound(f"Device '{device_id}' not found")
        return device

    async def revoke_device(self, device_id: str, owner_id: str) -> None:
        """Revoke a device. Revoked devices cannot authenticate or heartbeat."""
        revoked = await self.repo.revoke(device_id, owner_id=owner_id)
        if not revoked:
            raise ServiceNotFound(f"Device '{device_id}' not found or already revoked")

    async def record_heartbeat(
        self,
        device_id: str,
        owner_id: str,
        *,
        status: str = "online",
        timestamp: datetime | None = None,
    ) -> Device:
        """Record device heartbeat and update presence status."""
        device = await self.repo.update_heartbeat(
            device_id=device_id,
            owner_id=owner_id,
            status=status,
            timestamp=timestamp,
        )
        if not device:
            raise ServiceNotFound(f"Device '{device_id}' not found or revoked")
        return device

    def evaluate_presence(
        self,
        device: Device,
        stale_timeout_seconds: int = DEFAULT_STALE_TIMEOUT_SECONDS,
    ) -> PresenceState:
        """Calculate dynamic presence state based on last seen timestamp."""
        if device.revoked_at is not None:
            return PresenceState.OFFLINE
        if not device.last_seen_at:
            return PresenceState.OFFLINE

        now = datetime.now(UTC)
        last_seen = device.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)

        elapsed = (now - last_seen).total_seconds()
        if elapsed > stale_timeout_seconds:
            return PresenceState.STALE

        try:
            return PresenceState(device.status)
        except ValueError:
            return PresenceState.ONLINE

    async def sweep_stale_devices(
        self, stale_timeout_seconds: int = DEFAULT_STALE_TIMEOUT_SECONDS
    ) -> int:
        """Sweep online/connecting devices with no recent heartbeats to stale."""
        threshold = datetime.now(UTC) - timedelta(seconds=stale_timeout_seconds)
        return await self.repo.mark_stale(threshold)

    # ----------------------------------------------------------------------
    # Device Operations / Capability Execution
    # ----------------------------------------------------------------------

    async def request_operation(
        self,
        owner_id: str,
        device_id: str,
        capability: str,
        params: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        timeout_seconds: int = 15,
        broker: Any | None = None,
    ) -> DeviceOperation:
        """Create and dispatch a capability operation request on a registered device."""
        device = await self.repo.get_by_device_id(device_id, owner_id=owner_id)
        if not device:
            raise ServiceNotFound("Device", device_id)
        if device.revoked_at is not None:
            raise DeviceAuthError("Device has been revoked")

        # Idempotent replay check
        if idempotency_key:
            existing = await self.op_repo.get_by_idempotency_key(
                owner_id, device_id, idempotency_key
            )
            if existing:
                if existing.status == "completed":
                    return existing
                if existing.status in ("pending", "running"):
                    return existing
                if existing.status == "outcome_unknown":
                    is_idempotent = IDEMPOTENT_CAPABILITIES.get(existing.capability, False)
                    if not is_idempotent:
                        raise ServiceConflictError(
                            "device_operation",
                            existing.id,
                            existing.revision,
                        )
                return existing

        # Validate capability against low-risk allowlist
        if capability not in ALLOWED_CAPABILITIES:
            raise ValueError(f"Capability '{capability}' is unknown or not permitted")

        # Create durable operation record in pending state
        operation = await self.op_repo.create(
            owner_id=owner_id,
            device_id=device_id,
            capability=capability,
            params=params or {},
            idempotency_key=idempotency_key,
            timeout_seconds=timeout_seconds,
        )
        await self.session.commit()

        # If broker is available, attempt immediate dispatch or notify
        if broker is not None:
            now = datetime.now(UTC)
            if broker.is_online(owner_id, device_id):
                # Atomically claim operation
                claimed = await self.op_repo.claim_pending(
                    owner_id, device_id, operation_id=operation.id
                )
                if not claimed:
                    return operation
                await self.session.commit()

                is_idempotent = IDEMPOTENT_CAPABILITIES.get(capability, False)
                try:
                    rpc_resp = await broker.dispatch_rpc(
                        owner_id,
                        device_id,
                        capability,
                        params or {},
                        timeout_seconds=float(timeout_seconds),
                    )
                    finish_time = datetime.now(UTC)
                    if rpc_resp.error:
                        await self.op_repo.update_status(
                            operation.id,
                            owner_id=owner_id,
                            status="failed",
                            error=rpc_resp.error.model_dump(),
                            finished_at=finish_time,
                        )
                    else:
                        await self.op_repo.update_status(
                            operation.id,
                            owner_id=owner_id,
                            status="completed",
                            result=rpc_resp.result,
                            finished_at=finish_time,
                        )
                except AmbiguousOutcomeError as exc:
                    await self.op_repo.update_status(
                        operation.id,
                        owner_id=owner_id,
                        status="outcome_unknown",
                        error={
                            "code": -32000,
                            "message": str(exc),
                            "data": {"idempotent": is_idempotent, "capability": capability},
                        },
                        finished_at=datetime.now(UTC),
                    )
                except TimeoutError as exc:
                    await self.op_repo.update_status(
                        operation.id,
                        owner_id=owner_id,
                        status="timeout",
                        error={"code": -32003, "message": str(exc)},
                        finished_at=datetime.now(UTC),
                    )
                except DeviceOfflineError as exc:
                    await self.op_repo.update_status(
                        operation.id,
                        owner_id=owner_id,
                        status="failed",
                        error={"code": -32001, "message": str(exc)},
                        finished_at=datetime.now(UTC),
                    )
                except Exception as exc:
                    await self.op_repo.update_status(
                        operation.id,
                        owner_id=owner_id,
                        status="failed",
                        error={"code": -32603, "message": str(exc)},
                        finished_at=datetime.now(UTC),
                    )
                await self.session.commit()
            elif getattr(broker, "is_cluster_mode", False):
                # Cluster mode: publish to bus and await remote worker pickup
                await broker.publish_dispatch(
                    operation.id,
                    owner_id,
                    device_id,
                    capability,
                    params=params,
                    timeout_seconds=timeout_seconds,
                )
                import asyncio

                deadline = asyncio.get_event_loop().time() + min(float(timeout_seconds), 5.0)
                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(0.05)
                    await self.session.refresh(operation)
                    if operation.status in ("completed", "failed", "timeout", "outcome_unknown"):
                        break
                if operation.status == "pending":
                    await self.op_repo.update_status(
                        operation.id,
                        owner_id=owner_id,
                        status="failed",
                        error={"code": -32001, "message": "Device is offline across cluster"},
                        finished_at=datetime.now(UTC),
                    )
                    await self.session.commit()
            else:
                await self.op_repo.update_status(
                    operation.id,
                    owner_id=owner_id,
                    status="failed",
                    error={"code": -32001, "message": "Device is offline"},
                    finished_at=now,
                )
                await self.session.commit()

        return operation

    async def retry_operation(
        self,
        owner_id: str,
        device_id: str,
        operation_id: str,
        *,
        force: bool = False,
        broker: Any | None = None,
    ) -> DeviceOperation:
        """Safely retry an operation in failed, timeout, or outcome_unknown state."""
        device = await self.repo.get_by_device_id(device_id, owner_id=owner_id)
        if not device:
            raise ServiceNotFound("Device", device_id)
        if device.revoked_at is not None:
            raise DeviceAuthError("Device has been revoked")

        op = await self.op_repo.get(operation_id, owner_id=owner_id)
        if not op or op.device_id != device_id:
            raise ServiceNotFound("DeviceOperation", operation_id)

        if op.status not in ("failed", "timeout", "outcome_unknown"):
            raise ValueError(f"Operation in status '{op.status}' cannot be retried")

        if op.status == "outcome_unknown":
            is_idempotent = IDEMPOTENT_CAPABILITIES.get(op.capability, False)
            if not is_idempotent and not force:
                raise ServiceConflictError(
                    "device_operation",
                    operation_id,
                    op.revision,
                )

        # Reset to pending
        await self.op_repo.reset_for_retry(operation_id, owner_id)
        await self.session.commit()

        # Re-dispatch if broker is available and device online
        if broker is not None and broker.is_online(owner_id, device_id):
            claimed = await self.op_repo.claim_pending(
                owner_id, device_id, operation_id=operation_id
            )
            if claimed:
                await self.session.commit()
                is_idempotent = IDEMPOTENT_CAPABILITIES.get(op.capability, False)
                try:
                    rpc_resp = await broker.dispatch_rpc(
                        owner_id,
                        device_id,
                        op.capability,
                        op.params or {},
                        timeout_seconds=float(op.timeout_seconds or 15),
                    )
                    finish_time = datetime.now(UTC)
                    if rpc_resp.error:
                        await self.op_repo.update_status(
                            operation_id,
                            owner_id=owner_id,
                            status="failed",
                            error=rpc_resp.error.model_dump(),
                            finished_at=finish_time,
                        )
                    else:
                        await self.op_repo.update_status(
                            operation_id,
                            owner_id=owner_id,
                            status="completed",
                            result=rpc_resp.result,
                            finished_at=finish_time,
                        )
                except AmbiguousOutcomeError as exc:
                    await self.op_repo.update_status(
                        operation_id,
                        owner_id=owner_id,
                        status="outcome_unknown",
                        error={
                            "code": -32000,
                            "message": str(exc),
                            "data": {"idempotent": is_idempotent, "capability": op.capability},
                        },
                        finished_at=datetime.now(UTC),
                    )
                except TimeoutError as exc:
                    await self.op_repo.update_status(
                        operation_id,
                        owner_id=owner_id,
                        status="timeout",
                        error={"code": -32003, "message": str(exc)},
                        finished_at=datetime.now(UTC),
                    )
                except Exception as exc:
                    await self.op_repo.update_status(
                        operation_id,
                        owner_id=owner_id,
                        status="failed",
                        error={"code": -32603, "message": str(exc)},
                        finished_at=datetime.now(UTC),
                    )
                await self.session.commit()

        return op

    async def get_operation(
        self, owner_id: str, device_id: str, operation_id: str
    ) -> DeviceOperation:
        """Retrieve operation for an owned device."""
        op = await self.op_repo.get(operation_id, owner_id=owner_id)
        if not op or op.device_id != device_id:
            raise ServiceNotFound("DeviceOperation", operation_id)
        return op

    async def list_operations(
        self,
        owner_id: str,
        device_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[DeviceOperation]:
        """List operations executed on an owned device."""
        return await self.op_repo.list_for_device(
            owner_id=owner_id,
            device_id=device_id,
            status=status,
            limit=limit,
            offset=offset,
        )


def build_device_service(session: AsyncSession, settings: Any) -> DeviceService:
    """Create a device service without permitting staging fallback secrets."""
    local_mode = getattr(settings, "environment", "production") in ("local", "test")
    secret = getattr(settings, "companion_jwt_secret", None)
    if not secret:
        if not local_mode:
            raise DeviceAuthError("Companion device authentication is not configured")
        secret = "personal-os-device-secret-key-local-dev-only"
    return DeviceService(
        session,
        secret=secret,
        allow_synthetic_tokens=local_mode and getattr(settings, "allow_synthetic_auth", False),
    )
