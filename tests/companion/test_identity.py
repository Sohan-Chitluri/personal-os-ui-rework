"""Tests for DeviceIdentityManager and secure storage (M2-A)."""

import stat
from pathlib import Path

import httpx
import pytest

from personal_os.companion.config import CompanionSettings
from personal_os.companion.identity import DeviceIdentityManager, IdentityError


def test_device_id_stable_across_restarts(tmp_path: Path):
    """Device ID persists to disk and is identical across separate manager instances."""
    settings = CompanionSettings(config_dir=tmp_path / "companion")
    mgr1 = DeviceIdentityManager(settings)
    id1 = mgr1.load_or_create_device_id()
    assert id1.startswith("dev_")

    # Second instance pointing to same directory
    mgr2 = DeviceIdentityManager(settings)
    id2 = mgr2.load_or_create_device_id()
    assert id1 == id2


def test_credentials_file_permissions(tmp_path: Path):
    """Credentials file must have 0600 and directory 0700 permissions."""
    comp_dir = tmp_path / "companion"
    settings = CompanionSettings(config_dir=comp_dir)
    mgr = DeviceIdentityManager(settings)
    mgr.save_credentials({"token": "secret-jwt-token", "device_id": "dev_123"})

    # Check directory permissions
    dir_mode = stat.S_IMODE(comp_dir.stat().st_mode)
    assert dir_mode == 0o700, f"Expected 0700 dir mode, got {oct(dir_mode)}"

    # Check file permissions
    file_mode = stat.S_IMODE(settings.credentials_path.stat().st_mode)
    assert file_mode == 0o600, f"Expected 0600 file mode, got {oct(file_mode)}"

    # Verify content read
    creds = mgr.load_credentials()
    assert creds is not None
    assert creds["token"] == "secret-jwt-token"
    assert creds["device_id"] == "dev_123"


@pytest.mark.asyncio
async def test_cloud_registration_flow(tmp_path: Path, monkeypatch):
    """Test successful cloud registration and local credential persistence."""
    settings = CompanionSettings(
        config_dir=tmp_path / "companion",
        cloud_url="https://api.test.local",
        user_token="user-bearer-xyz",
        device_name="MyRig",
    )
    mgr = DeviceIdentityManager(settings)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.test.local/api/v1/devices"
        assert request.headers["Authorization"] == "Bearer user-bearer-xyz"
        return httpx.Response(
            201,
            json={
                "id": "01JABC12345678901234567890",
                "device_id": "dev_test_device",
                "owner_id": "user_456",
                "token": "issued-device-jwt-token",
                "registered_at": "2026-10-18T18:00:00Z",
            },
        )

    transport = httpx.MockTransport(mock_handler)
    orig_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: orig_async_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    token = await mgr.register_with_cloud()
    assert token == "issued-device-jwt-token"

    creds = mgr.load_credentials()
    assert creds is not None
    assert creds["token"] == "issued-device-jwt-token"


@pytest.mark.asyncio
async def test_registration_without_user_token_raises_error(tmp_path: Path):
    """Attempting registration without user token raises IdentityError."""
    settings = CompanionSettings(config_dir=tmp_path / "companion", user_token=None)
    mgr = DeviceIdentityManager(settings)

    with pytest.raises(IdentityError, match="No user bearer token"):
        await mgr.register_with_cloud()


def test_clear_credentials(tmp_path: Path):
    """Revoking or resetting clears credentials from disk."""
    settings = CompanionSettings(config_dir=tmp_path / "companion")
    mgr = DeviceIdentityManager(settings)
    mgr.save_credentials({"token": "old-token"})
    assert settings.credentials_path.exists()

    mgr.clear_credentials()
    assert not settings.credentials_path.exists()
