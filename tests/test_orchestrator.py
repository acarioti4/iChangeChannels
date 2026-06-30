from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ichannel.android_tv import AndroidTVError  # noqa: E402
from ichannel.config import AppConfig  # noqa: E402
from ichannel.discord_desktop import DesktopAutomationError  # noqa: E402
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


class FakeUser:
    def __init__(self, user_id: int, name: str) -> None:
        self.id = user_id
        self.name = name

    def __str__(self) -> str:
        return self.name


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


class FakeTV:
    def __init__(self) -> None:
        self.ensure_off_calls = 0
        self.ensure_off_error: Exception | None = None
        self.refresh_power_cycle_calls = 0
        self.refresh_power_cycle_error: Exception | None = None

    async def ensure_off(self) -> bool:
        self.ensure_off_calls += 1
        if self.ensure_off_error is not None:
            raise self.ensure_off_error
        return True

    async def refresh_power_cycle(self) -> None:
        self.refresh_power_cycle_calls += 1
        if self.refresh_power_cycle_error is not None:
            raise self.refresh_power_cycle_error


class FakeDesktop:
    def __init__(self, stream_member: FakeMember) -> None:
        self.stream_member = stream_member
        self.start_vlc_stream_calls = 0

    async def start_vlc_stream(self) -> None:
        self.start_vlc_stream_calls += 1
        self.stream_member.voice.self_stream = True
        raise DesktopAutomationError("Go Live button disappeared")


def make_config(
    state_file: Path,
    *,
    desktop_automation_enabled: bool = False,
    discord_admin_user_ids: frozenset[int] = frozenset(),
    discord_admin_usernames: frozenset[str] = frozenset(),
) -> AppConfig:
    return AppConfig(
        discord_token="token",
        stream_user_id=STREAM_USER_ID,
        stream_username="stream-account",
        discord_admin_user_ids=discord_admin_user_ids,
        discord_admin_usernames=discord_admin_usernames,
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
        log_file=state_file.parent / "ichannel.log",
        state_file=state_file,
        command_sync_on_start=False,
        android_tv_power_timeout_seconds=1,
        discord_join_timeout_seconds=1,
        discord_stream_timeout_seconds=1,
        vlc_start_timeout_seconds=1,
        desktop_automation_enabled=desktop_automation_enabled,
        remote_key_rate_per_second=10,
        remote_key_burst=5,
        remote_number_rate_per_second=6,
        remote_number_burst=3,
        remote_key_queue_limit=5,
    )


class PowerCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def build_coordinator(
        self,
        guilds: list[FakeGuild],
        state_file: Path,
        *,
        desktop_automation_enabled: bool = False,
        discord_admin_user_ids: frozenset[int] = frozenset(),
        discord_admin_usernames: frozenset[str] = frozenset(),
    ) -> PowerCoordinator:
        coordinator = PowerCoordinator(
            FakeBot(guilds),
            make_config(
                state_file,
                desktop_automation_enabled=desktop_automation_enabled,
                discord_admin_user_ids=discord_admin_user_ids,
                discord_admin_usernames=discord_admin_usernames,
            ),
        )  # type: ignore[arg-type]
        coordinator.tv = FakeTV()  # type: ignore[assignment]
        return coordinator

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
            tv_off_calls = coordinator.tv.ensure_off_calls  # type: ignore[attr-defined]

        self.assertTrue(result.ok)
        self.assertEqual(
            stream_member.move_to_calls,
            [(None, "iChangeChannels Power Off")],
        )
        self.assertEqual(
            result.checks,
            {"stream_account_disconnected": True, "android_tv_off": True},
        )
        self.assertEqual(tv_off_calls, 1)

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

    async def test_power_off_reports_android_tv_failure_after_disconnect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stream_member = FakeMember(STREAM_USER_ID, "stream-account")
            channel = FakeChannel(100, "Movies", [stream_member])
            guild = FakeGuild(200, "Active Guild", [channel])
            coordinator = self.build_coordinator([guild], Path(temp_dir) / "state.json")
            coordinator.tv.ensure_off_error = AndroidTVError("still reports on")  # type: ignore[attr-defined]
            interaction = SimpleNamespace(user="requester", guild=guild)

            result = await coordinator.power_off(interaction)  # type: ignore[arg-type]

        self.assertFalse(result.ok)
        self.assertEqual(
            stream_member.move_to_calls,
            [(None, "iChangeChannels Power Off")],
        )
        self.assertEqual(
            result.checks,
            {"stream_account_disconnected": True, "android_tv_off": False},
        )
        self.assertIn("power-off failed", result.message)

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
            tv_off_calls = coordinator.tv.ensure_off_calls  # type: ignore[attr-defined]

        self.assertEqual(
            stream_member.move_to_calls,
            [(None, "iChangeChannels Auto Power Off: stream account alone")],
        )
        self.assertIsNone(lock_reason)
        self.assertEqual(tv_off_calls, 1)

    async def test_remote_lockout_message_does_not_include_countdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self.build_coordinator([], Path(temp_dir) / "state.json")

            self.assertIsNone(
                coordinator.claim_remote_ui(user_id=1, username="owner", guild_id=200)
            )
            lock_reason = coordinator.claim_remote_ui(
                user_id=2, username="next", guild_id=200
            )

        self.assertIsNotNone(lock_reason)
        message = str(lock_reason)
        self.assertEqual(message, "owner currently has the remote.")

    async def test_admin_can_take_remote_control_from_standard_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self.build_coordinator(
                [],
                Path(temp_dir) / "state.json",
                discord_admin_user_ids=frozenset({99}),
            )
            admin = FakeUser(99, "admin")

            self.assertIsNone(
                coordinator.claim_remote_ui(user_id=1, username="owner", guild_id=200)
            )
            message = coordinator.take_remote_control(user=admin, guild_id=200)  # type: ignore[arg-type]
            lock_reason = coordinator.claim_remote_ui(
                user_id=2,
                username="next",
                guild_id=200,
            )

        self.assertEqual(message, "Took control of the remote from owner.")
        self.assertIsNotNone(lock_reason)
        self.assertIn("admin currently has the remote", str(lock_reason))

    async def test_admin_detection_accepts_configured_username(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self.build_coordinator(
                [],
                Path(temp_dir) / "state.json",
                discord_admin_usernames=frozenset({"adminuser"}),
            )

            is_admin = coordinator.is_remote_admin(FakeUser(99, "AdminUser"))  # type: ignore[arg-type]

        self.assertTrue(is_admin)

    async def test_non_admin_cannot_take_remote_control(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = self.build_coordinator([], Path(temp_dir) / "state.json")
            user = FakeUser(99, "not-admin")

            self.assertIsNone(
                coordinator.claim_remote_ui(user_id=1, username="owner", guild_id=200)
            )
            message = coordinator.take_remote_control(user=user, guild_id=200)  # type: ignore[arg-type]
            lock_reason = coordinator.claim_remote_ui(
                user_id=2,
                username="next",
                guild_id=200,
            )

        self.assertEqual(message, "Only configured admins can take control of the remote.")
        self.assertIsNotNone(lock_reason)
        self.assertIn("owner currently has the remote", str(lock_reason))

    async def test_manual_stream_disconnect_does_not_power_off_tv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stream_member = FakeMember(STREAM_USER_ID, "stream-account")
            channel = FakeChannel(100, "Movies", [])
            guild = FakeGuild(200, "Active Guild", [channel])
            coordinator = self.build_coordinator([guild], Path(temp_dir) / "state.json")
            before = SimpleNamespace(channel=channel, self_stream=True)
            after = SimpleNamespace(channel=None, self_stream=False)

            await coordinator.note_stream_voice_update(stream_member, before, after)  # type: ignore[arg-type]
            tv_off_calls = coordinator.tv.ensure_off_calls  # type: ignore[attr-defined]

        self.assertEqual(stream_member.move_to_calls, [])
        self.assertEqual(tv_off_calls, 0)

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

    async def test_refresh_tv_box_power_cycles_tv_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            channel = FakeChannel(100, "Movies", [])
            guild = FakeGuild(200, "Active Guild", [channel])
            coordinator = self.build_coordinator([guild], Path(temp_dir) / "state.json")
            interaction = SimpleNamespace(user="requester", guild=guild)

            result = await coordinator.refresh_tv_box(interaction)  # type: ignore[arg-type]
            refresh_power_cycle_calls = coordinator.tv.refresh_power_cycle_calls  # type: ignore[attr-defined]

        self.assertTrue(result.ok)
        self.assertEqual(refresh_power_cycle_calls, 1)
        self.assertEqual(
            result.checks,
            {"android_tv_refresh_completed": True},
        )

    async def test_refresh_tv_box_reports_android_tv_failure_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            channel = FakeChannel(100, "Movies", [])
            guild = FakeGuild(200, "Active Guild", [channel])
            coordinator = self.build_coordinator([guild], Path(temp_dir) / "state.json")
            coordinator.tv.refresh_power_cycle_error = AndroidTVError("POWER did not send")  # type: ignore[attr-defined]
            interaction = SimpleNamespace(user="requester", guild=guild)

            with self.assertLogs("ichannel.power", level="ERROR"):
                result = await coordinator.refresh_tv_box(interaction)  # type: ignore[arg-type]

        self.assertFalse(result.ok)
        self.assertIn("POWER did not send", result.message)
        self.assertEqual(
            result.checks,
            {"android_tv_refresh_completed": False},
        )

    async def test_ensure_streaming_accepts_discord_live_state_after_desktop_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stream_member = FakeMember(STREAM_USER_ID, "stream-account")
            stream_member.voice.self_stream = False
            channel = FakeChannel(100, "Movies", [stream_member])
            guild = FakeGuild(200, "Active Guild", [channel])
            coordinator = self.build_coordinator(
                [guild],
                Path(temp_dir) / "state.json",
                desktop_automation_enabled=True,
            )
            desktop = FakeDesktop(stream_member)
            coordinator.desktop = desktop  # type: ignore[assignment]
            session = SimpleNamespace(join_url="https://discord.com/channels/200/100")

            result = await coordinator._ensure_streaming(session, channel)  # type: ignore[arg-type]

        self.assertTrue(result.ok)
        self.assertEqual(desktop.start_vlc_stream_calls, 1)
        self.assertEqual(
            result.checks,
            {"stream_account_in_channel": True, "stream_active": True},
        )
        self.assertIn("started streaming", result.message)


if __name__ == "__main__":
    unittest.main()
