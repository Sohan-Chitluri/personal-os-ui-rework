"""Local Companion Runtime Daemon (M2-A).

Lifecycle:
- Validated configuration via CompanionSettings
- Outbound-only WebSocket client connection with bounded exponential backoff and jitter
- Authentication handshake and capability negotiation
- Periodic heartbeat loop and presence reporting
- Dispatches server requests to RPCDispatcher
- Clean cancellation and resource cleanup
- Diagnostic logging with credentials/secrets excluded
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import signal
from datetime import UTC, datetime
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from personal_os.companion.config import CompanionSettings
from personal_os.companion.discovery import ProjectDiscoveryService
from personal_os.companion.identity import DeviceIdentityManager, IdentityError
from personal_os.companion.process import ProcessInspector
from personal_os.companion.protocol import (
    DEFAULT_CAPABILITIES,
    PROTOCOL_VERSION,
    HandshakeParams,
    HandshakeResult,
    HeartbeatParams,
    PresenceState,
    RPCRequest,
    RPCResponse,
)
from personal_os.companion.rpc import RPCDispatcher

log = logging.getLogger("personal_os.companion.daemon")


class CompanionDaemon:
    """Outbound local companion daemon."""

    def __init__(self, settings: CompanionSettings | None = None) -> None:
        self.settings = settings or CompanionSettings()
        self.identity = DeviceIdentityManager(self.settings)
        self.discovery = ProjectDiscoveryService(self.settings.authorized_roots)
        self.process = ProcessInspector()
        self.dispatcher = RPCDispatcher(self.discovery, self.process)

        self._running = False
        self._ws: Any = None
        self._current_backoff = self.settings.reconnect_min_backoff
        self._presence: PresenceState = PresenceState.OFFLINE
        self._device_token: str | None = None
        self._session_id: str | None = None
        self._tasks: list[asyncio.Task] = []

    @property
    def presence(self) -> PresenceState:
        return self._presence

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the companion daemon and enter the connection loop."""
        self._running = True
        self._device_id = self.identity.load_or_create_device_id()
        log.info(
            "Companion daemon starting: device_id=%s name=%s roots=%s",
            self._device_id,
            self.settings.device_name,
            [str(r) for r in self.settings.authorized_roots],
        )

        while self._running:
            try:
                # Ensure device token is available
                if not self._device_token:
                    try:
                        self._device_token = await self.identity.get_or_register_token(
                            capabilities={k: v.model_dump() for k, v in DEFAULT_CAPABILITIES.items()}
                        )
                    except IdentityError as exc:
                        log.warning("Authentication/Registration pending: %s", exc)
                        await self._sleep_with_backoff()
                        continue

                await self._run_connection_lifecycle()

            except asyncio.CancelledError:
                log.info("Companion connection loop cancelled")
                break
            except Exception as exc:
                log.error("Companion unexpected error in connection loop: %s", exc, exc_info=True)
                await self._sleep_with_backoff()

        await self.stop()

    async def _run_connection_lifecycle(self) -> None:
        """Connect to cloud WebSocket, perform handshake, and handle messages."""
        self._presence = PresenceState.CONNECTING
        ws_url = self.settings.ws_url

        log.info("Connecting to cloud session broker: %s", ws_url)
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=None,  # We manage our own application-level heartbeats
                close_timeout=5,
            ) as ws:
                self._ws = ws
                log.info("WebSocket transport connected; sending handshake")

                # Perform handshake
                handshake_ok = await self._perform_handshake(ws)
                if not handshake_ok:
                    self._presence = PresenceState.OFFLINE
                    await self._sleep_with_backoff()
                    return

                # Handshake successful; reset backoff
                self._current_backoff = self.settings.reconnect_min_backoff
                self._presence = PresenceState.ONLINE

                # Launch heartbeat task
                heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                self._tasks.append(heartbeat_task)

                try:
                    await self._receive_loop(ws)
                finally:
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)

        except (ConnectionRefusedError, OSError, WebSocketException) as exc:
            log.warning("Connection to %s failed: %s", ws_url, exc)
            self._presence = PresenceState.OFFLINE
            await self._sleep_with_backoff()

    async def _perform_handshake(self, ws: Any) -> bool:
        """Send auth.handshake request and await server confirmation."""
        handshake_params = HandshakeParams(
            protocol_version=PROTOCOL_VERSION,
            device_id=self._device_id,
            token=self._device_token or "",
            client_version="0.1.0",
            platform=self.settings.platform,
            supported_capabilities=list(DEFAULT_CAPABILITIES.keys()),
        )
        req = RPCRequest(method="auth.handshake", params=handshake_params.model_dump())
        await ws.send(req.model_dump_json())

        try:
            raw_response = await asyncio.wait_for(ws.recv(), timeout=10.0)
            data = json.loads(raw_response)
            resp = RPCResponse.model_validate(data)

            if resp.error:
                log.error(
                    "Handshake rejected by server: code=%s message=%s",
                    resp.error.code,
                    resp.error.message,
                )
                if resp.error.data and resp.error.data.get("revoked"):
                    log.warning("Device credentials reported as revoked. Clearing local credentials.")
                    self.identity.clear_credentials()
                    self._device_token = None
                return False

            if not resp.result or not resp.result.get("authenticated"):
                log.error("Handshake response missing authenticated confirmation: %s", resp.result)
                return False

            handshake_result = HandshakeResult.model_validate(resp.result)
            self._session_id = handshake_result.session_id
            log.info(
                "Handshake successful: session_id=%s granted=%s",
                self._session_id,
                handshake_result.granted_capabilities,
            )
            return True

        except TimeoutError:
            log.error("Handshake timed out waiting for server response")
            return False
        except Exception as exc:
            log.error("Failed to parse handshake response: %s", exc)
            return False

    async def _heartbeat_loop(self, ws: Any) -> None:
        """Periodically report device presence and metrics."""
        log.info("Heartbeat loop started (interval: %ss)", self.settings.heartbeat_interval_seconds)
        while self._running:
            try:
                await asyncio.sleep(self.settings.heartbeat_interval_seconds)
                hb_params = HeartbeatParams(
                    device_id=self._device_id,
                    timestamp=datetime.now(UTC),
                    status=self._presence,
                )
                req = RPCRequest(method="device.heartbeat", params=hb_params.model_dump(mode="json"))
                await ws.send(req.model_dump_json())
                log.debug("Heartbeat dispatched")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("Heartbeat send error: %s", exc)
                break

    async def _receive_loop(self, ws: Any) -> None:
        """Process incoming messages from session broker."""
        while self._running:
            try:
                raw_text = await ws.recv()
            except ConnectionClosed as exc:
                log.info("Connection closed by server: %s", exc)
                break

            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                log.warning("Received invalid JSON from server: %s", exc)
                continue

            # Check if this is an incoming RPC request from server
            if isinstance(data, dict) and "method" in data:
                try:
                    req = RPCRequest.model_validate(data)
                except Exception as exc:
                    log.warning("Malformed RPC request: %s", exc)
                    continue

                # Ignore heartbeat acknowledgements if treated as request
                if req.method == "device.heartbeat":
                    continue

                log.info("Received RPC request from server: method=%s id=%s", req.method, req.id)
                response = await self.dispatcher.dispatch(req)
                await ws.send(response.model_dump_json())

    async def _sleep_with_backoff(self) -> None:
        """Sleep with exponential backoff and jitter."""
        jitter_factor = 1.0 + (random.uniform(-self.settings.reconnect_jitter, self.settings.reconnect_jitter))
        sleep_seconds = self._current_backoff * jitter_factor
        log.info("Reconnecting in %.2fs (backoff: %.1fs)...", sleep_seconds, self._current_backoff)
        await asyncio.sleep(sleep_seconds)
        self._current_backoff = min(self._current_backoff * 2.0, self.settings.reconnect_max_backoff)

    async def stop(self) -> None:
        """Clean shutdown and resource release."""
        log.info("Companion daemon stopping")
        self._running = False
        self._presence = PresenceState.OFFLINE

        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        log.info("Companion daemon stopped cleanly")


def main(args: list[str] | None = None) -> None:
    """CLI entry point for running the companion daemon or management tasks."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="pos-companion",
        description="Personal OS Local Companion Daemon and CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Run daemon (default)
    run_parser = subparsers.add_parser("run", help="Run companion daemon in foreground")
    run_parser.add_argument("--config-dir", type=str, help="Custom config directory")
    run_parser.add_argument("--cloud-url", type=str, help="Override cloud URL")
    run_parser.add_argument("--ws-url", type=str, help="Override WebSocket URL")
    run_parser.add_argument("--log-level", type=str, default=None, help="Log level (INFO, DEBUG, etc.)")

    # Register with cloud
    reg_parser = subparsers.add_parser("register", help="Register device with cloud using user token")
    reg_parser.add_argument("--user-token", required=True, type=str, help="User Bearer token")
    reg_parser.add_argument("--cloud-url", type=str, help="Cloud API URL")
    reg_parser.add_argument("--name", type=str, help="Custom device name")
    reg_parser.add_argument("--config-dir", type=str, help="Custom config directory")

    # Status
    stat_parser = subparsers.add_parser("status", help="Show companion configuration and identity status")
    stat_parser.add_argument("--config-dir", type=str, help="Custom config directory")

    # Revoke
    rev_parser = subparsers.add_parser("revoke", help="Clear local device credentials")
    rev_parser.add_argument("--config-dir", type=str, help="Custom config directory")

    parsed = parser.parse_args(args)
    cmd = parsed.command or "run"

    # Base settings with CLI overrides
    overrides: dict[str, Any] = {}
    if getattr(parsed, "config_dir", None):
        overrides["config_dir"] = Path(parsed.config_dir)
    if getattr(parsed, "cloud_url", None):
        overrides["cloud_url"] = parsed.cloud_url
    if getattr(parsed, "ws_url", None):
        overrides["ws_url"] = parsed.ws_url
    if getattr(parsed, "name", None):
        overrides["device_name"] = parsed.name
    if getattr(parsed, "log_level", None):
        overrides["log_level"] = parsed.log_level

    settings = CompanionSettings(**overrides)

    if cmd == "status":
        identity = DeviceIdentityManager(settings)
        creds = identity.load_credentials()
        print(f"Device ID:        {identity.load_or_create_device_id()}")
        print(f"Device Name:      {settings.device_name}")
        print(f"Platform:         {settings.platform}")
        print(f"Cloud URL:        {settings.cloud_url}")
        print(f"WebSocket URL:    {settings.ws_url}")
        print(f"Config Directory: {settings.config_dir}")
        print(f"Credentials File: {settings.credentials_path} ({'present' if creds else 'not found'})")
        print(f"Authorized Roots: {', '.join(str(r) for r in settings.authorized_roots)}")
        if creds and creds.get("token"):
            print("Status:           Paired and registered with cloud")
        else:
            print("Status:           Unpaired (run 'pos-companion register --user-token <token>')")
        return

    if cmd == "revoke":
        identity = DeviceIdentityManager(settings)
        identity.clear_credentials()
        print(f"Credentials cleared for device at {settings.credentials_path}")
        return

    if cmd == "register":
        identity = DeviceIdentityManager(settings)
        print(f"Registering device '{settings.device_name}' with {settings.cloud_url}...")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            token = loop.run_until_complete(
                identity.register_with_cloud(
                    user_bearer_token=parsed.user_token,
                    capabilities={k: v.model_dump() for k, v in DEFAULT_CAPABILITIES.items()},
                )
            )
            loop.close()
            print(f"Registration successful! Device paired.")
            print(f"Credentials saved to {settings.credentials_path} (mode 0600)")
        except Exception as exc:
            print(f"Registration failed: {exc}")
            raise SystemExit(1) from exc
        return

    # Run command
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    daemon = CompanionDaemon(settings)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.stop()))
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(daemon.stop())
        loop.close()


if __name__ == "__main__":
    main()

