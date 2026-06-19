from __future__ import annotations

import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ichannel.android_tv import AndroidTVController  # noqa: E402


class CannotConnect(Exception):
    pass


class ConnectionClosed(Exception):
    pass


class InvalidAuth(Exception):
    pass


class FakeRemote:
    instances: list["FakeRemote"] = []
    power_state = True
    power_key_changes_state = True
    update_instance_on_power_command = True
    connect_failures_remaining = 0
    connect_failures_after_power_command = 0

    def __init__(
        self,
        client_name: str,
        certfile: str,
        keyfile: str,
        host: str,
        *,
        enable_voice: bool,
    ) -> None:
        self.client_name = client_name
        self.certfile = certfile
        self.keyfile = keyfile
        self.host = host
        self.enable_voice = enable_voice
        self.connect_calls = 0
        self.keep_reconnecting_calls = 0
        self.disconnect_calls = 0
        self.key_commands: list[str] = []
        self.is_on = FakeRemote.power_state
        self._availability_callbacks = []
        self._is_on_callbacks = []
        self._callbacks = self._availability_callbacks
        FakeRemote.instances.append(self)

    def add_is_available_updated_callback(self, callback) -> None:
        self._availability_callbacks.append(callback)

    def add_is_on_updated_callback(self, callback) -> None:
        self._is_on_callbacks.append(callback)

    async def async_generate_cert_if_missing(self) -> bool:
        return False

    async def async_connect(self) -> None:
        self.connect_calls += 1
        if FakeRemote.connect_failures_remaining:
            FakeRemote.connect_failures_remaining -= 1
            raise CannotConnect("temporary failure")
        self.is_on = FakeRemote.power_state
        for callback in self._is_on_callbacks:
            callback(self.is_on)

    def keep_reconnecting(self, invalid_auth_callback=None) -> None:
        self.keep_reconnecting_calls += 1

    def send_key_command(self, key: str) -> None:
        self.key_commands.append(key)
        if not FakeRemote.power_key_changes_state:
            return
        if key == "POWER":
            next_power_state = not FakeRemote.power_state
        else:
            return

        FakeRemote.power_state = next_power_state
        FakeRemote.connect_failures_remaining += FakeRemote.connect_failures_after_power_command
        if FakeRemote.update_instance_on_power_command:
            self.is_on = FakeRemote.power_state
            for callback in self._is_on_callbacks:
                callback(self.is_on)

    def disconnect(self) -> None:
        self.disconnect_calls += 1


def fake_androidtvremote2_module() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        AndroidTVRemote=FakeRemote,
        CannotConnect=CannotConnect,
        ConnectionClosed=ConnectionClosed,
        InvalidAuth=InvalidAuth,
    )


class AndroidTVControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeRemote.instances.clear()
        FakeRemote.power_state = True
        FakeRemote.power_key_changes_state = True
        FakeRemote.update_instance_on_power_command = True
        FakeRemote.connect_failures_remaining = 0
        FakeRemote.connect_failures_after_power_command = 0

    async def test_reuses_available_connection_for_multiple_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AndroidTVController(
                host="192.0.2.10",
                certfile=Path(temp_dir) / "cert.pem",
                keyfile=Path(temp_dir) / "key.pem",
                client_name="iChangeChannels",
                power_timeout_seconds=0.1,
                logger=logging.getLogger("test"),
            )

            with patch.dict(sys.modules, {"androidtvremote2": fake_androidtvremote2_module()}):
                await controller.send_key("DPAD_UP")
                await controller.send_key("DPAD_DOWN")

        remote = FakeRemote.instances[0]
        self.assertEqual(remote.connect_calls, 1)
        self.assertEqual(remote.keep_reconnecting_calls, 1)
        self.assertEqual(remote.key_commands, ["DPAD_UP", "DPAD_DOWN"])

    async def test_reconnects_after_availability_callback_reports_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AndroidTVController(
                host="192.0.2.10",
                certfile=Path(temp_dir) / "cert.pem",
                keyfile=Path(temp_dir) / "key.pem",
                client_name="iChangeChannels",
                power_timeout_seconds=0.1,
                logger=logging.getLogger("test"),
            )

            with patch.dict(sys.modules, {"androidtvremote2": fake_androidtvremote2_module()}):
                await controller.connect()
                FakeRemote.instances[0]._callbacks[0](False)
                await controller.connect()

        remote = FakeRemote.instances[0]
        self.assertEqual(remote.connect_calls, 2)
        self.assertEqual(remote.keep_reconnecting_calls, 1)

    async def test_refresh_power_cycle_sends_power_waits_then_power(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AndroidTVController(
                host="192.0.2.10",
                certfile=Path(temp_dir) / "cert.pem",
                keyfile=Path(temp_dir) / "key.pem",
                client_name="iChangeChannels",
                power_timeout_seconds=0.1,
                logger=logging.getLogger("test"),
            )

            sleep = AsyncMock()
            with (
                patch.dict(sys.modules, {"androidtvremote2": fake_androidtvremote2_module()}),
                patch("ichannel.android_tv.asyncio.sleep", sleep),
            ):
                await controller.refresh_power_cycle()

        key_commands = [
            command
            for remote in FakeRemote.instances
            for command in remote.key_commands
        ]
        self.assertTrue(FakeRemote.power_state)
        self.assertEqual(key_commands, ["POWER", "POWER"])
        self.assertEqual(len(FakeRemote.instances), 1)
        sleep.assert_awaited_once_with(5.0)

    async def test_refresh_power_cycle_does_not_wait_for_reported_state(self) -> None:
        FakeRemote.power_key_changes_state = False

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AndroidTVController(
                host="192.0.2.10",
                certfile=Path(temp_dir) / "cert.pem",
                keyfile=Path(temp_dir) / "key.pem",
                client_name="iChangeChannels",
                power_timeout_seconds=0.1,
                logger=logging.getLogger("test"),
            )

            with (
                patch.dict(sys.modules, {"androidtvremote2": fake_androidtvremote2_module()}),
                patch("ichannel.android_tv.asyncio.sleep", AsyncMock()),
            ):
                await controller.refresh_power_cycle()

        key_commands = [
            command
            for remote in FakeRemote.instances
            for command in remote.key_commands
        ]
        self.assertTrue(FakeRemote.power_state)
        self.assertEqual(key_commands, ["POWER", "POWER"])

    async def test_ensure_on_sends_power_when_remote_reports_off(self) -> None:
        FakeRemote.power_state = False

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AndroidTVController(
                host="192.0.2.10",
                certfile=Path(temp_dir) / "cert.pem",
                keyfile=Path(temp_dir) / "key.pem",
                client_name="iChangeChannels",
                power_timeout_seconds=0.01,
                logger=logging.getLogger("test"),
            )

            with patch.dict(sys.modules, {"androidtvremote2": fake_androidtvremote2_module()}):
                await controller.ensure_on()

        key_commands = [
            command
            for remote in FakeRemote.instances
            for command in remote.key_commands
        ]
        self.assertTrue(FakeRemote.power_state)
        self.assertEqual(key_commands, ["POWER"])

    async def test_ensure_off_sends_power_when_remote_reports_on(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AndroidTVController(
                host="192.0.2.10",
                certfile=Path(temp_dir) / "cert.pem",
                keyfile=Path(temp_dir) / "key.pem",
                client_name="iChangeChannels",
                power_timeout_seconds=0.2,
                logger=logging.getLogger("test"),
            )

            with patch.dict(sys.modules, {"androidtvremote2": fake_androidtvremote2_module()}):
                await controller.ensure_off()

        key_commands = [
            command
            for remote in FakeRemote.instances
            for command in remote.key_commands
        ]
        self.assertFalse(FakeRemote.power_state)
        self.assertEqual(key_commands, ["POWER"])

    async def test_ensure_off_does_not_fail_when_confirmation_times_out(self) -> None:
        FakeRemote.power_key_changes_state = False

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AndroidTVController(
                host="192.0.2.10",
                certfile=Path(temp_dir) / "cert.pem",
                keyfile=Path(temp_dir) / "key.pem",
                client_name="iChangeChannels",
                power_timeout_seconds=0.2,
                logger=logging.getLogger("test"),
            )

            with patch.dict(sys.modules, {"androidtvremote2": fake_androidtvremote2_module()}):
                with self.assertLogs("test", level="WARNING"):
                    result = await controller.ensure_off()

        key_commands = [
            command
            for remote in FakeRemote.instances
            for command in remote.key_commands
        ]
        self.assertTrue(result)
        self.assertTrue(FakeRemote.power_state)
        self.assertEqual(key_commands, ["POWER"])


if __name__ == "__main__":
    unittest.main()
