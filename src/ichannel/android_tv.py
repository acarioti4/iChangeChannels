from __future__ import annotations

import asyncio
import logging
from pathlib import Path

REFRESH_POWER_CYCLE_WAIT_SECONDS = 5.0
POWER_STATE_POLL_SECONDS = 0.5
POWER_TOGGLE_KEY = "POWER"
POWER_OFF_KEY = "POWER"


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

    def _on_is_on_updated(self, is_on: bool) -> None:
        self.logger.info("Android TV power state changed: %s", "on" if is_on else "off")

    def _on_invalid_auth(self) -> None:
        self._auth_failed = True
        self._is_available = False
        self.logger.error("Android TV rejected stored certificate/key during reconnect")

    def reset_connection(self) -> None:
        if self._remote is not None:
            try:
                self._remote.disconnect()
            except Exception as exc:
                self.logger.warning("Android TV disconnect during refresh failed: %s", exc)
        self._remote = None
        self._is_available = False
        self._reconnect_started = False

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
                self._remote.add_is_on_updated_callback(self._on_is_on_updated)
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
        return await self.refresh_power_state()

    async def refresh_power_state(self) -> bool:
        self.reset_connection()
        await self.connect()
        return self._reported_power_state()

    async def ensure_on(self) -> bool:
        await self.connect()
        if self._reported_power_state():
            self.logger.info("Android TV is already on")
            return True

        self.logger.info("Android TV is off or asleep; sending %s", POWER_TOGGLE_KEY)
        await self._send_key_command(POWER_TOGGLE_KEY)
        try:
            await self.wait_for_power_state(True, phase="power on")
        except AndroidTVError as exc:
            self.logger.warning(
                "Android TV did not confirm powered on after %s was sent: %s",
                POWER_TOGGLE_KEY,
                exc,
            )
        else:
            self.logger.info("Android TV reported on")
        return True

    async def ensure_off(self) -> bool:
        await self.connect()
        if not self._reported_power_state():
            self.logger.info("Android TV is already off")
            return True

        self.logger.info("Android TV is on; sending %s", POWER_OFF_KEY)
        await self._send_key_command(POWER_OFF_KEY)
        try:
            await self.wait_for_power_state(False, phase="power off")
        except AndroidTVError as exc:
            self.logger.warning(
                "Android TV did not confirm powered off after %s was sent: %s",
                POWER_OFF_KEY,
                exc,
            )
        else:
            self.logger.info("Android TV reported off")
        return True

    async def refresh_power_cycle(self) -> None:
        self.logger.info("Android TV refresh: sending %s", POWER_TOGGLE_KEY)
        await self._send_key_command(POWER_TOGGLE_KEY)
        self.logger.info(
            "Android TV refresh: waiting %.1f seconds before second %s",
            REFRESH_POWER_CYCLE_WAIT_SECONDS,
            POWER_TOGGLE_KEY,
        )
        await asyncio.sleep(REFRESH_POWER_CYCLE_WAIT_SECONDS)
        self.logger.info("Android TV refresh: sending %s again", POWER_TOGGLE_KEY)
        await self._send_key_command(POWER_TOGGLE_KEY)
        self.logger.info("Android TV refresh power sequence complete")

    async def wait_for_power_state(self, expected: bool, *, phase: str) -> None:
        deadline = asyncio.get_running_loop().time() + self.power_timeout_seconds
        last_error: AndroidTVError | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                actual = self._reported_power_state()
            except AndroidTVPairingRequired:
                raise
            except AndroidTVError as exc:
                last_error = exc
                self.logger.info(
                    "Android TV power state read failed during %s; retrying: %s",
                    phase,
                    exc,
                )
            else:
                if actual is expected:
                    return
            await asyncio.sleep(self._power_state_poll_seconds())

        expected_label = "on" if expected else "off"
        message = f"Android TV did not report powered {expected_label} during {phase} before timeout"
        if last_error is not None:
            message = f"{message}; last refresh error: {last_error}"
        raise AndroidTVError(message)

    async def send_key(self, key: str) -> None:
        self.logger.info("Android TV key: %s", key)
        await self._send_key_command(key)

    def _reported_power_state(self) -> bool:
        state = getattr(self._remote, "is_on", None)
        if state is None:
            raise AndroidTVError("Android TV did not report a power state after reconnect")
        return bool(state)

    def _power_state_poll_seconds(self) -> float:
        return min(POWER_STATE_POLL_SECONDS, max(0.05, self.power_timeout_seconds / 4))

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
