from __future__ import annotations

import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.key_commands: list[str] = []
        self.is_on = True
        self._callbacks = []
        FakeRemote.instances.append(self)

    def add_is_available_updated_callback(self, callback) -> None:
        self._callbacks.append(callback)

    async def async_generate_cert_if_missing(self) -> bool:
        return False

    async def async_connect(self) -> None:
        self.connect_calls += 1

    def keep_reconnecting(self, invalid_auth_callback=None) -> None:
        self.keep_reconnecting_calls += 1

    def send_key_command(self, key: str) -> None:
        self.key_commands.append(key)


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


if __name__ == "__main__":
    unittest.main()
