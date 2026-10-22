"""Companion daemon configuration (M2-A).

Uses pydantic-settings for validated local settings with environment variable
overrides (PERSONAL_OS_COMPANION_*).
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_config_dir() -> Path:
    override = os.environ.get("PERSONAL_OS_COMPANION_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".config" / "personal-os" / "companion").resolve()


def default_authorized_roots() -> list[Path]:
    projects_dir = Path.home() / "Projects"
    if projects_dir.exists():
        return [projects_dir.resolve()]
    return [Path.cwd().resolve()]


class CompanionSettings(BaseSettings):
    """Configuration for local personal-os companion daemon."""

    model_config = SettingsConfigDict(
        env_prefix="PERSONAL_OS_COMPANION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Device identity
    device_id: str | None = None
    device_name: str = Field(default_factory=socket.gethostname)
    platform: str = Field(default_factory=lambda: sys.platform)

    # Cloud endpoints
    cloud_url: str = "http://localhost:8000"
    ws_url: str = "ws://localhost:8000/ws/companion"

    # Credentials
    auth_token: str | None = None
    user_token: str | None = None

    # Paths and boundaries
    config_dir: Path = Field(default_factory=default_config_dir)
    authorized_roots: list[Path] = Field(default_factory=default_authorized_roots)

    # Timing & Backoff
    heartbeat_interval_seconds: float = Field(default=15.0, ge=0.05, le=300.0)
    reconnect_min_backoff: float = Field(default=1.0, ge=0.001, le=10.0)
    reconnect_max_backoff: float = Field(default=30.0, ge=0.01, le=300.0)
    reconnect_jitter: float = Field(default=0.2, ge=0.0, le=0.5)

    # Diagnostics
    log_level: str = "INFO"

    @property
    def credentials_path(self) -> Path:
        return self.config_dir / "credentials.json"

    @field_validator("authorized_roots", mode="after")
    @classmethod
    def resolve_roots(cls, roots: list[Path]) -> list[Path]:
        return [r.expanduser().resolve() for r in roots]

    @field_validator("config_dir", mode="after")
    @classmethod
    def resolve_config_dir(cls, d: Path) -> Path:
        return d.expanduser().resolve()
