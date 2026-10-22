"""Tests for Devices REST API endpoints (M2-B)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_device_registration_and_get(auth_client: AsyncClient):
    """Register device returns 201 with auth token and device details."""
    payload = {
        "device_id": "dev_laptop_alpha",
        "name": "ThinkPad Carbon",
        "platform": "linux",
        "capabilities": {
            "process.list": True,
            "filesystem.discover": True,
        },
    }
    res = await auth_client.post("/api/v1/devices", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["device_id"] == "dev_laptop_alpha"
    assert data["name"] == "ThinkPad Carbon"
    assert data["platform"] == "linux"
    assert data["status"] == "online"
    assert "token" in data
    assert data["revision"] == 1

    # Get device by ID
    get_res = await auth_client.get(f"/api/v1/devices/{data['device_id']}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == data["id"]

    # List devices
    list_res = await auth_client.get("/api/v1/devices")
    assert list_res.status_code == 200
    assert list_res.json()["total"] >= 1


@pytest.mark.asyncio
async def test_device_update_and_revision_conflict(auth_client: AsyncClient):
    """Updating device with If-Match revision header; conflict returns 409."""
    res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_update_me", "name": "Initial Name"},
    )
    assert res.status_code == 201
    dev = res.json()

    # Valid update with If-Match: 1
    upd = await auth_client.patch(
        f"/api/v1/devices/{dev['device_id']}",
        json={"name": "New Name"},
        headers={"If-Match": "1"},
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "New Name"
    assert upd.json()["revision"] == 2

    # Stale update with If-Match: 1 returns 409
    conflict = await auth_client.patch(
        f"/api/v1/devices/{dev['device_id']}",
        json={"name": "Conflicting Name"},
        headers={"If-Match": "1"},
    )
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_device_heartbeat_and_revoke(auth_client: AsyncClient):
    """Device heartbeat reports presence; revocation sets device to offline."""
    res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_pulse_api", "name": "Pulse API Rig"},
    )
    assert res.status_code == 201
    dev_id = res.json()["device_id"]

    # Heartbeat
    hb_res = await auth_client.post(
        f"/api/v1/devices/{dev_id}/heartbeat",
        json={"status": "online"},
    )
    assert hb_res.status_code == 200
    assert hb_res.json()["status"] == "online"

    # Revoke
    rev_res = await auth_client.post(f"/api/v1/devices/{dev_id}/revoke")
    assert rev_res.status_code == 204

    # Verify revoked
    get_res = await auth_client.get(f"/api/v1/devices/{dev_id}")
    assert get_res.status_code == 200
    assert get_res.json()["revoked_at"] is not None
    assert get_res.json()["status"] == "offline"


@pytest.mark.asyncio
async def test_cross_user_isolation(
    auth_client: AsyncClient, other_client: AsyncClient
):
    """User B cannot see, modify, or hijack User A's registered device."""
    # Alice registers device
    reg_alice = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_alice_exclusive", "name": "Alice Device"},
    )
    assert reg_alice.status_code == 201

    # Bob attempts to get Alice's device -> 404
    get_bob = await other_client.get("/api/v1/devices/dev_alice_exclusive")
    assert get_bob.status_code == 404

    # Bob attempts to update Alice's device -> 404
    upd_bob = await other_client.patch(
        "/api/v1/devices/dev_alice_exclusive",
        json={"name": "Hacked Device"},
    )
    assert upd_bob.status_code == 404

    # Bob attempts to revoke Alice's device -> 404
    rev_bob = await other_client.post("/api/v1/devices/dev_alice_exclusive/revoke")
    assert rev_bob.status_code == 404

    # Bob attempts to register Alice's device_id -> 409 Conflict
    conflict_bob = await other_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_alice_exclusive", "name": "Bob Fake Device"},
    )
    assert conflict_bob.status_code == 409


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(unauth_client: AsyncClient):
    """Unauthenticated access to devices API returns 401."""
    res = await unauth_client.get("/api/v1/devices")
    assert res.status_code == 401
