from __future__ import annotations

import asyncio
import sys
import unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ichannel.bot import REMOTE_UNLOCKED_MESSAGE, RemoteView, _run_lockout_countdown  # noqa: E402
from ichannel.orchestrator import PowerResult  # noqa: E402


class FakeCoordinator:
    def __init__(self) -> None:
        self.remaining_seconds: int | None = 300

    def claim_remote_ui(self, *, user_id: int, username: str, guild_id: int | None) -> None:
        return None

    def remote_control_lease_remaining_seconds(self, *, user_id: int) -> int | None:
        return self.remaining_seconds

    def remote_control_lockout_message(self) -> str | None:
        if self.remaining_seconds is None:
            return None
        minutes, seconds = divmod(self.remaining_seconds, 60)
        return f"owner is using the remote right now. Remote unlocks in {minutes:02}:{seconds:02}."

    def remote_control_block_reason(self, guild: object) -> None:
        return None

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


class FakeInteraction:
    def __init__(self) -> None:
        self.user = SimpleNamespace(id=1, __str__=lambda self: "owner")
        self.guild = SimpleNamespace(id=10)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.original_response_edits: list[dict[str, object]] = []

    async def edit_original_response(self, **kwargs: object) -> None:
        self.original_response_edits.append(kwargs)


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

    def test_remote_content_includes_lease_countdown(self) -> None:
        view = RemoteView(FakeCoordinator(), owner_id=1)  # type: ignore[arg-type]

        content = view.content()

        self.assertIn("Remote unlocks in 05:00", content)

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
            any("Remote unlocks in 04:59" in edit[0] for edit in edits),
            edits,
        )

    def test_remote_countdown_shows_unlocked_when_lease_expires(self) -> None:
        async def run_countdown() -> list[tuple[str, RemoteView | None]]:
            coordinator = FakeCoordinator()
            view = RemoteView(coordinator, owner_id=1)  # type: ignore[arg-type]
            edits: list[tuple[str, RemoteView | None]] = []

            async def edit(content: str, edited_view: RemoteView | None) -> None:
                edits.append((content, edited_view))

            view.start_countdown(edit, interval_seconds=0.01)
            coordinator.remaining_seconds = None
            await asyncio.sleep(0.05)
            view.stop_countdown()
            return edits

        edits = asyncio.run(run_countdown())

        self.assertIn((REMOTE_UNLOCKED_MESSAGE, None), edits)

    def test_lockout_countdown_updates_message_content(self) -> None:
        async def run_countdown() -> list[str]:
            coordinator = FakeCoordinator()
            interaction = FakeInteraction()
            task = asyncio.create_task(
                _run_lockout_countdown(
                    coordinator,  # type: ignore[arg-type]
                    interaction,  # type: ignore[arg-type]
                    0.01,
                )
            )
            await asyncio.sleep(0)
            coordinator.remaining_seconds = 299
            await asyncio.sleep(0.05)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            return [
                str(edit["content"])
                for edit in interaction.original_response_edits
            ]

        edits = asyncio.run(run_countdown())

        self.assertTrue(
            any("Remote unlocks in 04:59" in edit for edit in edits),
            edits,
        )

    def test_lockout_countdown_shows_unlocked_when_lease_expires(self) -> None:
        async def run_countdown() -> list[str]:
            coordinator = FakeCoordinator()
            interaction = FakeInteraction()
            task = asyncio.create_task(
                _run_lockout_countdown(
                    coordinator,  # type: ignore[arg-type]
                    interaction,  # type: ignore[arg-type]
                    0.01,
                )
            )
            await asyncio.sleep(0)
            coordinator.remaining_seconds = None
            await asyncio.sleep(0.05)
            with suppress(asyncio.CancelledError):
                await task
            return [
                str(edit["content"])
                for edit in interaction.original_response_edits
            ]

        edits = asyncio.run(run_countdown())

        self.assertIn(REMOTE_UNLOCKED_MESSAGE, edits)

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
