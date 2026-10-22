"""Local device identity and secure credential storage (M2-A).

Ensures:
- Stable local device identity that survives process restarts.
- Restrictive filesystem permissions (0700 dir, 0600 file).
- Explicit registration and revocation handling.
- Secure local storage without credentials committed to git.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx

from personal_os.companion.config import CompanionSettings

log = logging.getLogger("personal_os.companion.identity")


class IdentityError(Exception):
    """Raised on device identity or registration failure."""


class DeviceIdentityManager:
    """Manages the lifecycle and secure persistence of device credentials."""

    def __init__(self, settings: CompanionSettings) -> None:
        self.settings = settings
        self.config_dir = settings.config_dir
        self.credentials_path = settings.credentials_path

    def _ensure_secure_dir(self) -> None:
        """Create directory with 0700 permissions if not present."""
        if not self.config_dir.exists():
            self.config_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        else:
            try:
                os.chmod(self.config_dir, stat.S_IRWXU)
            except OSError as exc:
                log.warning("Could not set 0700 permissions on %s: %s", self.config_dir, exc)

    def load_or_create_device_id(self) -> str:
        """Return stable device ID from credentials file, or generate and persist a new one."""
        if self.settings.device_id:
            return self.settings.device_id

        creds = self.load_credentials()
        if creds and creds.get("device_id"):
            return str(creds["device_id"])

        new_id = f"dev_{uuid4().hex[:16]}"
        self.save_credentials({"device_id": new_id, "created_at": datetime.now(UTC).isoformat()})
        return new_id

    def load_credentials(self) -> dict[str, Any] | None:
        """Load stored credentials from file with safety checks."""
        if not self.credentials_path.exists():
            return None

        # Verify file permissions
        try:
            mode = stat.S_IMODE(self.credentials_path.stat().st_mode)
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                log.warning(
                    "Credentials file %s has overly permissive permissions (%o). Restricting to 0600.",
                    self.credentials_path,
                    mode,
                )
                os.chmod(self.credentials_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            log.warning("Could not check/chmod credentials file: %s", exc)

        try:
            with open(self.credentials_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return cast(dict[str, Any], data) if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Failed to read credentials file: %s", exc)
            return None

    def save_credentials(self, data: dict[str, Any]) -> None:
        """Write credentials with restrictive 0600 permissions."""
        self._ensure_secure_dir()
        existing = self.load_credentials() or {}
        merged = {**existing, **data}

        # Write to temporary file with 0600 then atomic replace
        temp_file = self.credentials_path.with_suffix(".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = stat.S_IRUSR | stat.S_IWUSR  # 0600
        fd = os.open(temp_file, flags, mode)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)

        os.replace(temp_file, self.credentials_path)
        try:
            os.chmod(self.credentials_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def clear_credentials(self) -> None:
        """Remove credentials upon explicit revocation."""
        if self.credentials_path.exists():
            try:
                self.credentials_path.unlink()
                log.info("Credentials file removed at %s", self.credentials_path)
            except OSError as exc:
                log.warning("Failed to delete credentials file: %s", exc)

    async def register_with_cloud(
        self,
        user_bearer_token: str | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> str:
        """Perform device registration via cloud REST API and persist credentials."""
        device_id = self.load_or_create_device_id()
        token_to_use = user_bearer_token or self.settings.user_token

        if not token_to_use:
            raise IdentityError(
                "Cannot register device: No user bearer token provided in settings or arguments"
            )

        reg_url = f"{self.settings.cloud_url.rstrip('/')}/api/v1/devices"
        payload = {
            "device_id": device_id,
            "name": self.settings.device_name,
            "platform": self.settings.platform,
            "capabilities": capabilities or {},
        }
        headers = {
            "Authorization": f"Bearer {token_to_use}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(reg_url, json=payload, headers=headers)
            except httpx.RequestError as exc:
                raise IdentityError(f"Cloud registration network request failed: {exc}") from exc

            if resp.status_code == 201:
                data = resp.json()
                raw_token = data.get("token")
                if not raw_token or not isinstance(raw_token, str):
                    raise IdentityError("Cloud registration response did not contain device token")
                device_token: str = raw_token

                self.save_credentials(
                    {
                        "device_id": device_id,
                        "device_name": self.settings.device_name,
                        "token": device_token,
                        "user_id": data.get("owner_id"),
                        "registered_at": data.get("registered_at"),
                    }
                )
                log.info("Device successfully registered with cloud: device_id=%s", device_id)
                return device_token

            raise IdentityError(
                f"Cloud registration returned HTTP {resp.status_code}: {resp.text}"
            )

    async def get_or_register_token(
        self, capabilities: dict[str, Any] | None = None
    ) -> str:
        """Return cached device token if available; otherwise register with cloud."""
        if self.settings.auth_token:
            return self.settings.auth_token

        creds = self.load_credentials()
        if creds and creds.get("token"):
            return str(creds["token"])

        if self.settings.user_token:
            return await self.register_with_cloud(capabilities=capabilities)

        raise IdentityError(
            "No device token found and no user_token configured for initial registration"
        )
