"""Tests for CompanionSettings configuration (M2-A)."""

from pathlib import Path

from personal_os.companion.config import CompanionSettings


def test_companion_settings_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERSONAL_OS_COMPANION_CONFIG_DIR", str(tmp_path / "comp"))
    monkeypatch.setenv("PERSONAL_OS_COMPANION_AUTHORIZED_ROOTS", f'["{tmp_path / "root"}"]')

    settings = CompanionSettings()
    assert settings.config_dir == (tmp_path / "comp").resolve()
    assert (tmp_path / "root").resolve() in settings.authorized_roots
    assert settings.heartbeat_interval_seconds == 15
    assert settings.reconnect_min_backoff == 1.0
    assert settings.reconnect_max_backoff == 30.0
    assert settings.credentials_path == (tmp_path / "comp").resolve() / "credentials.json"


def test_companion_settings_env_overrides(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERSONAL_OS_COMPANION_DEVICE_NAME", "TestRig")
    monkeypatch.setenv("PERSONAL_OS_COMPANION_HEARTBEAT_INTERVAL_SECONDS", "25")
    monkeypatch.setenv("PERSONAL_OS_COMPANION_CLOUD_URL", "https://api.example.com")
    monkeypatch.setenv("PERSONAL_OS_COMPANION_CONFIG_DIR", str(tmp_path / "custom"))

    settings = CompanionSettings()
    assert settings.device_name == "TestRig"
    assert settings.heartbeat_interval_seconds == 25
    assert settings.cloud_url == "https://api.example.com"
    assert settings.config_dir == (tmp_path / "custom").resolve()
