from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ichannel.bot import IChangeChannelsBot, REMOTE_UNLOCKED_MESSAGE, RemoteView  # noqa: E402
from ichannel.orchestrator import PowerResult  # noqa: E402


class FakeCoordinator:
    def __init__(self) -> None:
        self.remaining_seconds: int | None = 300
        self.lease_owner_id: int | None = 1
        self.admin_user_ids: set[int] = set()
        self.claim_calls: list[tuple[int, str, int | None, bool]] = []
        self.take_control_calls: list[tuple[int, int | None]] = []

    def claim_remote_ui(
        self,
        *,
        user_id: int,
        username: str,
        guild_id: int | None,
        force: bool = False,
    ) -> str | None:
        self.claim_calls.append((user_id, username, guild_id, force))
        if (
            self.lease_owner_id is not None
            and self.lease_owner_id != user_id
            and self.remaining_seconds is not None
            and not force
        ):
            return "owner currently has the remote."

        self.lease_owner_id = user_id
        self.remaining_seconds = 300
        return None

    def remote_control_lease_remaining_seconds(self, *, user_id: int) -> int | None:
        if self.lease_owner_id is not None and self.lease_owner_id != user_id:
            return None
        return self.remaining_seconds

    def remote_control_lockout_message(self) -> str | None:
        if self.remaining_seconds is None or self.lease_owner_id is None:
            return None
        return "owner currently has the remote."

    def remote_control_block_reason(self, guild: object) -> None:
        return None

    def is_remote_admin(self, user: object) -> bool:
        return getattr(user, "id", None) in self.admin_user_ids

    def take_remote_control(self, *, user: object, guild_id: int | None) -> str:
        self.take_control_calls.append((getattr(user, "id"), guild_id))
        self.lease_owner_id = getattr(user, "id")
        self.remaining_seconds = 300
        return "Took control of the remote from owner."

    async def status(self) -> PowerResult:
        return PowerResult(
            True,
            "Status:\n- Android Tv On: OK",
            {"android_tv_on": True},
        )

    async def send_tv_key(
        self,
        *,
        action: str,
        key: str,
        user_id: int,
        guild_id: int | None,
    ) -> PowerResult:
        return PowerResult(True, f"Sent {key}.", {"android_tv_key_sent": True})


class FakeResponse:
    def __init__(self) -> None:
        self.deferred_kwargs: dict[str, object] | None = None
        self.edits: list[dict[str, object]] = []
        self.messages: list[dict[str, object]] = []

    async def defer(self, **kwargs: object) -> None:
        self.deferred_kwargs = kwargs

    async def edit_message(self, **kwargs: object) -> None:
        self.edits.append(kwargs)

    async def send_message(self, *args: object, **kwargs: object) -> None:
        self.messages.append({"args": args, "kwargs": kwargs})


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, *args: object, **kwargs: object) -> None:
        self.messages.append({"args": args, "kwargs": kwargs})


class FakeUser:
    def __init__(self, user_id: int, name: str = "owner") -> None:
        self.id = user_id
        self.name = name

    def __str__(self) -> str:
        return self.name


class FakeInteraction:
    def __init__(self, *, user_id: int = 1, username: str = "owner") -> None:
        self.user = FakeUser(user_id, username)
        self.guild = SimpleNamespace(id=10)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.original_response_edits: list[dict[str, object]] = []

    async def edit_original_response(self, **kwargs: object) -> None:
        self.original_response_edits.append(kwargs)


class BotStartupTests(unittest.TestCase):
    def test_startup_sync_does_not_clear_guild_commands(self) -> None:
        class FakeTree:
            def __init__(self) -> None:
                self.sync_calls: list[object | None] = []

            async def sync(self, *, guild: object | None = None) -> list[object]:
                self.sync_calls.append(guild)
                return [object()]

            def clear_commands(self, *, guild: object) -> None:
                raise AssertionError("startup should not clear guild commands")

        async def run_sync() -> list[object | None]:
            tree = FakeTree()
            bot = SimpleNamespace(
                tree=tree,
                logger=SimpleNamespace(info=lambda *args, **kwargs: None),
                guilds=[SimpleNamespace(id=10)],
            )

            await IChangeChannelsBot._sync_global_commands_only(bot)  # type: ignore[arg-type]
            return tree.sync_calls

        self.assertEqual(asyncio.run(run_sync()), [None])


class RemoteViewTests(unittest.TestCase):
    def test_remote_view_includes_refresh_button(self) -> None:
        view = RemoteView(FakeCoordinator(), owner_id=1)  # type: ignore[arg-type]

        refresh_buttons = [
            item
            for item in view.children
            if getattr(item, "label", None) == "Refresh"
        ]

        self.assertEqual(len(refresh_buttons), 1)
        self.assertEqual(refresh_buttons[0].action, "refresh_tv")

    def test_remote_view_includes_take_control_button_for_admin(self) -> None:
        view = RemoteView(
            FakeCoordinator(),  # type: ignore[arg-type]
            owner_id=1,
            is_admin=True,
        )

        take_control_buttons = [
            item
            for item in view.children
            if getattr(item, "label", None) == "Take Control"
        ]

        self.assertEqual(len(take_control_buttons), 1)
        self.assertEqual(take_control_buttons[0].action, "take_control")

    def test_remote_content_includes_lease_countdown(self) -> None:
        view = RemoteView(FakeCoordinator(), owner_id=1)  # type: ignore[arg-type]

        content = view.content()

        self.assertIn("Remote is yours for 5m 0s", content)

    def test_admin_remote_content_hides_lock_state_until_take_control(self) -> None:
        coordinator = FakeCoordinator()
        view = RemoteView(
            coordinator,  # type: ignore[arg-type]
            owner_id=2,
            is_admin=True,
        )

        content = view.content()

        self.assertNotIn("owner currently has the remote", content)
        self.assertNotIn("Remote is yours", content)
        self.assertNotIn("Remote unlocks", content)
        self.assertNotIn(REMOTE_UNLOCKED_MESSAGE, content)
        self.assertFalse(
            any(
                getattr(item, "disabled", False)
                for item in view.children
                if getattr(item, "label", None) != "Nav"
            )
        )

    def test_locked_out_remote_view_disables_buttons(self) -> None:
        view = RemoteView(FakeCoordinator(), owner_id=2)  # type: ignore[arg-type]

        content = view.content()

        self.assertIn("owner currently has the remote", content)
        self.assertTrue(all(getattr(item, "disabled", False) for item in view.children))

    def test_remote_countdown_updates_message_content(self) -> None:
        async def run_countdown() -> list[tuple[str, RemoteView | None]]:
            coordinator = FakeCoordinator()
            view = RemoteView(coordinator, owner_id=1)  # type: ignore[arg-type]
            edits: list[tuple[str, RemoteView | None]] = []

            async def edit(content: str, edited_view: RemoteView | None) -> None:
                edits.append((content, edited_view))

            view.start_countdown(edit, interval_seconds=0.01)
            coordinator.remaining_seconds = 299
            await asyncio.sleep(0.05)
            view.stop_countdown()
            return edits

        edits = asyncio.run(run_countdown())

        self.assertTrue(
            any("Remote is yours for 4m 59s" in edit[0] for edit in edits),
            edits,
        )

    def test_remote_countdown_shows_unlocked_when_lease_expires(self) -> None:
        async def run_countdown() -> tuple[RemoteView, list[tuple[str, RemoteView | None]]]:
            coordinator = FakeCoordinator()
            view = RemoteView(coordinator, owner_id=1)  # type: ignore[arg-type]
            edits: list[tuple[str, RemoteView | None]] = []

            async def edit(content: str, edited_view: RemoteView | None) -> None:
                edits.append((content, edited_view))

            view.start_countdown(edit, interval_seconds=0.01)
            coordinator.remaining_seconds = None
            await asyncio.sleep(0.05)
            view.stop_countdown()
            return view, edits

        view, edits = asyncio.run(run_countdown())

        self.assertTrue(
            any(
                REMOTE_UNLOCKED_MESSAGE in content and edited_view is view
                for content, edited_view in edits
            ),
            edits,
        )
        self.assertFalse(any(edited_view is None for _, edited_view in edits))

    def test_locked_out_countdown_enables_buttons_when_lease_expires(self) -> None:
        async def run_countdown() -> tuple[
            RemoteView,
            list[tuple[str, RemoteView | None]],
        ]:
            coordinator = FakeCoordinator()
            view = RemoteView(coordinator, owner_id=2)  # type: ignore[arg-type]
            edits: list[tuple[str, RemoteView | None]] = []

            async def edit(content: str, edited_view: RemoteView | None) -> None:
                edits.append((content, edited_view))

            view.start_countdown(edit, interval_seconds=0.01)
            coordinator.remaining_seconds = None
            coordinator.lease_owner_id = None
            await asyncio.sleep(0.05)
            view.stop_countdown()
            return view, edits

        view, edits = asyncio.run(run_countdown())

        power_on = next(item for item in view.children if getattr(item, "label", None) == "Power On")
        self.assertFalse(power_on.disabled)
        self.assertTrue(
            any(
                REMOTE_UNLOCKED_MESSAGE in content and edited_view is view
                for content, edited_view in edits
            ),
            edits,
        )

    def test_remote_command_sends_locked_view_for_non_admin(self) -> None:
        async def run_command() -> dict[str, object]:
            coordinator = FakeCoordinator()
            interaction = FakeInteraction(user_id=2, username="next")
            bot = SimpleNamespace(coordinator=coordinator)

            await IChangeChannelsBot.remote_command(bot, interaction)  # type: ignore[arg-type]
            message = interaction.response.messages[0]
            view = message["kwargs"]["view"]
            assert isinstance(view, RemoteView)
            view.stop_countdown()
            return message

        message = asyncio.run(run_command())
        view = message["kwargs"]["view"]

        self.assertIsInstance(view, RemoteView)
        self.assertTrue(message["kwargs"]["ephemeral"])
        self.assertIn("owner currently has the remote", str(message["args"][0]))
        self.assertTrue(all(getattr(item, "disabled", False) for item in view.children))

    def test_remote_command_admin_bypasses_lock_without_claiming(self) -> None:
        async def run_command() -> tuple[FakeCoordinator, dict[str, object]]:
            coordinator = FakeCoordinator()
            coordinator.admin_user_ids.add(2)
            interaction = FakeInteraction(user_id=2, username="admin")
            bot = SimpleNamespace(coordinator=coordinator)

            await IChangeChannelsBot.remote_command(bot, interaction)  # type: ignore[arg-type]
            message = interaction.response.messages[0]
            view = message["kwargs"]["view"]
            assert isinstance(view, RemoteView)
            view.stop_countdown()
            return coordinator, message

        coordinator, message = asyncio.run(run_command())
        view = message["kwargs"]["view"]

        self.assertIsInstance(view, RemoteView)
        self.assertTrue(message["kwargs"]["ephemeral"])
        self.assertEqual(coordinator.claim_calls, [])
        self.assertNotIn("owner currently has the remote", str(message["args"][0]))
        self.assertNotIn("Remote is yours", str(message["args"][0]))
        self.assertFalse(
            any(
                getattr(item, "disabled", False)
                for item in view.children
                if getattr(item, "label", None) != "Nav"
            )
        )

    def test_status_updates_remote_message_instead_of_followup(self) -> None:
        view = RemoteView(FakeCoordinator(), owner_id=1)  # type: ignore[arg-type]
        interaction = FakeInteraction()

        asyncio.run(view.handle_action(interaction, "status"))  # type: ignore[arg-type]

        self.assertEqual(interaction.response.deferred_kwargs, {})
        self.assertEqual(interaction.followup.messages, [])
        self.assertEqual(len(interaction.original_response_edits), 1)
        edit = interaction.original_response_edits[0]
        self.assertIn("iChangeChannels remote - Nav", edit["content"])
        self.assertIn("Status:", edit["content"])
        self.assertIsInstance(edit["view"], RemoteView)

    def test_take_control_updates_remote_message_for_admin(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.admin_user_ids.add(2)
        view = RemoteView(
            coordinator,  # type: ignore[arg-type]
            owner_id=2,
            is_admin=True,
        )
        interaction = FakeInteraction(user_id=2, username="admin")

        asyncio.run(view.handle_action(interaction, "take_control"))  # type: ignore[arg-type]

        self.assertEqual(coordinator.take_control_calls, [(2, 10)])
        self.assertEqual(len(interaction.response.edits), 1)
        edit = interaction.response.edits[0]
        self.assertIn("Took control of the remote from owner.", edit["content"])
        self.assertIn("Remote is yours for 5m 0s", edit["content"])

    def test_admin_button_press_bypasses_lock_without_claiming(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.admin_user_ids.add(2)
        view = RemoteView(
            coordinator,  # type: ignore[arg-type]
            owner_id=2,
            is_admin=True,
        )
        interaction = FakeInteraction(user_id=2, username="admin")

        asyncio.run(view.handle_action(interaction, "status"))  # type: ignore[arg-type]

        self.assertEqual(coordinator.claim_calls, [])
        self.assertEqual(interaction.response.deferred_kwargs, {})
        self.assertEqual(len(interaction.original_response_edits), 1)
        edit = interaction.original_response_edits[0]
        self.assertIn("Status:", edit["content"])
        self.assertNotIn("owner currently has the remote", edit["content"])
        self.assertNotIn("Remote is yours", edit["content"])

    def test_key_press_updates_remote_message_instead_of_followup(self) -> None:
        view = RemoteView(FakeCoordinator(), owner_id=1)  # type: ignore[arg-type]
        interaction = FakeInteraction()

        asyncio.run(view.handle_action(interaction, "up"))  # type: ignore[arg-type]

        self.assertEqual(interaction.response.deferred_kwargs, {})
        self.assertEqual(interaction.followup.messages, [])
        self.assertEqual(len(interaction.original_response_edits), 1)
        edit = interaction.original_response_edits[0]
        self.assertIn("OK: Sent DPAD_UP.", edit["content"])
        self.assertNotIn("Android Tv Key Sent", edit["content"])


if __name__ == "__main__":
    unittest.main()
