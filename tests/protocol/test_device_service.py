"""Tests for DeviceService domain logic and presence tracking (M2-B)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.companion.protocol import PresenceState
from personal_os.db.models import User
from personal_os.services.devices import DeviceAuthError, DeviceService
from personal_os.services.interfaces import ServiceConflictError, ServiceNotFound

TEST_DEVICE_SECRET = "protocol-device-test-secret-32-chars-long-123456"


@pytest.mark.asyncio
async def test_device_token_create_and_verify(proto_session: AsyncSession):
    service = DeviceService(proto_session, secret=TEST_DEVICE_SECRET)
    token = service.create_device_token(
        device_id="dev_123",
        user_id="user_abc",
        capabilities={"fs": True},
    )
    assert isinstance(token, str)

    claims = service.verify_device_token(token)
    assert claims["device_id"] == "dev_123"
    assert claims["user_id"] == "user_abc"
    assert claims["capabilities"] == {"fs": True}
    assert claims["token_use"] == "device"


@pytest.mark.asyncio
async def test_device_token_invalid_or_expired_rejected(proto_session: AsyncSession):
    service = DeviceService(proto_session, secret=TEST_DEVICE_SECRET)

    # Invalid secret
    other_service = DeviceService(proto_session, secret="different-secret-also-32-chars-long-456")
    token = other_service.create_device_token(device_id="dev_123", user_id="u1")
    with pytest.raises(DeviceAuthError, match="Invalid or expired"):
        service.verify_device_token(token)

    # Expired token
    expired_token = service.create_device_token(
        device_id="dev_123",
        user_id="u1",
        expires_delta=timedelta(seconds=-10),
    )
    with pytest.raises(DeviceAuthError, match="Invalid or expired"):
        service.verify_device_token(expired_token)


@pytest.mark.asyncio
async def test_synthetic_device_token_support(proto_session: AsyncSession):
    service = DeviceService(proto_session, secret=TEST_DEVICE_SECRET, allow_synthetic_tokens=True)
    claims = service.verify_device_token("device:dev_xyz:user_123")
    assert claims["device_id"] == "dev_xyz"
    assert claims["user_id"] == "user_123"
    assert claims["token_use"] == "device"


@pytest.mark.asyncio
async def test_synthetic_device_token_rejected_by_default(proto_session: AsyncSession):
    service = DeviceService(proto_session, secret=TEST_DEVICE_SECRET)
    with pytest.raises(DeviceAuthError, match="Invalid or expired"):
        service.verify_device_token("device:dev_xyz:user_123")


@pytest.mark.asyncio
async def test_register_device_and_idempotent_update(
    proto_session: AsyncSession, test_user: User
):
    service = DeviceService(proto_session, secret=TEST_DEVICE_SECRET)
    device, token = await service.register_device(
        owner_id=test_user.id,
        device_id="dev_lap_1",
        name="Laptop Alpha",
        platform="linux",
        capabilities={"system.health": True},
    )
    assert device.device_id == "dev_lap_1"
    assert device.name == "Laptop Alpha"
    assert device.status == "online"
    assert token is not None

    # Re-registering with same owner updates name and capabilities (idempotent)
    updated, new_token = await service.register_device(
        owner_id=test_user.id,
        device_id="dev_lap_1",
        name="Laptop Renamed",
        platform="linux",
        capabilities={"process.list": True},
    )
    assert updated.id == device.id
    assert updated.name == "Laptop Renamed"
    assert updated.capabilities == {"process.list": True}


@pytest.mark.asyncio
async def test_cross_user_device_registration_conflict(
    proto_session: AsyncSession, test_user: User, other_user: User
):
    """User B cannot register a device_id already registered to User A."""
    service = DeviceService(proto_session, secret=TEST_DEVICE_SECRET)
    await service.register_device(
        owner_id=test_user.id,
        device_id="dev_exclusive",
        name="Alice's Rig",
    )

    with pytest.raises(ServiceConflictError) as exc_info:
        await service.register_device(
            owner_id=other_user.id,
            device_id="dev_exclusive",
            name="Bob's Rig",
        )
    assert exc_info.value.entity == "device"
    assert exc_info.value.entity_id == "dev_exclusive"


@pytest.mark.asyncio
async def test_device_heartbeat_and_revocation(
    proto_session: AsyncSession, test_user: User
):
    service = DeviceService(proto_session, secret=TEST_DEVICE_SECRET)
    device, _ = await service.register_device(
        owner_id=test_user.id,
        device_id="dev_pulse",
        name="Pulse Test",
    )

    now = datetime.now(UTC)
    hb_dev = await service.record_heartbeat(
        device_id=device.device_id,
        owner_id=test_user.id,
        status="online",
        timestamp=now,
    )
    assert hb_dev.status == "online"

    # Revoke device
    await service.revoke_device(device.device_id, owner_id=test_user.id)
    revoked = await service.get_device(device.device_id, owner_id=test_user.id)
    assert revoked.revoked_at is not None
    assert revoked.status == "offline"

    # Heartbeat on revoked device fails
    with pytest.raises(ServiceNotFound, match="revoked"):
        await service.record_heartbeat(device.device_id, owner_id=test_user.id)


@pytest.mark.asyncio
async def test_evaluate_presence_and_stale_sweep(
    proto_session: AsyncSession, test_user: User
):
    service = DeviceService(proto_session, secret=TEST_DEVICE_SECRET)
    device, _ = await service.register_device(
        owner_id=test_user.id,
        device_id="dev_stale_check",
        name="Stale Test",
    )

    # Currently fresh -> ONLINE
    presence = service.evaluate_presence(device, stale_timeout_seconds=60)
    assert presence == PresenceState.ONLINE

    # Simulate old heartbeat (> 60s ago)
    old_time = datetime.now(UTC) - timedelta(seconds=120)
    await service.repo.update_heartbeat(
        device.device_id, test_user.id, status="online", timestamp=old_time
    )
    stale_dev = await service.get_device(device.device_id, owner_id=test_user.id)

    presence = service.evaluate_presence(stale_dev, stale_timeout_seconds=60)
    assert presence == PresenceState.STALE

    # Sweep marks status in DB to 'stale'
    swept = await service.sweep_stale_devices(stale_timeout_seconds=60)
    assert swept >= 1
    reloaded = await service.get_device(device.device_id, owner_id=test_user.id)
    assert reloaded.status == "stale"
