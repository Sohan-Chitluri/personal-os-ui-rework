"""Tests for companion CLI entrypoint and commands (M2-F)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from personal_os.companion.daemon import main
from personal_os.companion.identity import DeviceIdentityManager


def test_cli_status_unpaired(tmp_path: Path, capsys):
    """CLI status displays device info and indicates unpaired status when no token present."""
    config_dir = tmp_path / "companion_conf"
    main(["status", "--config-dir", str(config_dir)])
    out = capsys.readouterr().out
    assert "Device ID:" in out
    assert "Unpaired" in out
    assert str(config_dir) in out


def test_cli_revoke_clears_credentials(tmp_path: Path, capsys):
    """CLI revoke removes local credentials.json."""
    config_dir = tmp_path / "companion_conf"
    config_dir.mkdir(parents=True, mode=0o700)
    creds_file = config_dir / "credentials.json"
    creds_file.write_text('{"device_id": "dev_test", "token": "tok_123"}')
    assert creds_file.exists()

    main(["revoke", "--config-dir", str(config_dir)])
    out = capsys.readouterr().out
    assert "Credentials cleared" in out
    assert not creds_file.exists()


def test_cli_register_success(tmp_path: Path, capsys):
    """CLI register invokes cloud registration and saves credentials."""
    config_dir = tmp_path / "companion_conf"

    with patch.object(
        DeviceIdentityManager,
        "register_with_cloud",
        new=AsyncMock(return_value="jwt_device_token_abc123"),
    ) as mock_reg:
        main(
            [
                "register",
                "--user-token",
                "user_bearer_token_xyz",
                "--config-dir",
                str(config_dir),
                "--cloud-url",
                "http://test-cloud:8000",
                "--name",
                "My Test Rig",
            ]
        )
        mock_reg.assert_called_once()
        out = capsys.readouterr().out
        assert "Registration successful!" in out
