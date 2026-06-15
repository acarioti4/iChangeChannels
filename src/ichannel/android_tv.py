from __future__ import annotations

import asyncio
import logging
from pathlib import Path


class AndroidTVError(RuntimeError):
    """Base Android TV control error."""


class AndroidTVPairingRequired(AndroidTVError):
    """Raised when the TV rejects the stored certificate/key pair."""


class AndroidTVController:
    def __init__(
        self,
        host: str,
        certfile: Path,
        keyfile: Path,
        client_name: str,
        power_timeout_seconds: float,
        logger: logging.Logger,
    ) -> None:
        self.host = host
        self.certfile = certfile
        self.keyfile = keyfile
        self.client_name = client_name
        self.power_timeout_seconds = power_timeout_seconds
        self.logger = logger
        self._remote = None
        self._connect_lock = asyncio.Lock()
        self._is_available = False
        self._reconnect_started = False
        self._auth_failed = False

    def _on_is_available_updated(self, is_available: bool) -> None:
        self._is_available = is_available
        self.logger.info("Android TV availability changed: %s", is_available)

    def _on_invalid_auth(self) -> None:
        self._auth_failed = True
        self._is_available = False
        self.logger.error("Android TV rejected stored certificate/key during reconnect")

    async def connect(self) -> None:
        from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth

        if self._auth_failed:
            raise AndroidTVPairingRequired(
                "Android TV rejected the stored certificate/key. Re-run pairing."
            )

        async with self._connect_lock:
            if self._auth_failed:
                raise AndroidTVPairingRequired(
                    "Android TV rejected the stored certificate/key. Re-run pairing."
                )

            if self._remote is None:
                self.certfile.parent.mkdir(parents=True, exist_ok=True)
                self.keyfile.parent.mkdir(parents=True, exist_ok=True)
                self._remote = AndroidTVRemote(
                    self.client_name,
                    str(self.certfile),
                    str(self.keyfile),
                    self.host,
                    enable_voice=False,
                )
                self._remote.add_is_available_updated_callback(self._on_is_available_updated)
                generated = await self._remote.async_generate_cert_if_missing()
                if generated:
                    raise AndroidTVPairingRequired(
                        "Generated a new Android TV certificate. Run scripts\\pair_android_tv.py "
                        "to pair this client with the TV before starting the bot."
                    )

            if self._is_available:
                return

            try:
                await self._remote.async_connect()
                self._is_available = True
            except InvalidAuth as exc:
                self._auth_failed = True
                self._is_available = False
                raise AndroidTVPairingRequired(
                    "Android TV rejected the stored certificate/key. Re-run pairing."
                ) from exc
            except (CannotConnect, ConnectionClosed) as exc:
                self._is_available = False
                raise AndroidTVError(f"Cannot connect to Android TV at {self.host}") from exc

            if not self._reconnect_started:
                self._remote.keep_reconnecting(self._on_invalid_auth)
                self._reconnect_started = True

    async def is_on(self) -> bool:
        await self.connect()
        return bool(getattr(self._remote, "is_on", False))

    async def ensure_on(self) -> bool:
        await self.connect()
        if bool(getattr(self._remote, "is_on", False)):
            self.logger.info("Android TV is already on")
            return True

        self.logger.info("Android TV is off or asleep; sending POWER")
        await self._send_key_command("POWER")

        deadline = asyncio.get_running_loop().time() + self.power_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.5)
            if bool(getattr(self._remote, "is_on", False)):
                self.logger.info("Android TV reported on")
                return True

        raise AndroidTVError("Android TV did not report powered on before timeout")

    async def send_key(self, key: str) -> None:
        self.logger.info("Android TV key: %s", key)
        await self._send_key_command(key)

    async def _send_key_command(self, key: str) -> None:
        from androidtvremote2 import ConnectionClosed

        for attempt in range(2):
            await self.connect()
            try:
                self._remote.send_key_command(key)
                return
            except ConnectionClosed as exc:
                self._is_available = False
                if attempt == 0:
                    self.logger.warning("Android TV connection closed while sending %s; retrying", key)
                    continue
                raise AndroidTVError("Android TV connection closed while sending key") from exc
            except ValueError as exc:
                raise AndroidTVError(f"Unsupported Android TV key: {key}") from exc
