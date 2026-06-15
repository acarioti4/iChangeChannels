from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ichannel.config import AppConfig  # noqa: E402
from ichannel.orchestrator import PowerCoordinator  # noqa: E402


STREAM_USER_ID = 42


class FakeMember:
    def __init__(self, member_id: int, name: str) -> None:
        self.id = member_id
        self.name = name
        self.voice = SimpleNamespace(self_stream=True)
        self.move_to_calls: list[tuple[object | None, str | None]] = []

    def __str__(self) -> str:
        return self.name

    async def move_to(self, channel: object | None, *, reason: str | None = None) -> None:
        self.move_to_calls.append((channel, reason))


class FakeChannel:
    def __init__(self, channel_id: int, name: str, members: list[FakeMember]) -> None:
        self.id = channel_id
        self.name = name
        self.members = members


class FakeGuild:
    def __init__(self, guild_id: int, name: str, channels: list[FakeChannel]) -> None:
        self.id = guild_id
        self.name = name
        self.voice_channels = channels
        self.stage_channels: list[FakeChannel] = []


class FakeBot:
    def __init__(self, guilds: list[FakeGuild]) -> None:
        self.guilds = guilds


def make_config(state_file: Path) -> AppConfig:
    return AppConfig(
        discord_token="token",
        stream_user_id=STREAM_USER_ID,
        stream_username="stream-account",
        discord_dm_search="iChangeChannels",
        android_tv_host="192.0.2.10",
        android_tv_certfile=state_file.parent / "cert.pem",
        android_tv_keyfile=state_file.parent / "key.pem",
        android_tv_client_name="iChangeChannels",
        vlc_path="vlc",
        vlc_args=[],
        vlc_process_names=["vlc"],
        vlc_window_title="VLC media player",
        vlc_window_rect=None,
        vlc_window_show_cmd=None,
        data_dir=state_file.parent,
        log_file=state_file.parent / "ichannel.log",
        state_file=state_file,
        command_sync_on_start=False,
        sync_commands_to_guild_id=None,
        power_on_timeout_seconds=1,
        android_tv_power_timeout_seconds=1,
        discord_join_timeout_seconds=1,
        discord_stream_timeout_seconds=1,
        vlc_start_timeout_seconds=1,
        desktop_automation_enabled=False,
        remote_key_rate_per_second=10,
        remote_key_burst=5,
        remote_number_rate_per_second=6,
        remote_number_burst=3,
        remote_key_queue_limit=5,
    )


class PowerCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def build_coordinator(self, guilds: list[FakeGuild], state_file: Path) -> PowerCoordinator:
        return PowerCoordinator(FakeBot(guilds), make_config(state_file))  # type: ignore[arg-type]

    async def test_power_off_allows_other_server_when_stream_account_is_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stream_member = FakeMember(STREAM_USER_ID, "stream-account")
            channel = FakeChannel(100, "Movies", [stream_member])
            active_guild = FakeGuild(200, "Active Guild", [channel])
            other_guild = FakeGuild(300, "Other Guild", [])
            coordinator = self.build_coordinator(
                [active_guild, other_guild],
                Path(temp_dir) / "state.json",
            )
            interaction = SimpleNamespace(user="requester", guild=other_guild)

            result = await coordinator.power_off(interaction)  # type: ignore[arg-type]

        self.assertTrue(result.ok)
        self.assertEqual(
            stream_member.move_to_calls,
            [(None, "iChangeChannels Power Off")],
        )
        self.assertEqual(result.checks, {"stream_account_disconnected": True})

    async def test_power_off_blocks_other_server_when_stream_account_is_not_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stream_member = FakeMember(STREAM_USER_ID, "stream-account")
            viewer = FakeMember(99, "viewer")
            channel = FakeChannel(100, "Movies", [stream_member, viewer])
            active_guild = FakeGuild(200, "Active Guild", [channel])
            other_guild = FakeGuild(300, "Other Guild", [])
            coordinator = self.build_coordinator(
                [active_guild, other_guild],
                Path(temp_dir) / "state.json",
            )
            interaction = SimpleNamespace(user="requester", guild=other_guild)

            result = await coordinator.power_off(interaction)  # type: ignore[arg-type]

        self.assertFalse(result.ok)
        self.assertEqual(stream_member.move_to_calls, [])
        self.assertEqual(result.checks, {"stream_account_disconnected": False})

    async def test_voice_update_disconnects_stream_account_when_last_viewer_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stream_member = FakeMember(STREAM_USER_ID, "stream-account")
            viewer = FakeMember(99, "viewer")
            channel = FakeChannel(100, "Movies", [stream_member])
            guild = FakeGuild(200, "Active Guild", [channel])
            coordinator = self.build_coordinator([guild], Path(temp_dir) / "state.json")
            self.assertIsNone(
                coordinator.claim_remote_ui(user_id=1, username="owner", guild_id=200)
            )
            self.assertIsNotNone(
                coordinator.claim_remote_ui(user_id=2, username="next", guild_id=200)
            )
            before = SimpleNamespace(channel=channel, self_stream=False)
            after = SimpleNamespace(channel=None, self_stream=False)

            await coordinator.note_stream_voice_update(viewer, before, after)  # type: ignore[arg-type]
            lock_reason = coordinator.claim_remote_ui(user_id=2, username="next", guild_id=200)

        self.assertEqual(
            stream_member.move_to_calls,
            [(None, "iChangeChannels Auto Power Off: stream account alone")],
        )
        self.assertIsNone(lock_reason)

    async def test_voice_update_does_not_disconnect_when_stream_account_joins_empty_channel(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stream_member = FakeMember(STREAM_USER_ID, "stream-account")
            channel = FakeChannel(100, "Movies", [stream_member])
            guild = FakeGuild(200, "Active Guild", [channel])
            coordinator = self.build_coordinator([guild], Path(temp_dir) / "state.json")
            before = SimpleNamespace(channel=None, self_stream=False)
            after = SimpleNamespace(channel=channel, self_stream=True)

            await coordinator.note_stream_voice_update(stream_member, before, after)  # type: ignore[arg-type]

        self.assertEqual(stream_member.move_to_calls, [])


if __name__ == "__main__":
    unittest.main()
