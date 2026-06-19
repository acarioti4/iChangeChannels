from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from .config import AppConfig
from .orchestrator import PowerCoordinator, PowerResult


TV_KEYS = {
    "up": "DPAD_UP",
    "down": "DPAD_DOWN",
    "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT",
    "ok": "DPAD_CENTER",
    "back": "BACK",
    "home": "HOME",
    "vol_down": "VOLUME_DOWN",
    "vol_up": "VOLUME_UP",
    "mute": "MUTE",
    "play_pause": "MEDIA_PLAY_PAUSE",
    "menu": "MENU",
    "stop": "MEDIA_STOP",
    "rewind": "MEDIA_REWIND",
    "fast_forward": "MEDIA_FAST_FORWARD",
    "previous": "MEDIA_PREVIOUS",
    "next": "MEDIA_NEXT",
    "channel_down": "CHANNEL_DOWN",
    "channel_up": "CHANNEL_UP",
    "info": "INFO",
    "guide": "GUIDE",
    "settings": "SETTINGS",
    "search": "SEARCH",
    "num_0": "0",
    "num_1": "1",
    "num_2": "2",
    "num_3": "3",
    "num_4": "4",
    "num_5": "5",
    "num_6": "6",
    "num_7": "7",
    "num_8": "8",
    "num_9": "9",
}

PANEL_NAMES = {
    "nav": "Nav",
    "media": "Media",
    "numpad": "Numpad",
}

REMOTE_COUNTDOWN_UPDATE_INTERVAL_SECONDS = 1.0
REMOTE_UNLOCKED_MESSAGE = "Remote unlocked!"


class RemoteButton(discord.ui.Button["RemoteView"]):
    def __init__(
        self,
        label: str,
        action: str,
        *,
        row: int,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> None:
        super().__init__(label=label, style=style, row=row, disabled=disabled)
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_action(interaction, self.action)


class RemoteView(discord.ui.View):
    def __init__(
        self,
        coordinator: PowerCoordinator,
        owner_id: int,
        panel: str = "nav",
        status: str | None = None,
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.coordinator = coordinator
        self.owner_id = owner_id
        self.panel = panel if panel in PANEL_NAMES else "nav"
        self.status = status
        self._countdown_task: asyncio.Task[None] | None = None

        self.add_item(RemoteButton("Power On", "power_on", row=0, style=discord.ButtonStyle.success))
        self.add_item(RemoteButton("Power Off", "power_off", row=0, style=discord.ButtonStyle.danger))
        self.add_item(RemoteButton("Refresh", "refresh_tv", row=0, style=discord.ButtonStyle.secondary))
        self.add_item(RemoteButton("Status", "status", row=0, style=discord.ButtonStyle.secondary))

        if self.panel == "nav":
            self._add_nav_panel()
        elif self.panel == "media":
            self._add_media_panel()
        else:
            self._add_numpad_panel()

        self._add_tabs()

    def content(self) -> str:
        remaining_seconds = self.coordinator.remote_control_lease_remaining_seconds(
            user_id=self.owner_id
        )
        return _remote_content(self.panel, self.status, remaining_seconds)

    def bind_countdown_to_interaction(self, interaction: discord.Interaction) -> None:
        self.start_countdown(
            lambda content, view: interaction.edit_original_response(
                content=content,
                view=view,
            )
        )

    def start_countdown(
        self,
        edit: Callable[[str, "RemoteView | None"], Awaitable[object]],
        *,
        interval_seconds: float = REMOTE_COUNTDOWN_UPDATE_INTERVAL_SECONDS,
    ) -> None:
        self.stop_countdown()
        last_content = self.content()
        self._countdown_task = asyncio.create_task(
            self._run_countdown(edit, interval_seconds, last_content)
        )

    def stop_countdown(self) -> None:
        task = self._countdown_task
        self._countdown_task = None
        if task is None or task.done():
            return

        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if task is not current_task:
            task.cancel()

    async def _run_countdown(
        self,
        edit: Callable[[str, "RemoteView | None"], Awaitable[object]],
        interval_seconds: float,
        last_content: str,
    ) -> None:
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                if self.coordinator.remote_control_lease_remaining_seconds(
                    user_id=self.owner_id
                ) is None:
                    await edit(REMOTE_UNLOCKED_MESSAGE, None)
                    return
                next_content = self.content()
                if next_content != last_content:
                    await edit(next_content, self)
                    last_content = next_content
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return

    async def on_timeout(self) -> None:
        self.stop_countdown()

    def _add_nav_panel(self) -> None:
        self.add_item(RemoteButton("Back", "back", row=1))
        self.add_item(RemoteButton("Up", "up", row=1, style=discord.ButtonStyle.primary))
        self.add_item(RemoteButton("Home", "home", row=1))
        self.add_item(RemoteButton("Menu", "menu", row=1))

        self.add_item(RemoteButton("Left", "left", row=2, style=discord.ButtonStyle.primary))
        self.add_item(RemoteButton("OK", "ok", row=2, style=discord.ButtonStyle.success))
        self.add_item(RemoteButton("Right", "right", row=2, style=discord.ButtonStyle.primary))

        self.add_item(RemoteButton("Vol -", "vol_down", row=3))
        self.add_item(RemoteButton("Down", "down", row=3, style=discord.ButtonStyle.primary))
        self.add_item(RemoteButton("Vol +", "vol_up", row=3))
        self.add_item(RemoteButton("Mute", "mute", row=3))
        self.add_item(RemoteButton("Play/Pause", "play_pause", row=3))

    def _add_media_panel(self) -> None:
        self.add_item(RemoteButton("Play/Pause", "play_pause", row=1))
        self.add_item(RemoteButton("Stop", "stop", row=1))
        self.add_item(RemoteButton("Rewind", "rewind", row=1))
        self.add_item(RemoteButton("Fast Fwd", "fast_forward", row=1))

        self.add_item(RemoteButton("Previous", "previous", row=2))
        self.add_item(RemoteButton("Next", "next", row=2))
        self.add_item(RemoteButton("Ch -", "channel_down", row=2))
        self.add_item(RemoteButton("Ch +", "channel_up", row=2))

        self.add_item(RemoteButton("Info", "info", row=3))
        self.add_item(RemoteButton("Guide", "guide", row=3))
        self.add_item(RemoteButton("Settings", "settings", row=3))
        self.add_item(RemoteButton("Search", "search", row=3))

    def _add_numpad_panel(self) -> None:
        self.add_item(RemoteButton("1", "num_1", row=1))
        self.add_item(RemoteButton("2", "num_2", row=1))
        self.add_item(RemoteButton("3", "num_3", row=1))
        self.add_item(RemoteButton("Back", "back", row=1))

        self.add_item(RemoteButton("4", "num_4", row=2))
        self.add_item(RemoteButton("5", "num_5", row=2))
        self.add_item(RemoteButton("6", "num_6", row=2))
        self.add_item(RemoteButton("Home", "home", row=2))

        self.add_item(RemoteButton("7", "num_7", row=3))
        self.add_item(RemoteButton("8", "num_8", row=3))
        self.add_item(RemoteButton("9", "num_9", row=3))
        self.add_item(RemoteButton("0", "num_0", row=3))
        self.add_item(RemoteButton("OK", "ok", row=3, style=discord.ButtonStyle.success))

    def _add_tabs(self) -> None:
        for panel, label in PANEL_NAMES.items():
            self.add_item(
                RemoteButton(
                    label,
                    f"tab_{panel}",
                    row=4,
                    style=discord.ButtonStyle.primary
                    if panel == self.panel
                    else discord.ButtonStyle.secondary,
                    disabled=panel == self.panel,
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This remote belongs to another user.", ephemeral=True)
        return False

    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        lock_reason = self.coordinator.claim_remote_ui(
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild_id=interaction.guild.id if interaction.guild else None,
        )
        if lock_reason:
            await self._edit_remote_response(interaction, status=lock_reason)
            return

        self.stop_countdown()

        if action.startswith("tab_"):
            panel = action.removeprefix("tab_")
            view = RemoteView(
                self.coordinator,
                owner_id=self.owner_id,
                panel=panel,
                status=self.status,
            )
            await interaction.response.edit_message(
                content=view.content(),
                view=view,
            )
            view.bind_countdown_to_interaction(interaction)
            return

        if action == "power_on":
            await interaction.response.defer()
            result = await self.coordinator.power_on(interaction)
            await self._edit_deferred_remote(interaction, status=_format_result(result))
            return

        if action == "power_off":
            await interaction.response.defer()
            result = await self.coordinator.power_off(interaction)
            await self._edit_deferred_remote(interaction, status=_format_result(result))
            return

        if action == "status":
            await interaction.response.defer()
            result = await self.coordinator.status()
            await self._edit_deferred_remote(interaction, status=result.message)
            return

        block_reason = self.coordinator.remote_control_block_reason(interaction.guild)
        if block_reason:
            await self._edit_remote_response(interaction, status=block_reason)
            return

        if action == "refresh_tv":
            await interaction.response.defer()
            result = await self.coordinator.refresh_tv_box(interaction)
            await self._edit_deferred_remote(interaction, status=_format_result(result))
            return

        key = TV_KEYS[action]
        await interaction.response.defer()
        result = await self.coordinator.send_tv_key(
            action=action,
            key=key,
            user_id=interaction.user.id,
            guild_id=interaction.guild.id if interaction.guild else None,
        )
        await self._edit_deferred_remote(
            interaction,
            status=_format_result(result, include_checks=False),
        )

    async def _edit_remote_response(
        self,
        interaction: discord.Interaction,
        *,
        status: str,
    ) -> None:
        view = RemoteView(
            self.coordinator,
            owner_id=self.owner_id,
            panel=self.panel,
            status=status,
        )
        await interaction.response.edit_message(
            content=view.content(),
            view=view,
        )
        view.bind_countdown_to_interaction(interaction)

    async def _edit_deferred_remote(
        self,
        interaction: discord.Interaction,
        *,
        status: str,
    ) -> None:
        view = RemoteView(
            self.coordinator,
            owner_id=self.owner_id,
            panel=self.panel,
            status=status,
        )
        await interaction.edit_original_response(
            content=view.content(),
            view=view,
        )
        view.bind_countdown_to_interaction(interaction)


class IChangeChannelsBot(commands.Bot):
    def __init__(self, config: AppConfig) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.logger = logging.getLogger("ichannel.bot")
        self.coordinator = PowerCoordinator(self, config)
        self._synced = False

    async def setup_hook(self) -> None:
        command = app_commands.Command(
            name="remote",
            description="Open the iChangeChannels Android TV remote.",
            callback=self.remote_command,
        )
        self.tree.add_command(command)

    async def on_ready(self) -> None:
        assert self.user is not None
        self.logger.info("Logged in as %s (%s)", self.user, self.user.id)
        if self.config.command_sync_on_start and not self._synced:
            try:
                await self._sync_global_commands_only()
                self._synced = True
            except Exception:
                self.logger.exception("Slash command sync failed")

    async def _sync_global_commands_only(self) -> None:
        global_synced = await self.tree.sync()
        self.logger.info(
            "Synced %s global slash command(s)",
            len(global_synced),
        )
        await self._clear_stale_guild_commands()

    async def _clear_stale_guild_commands(self) -> None:
        for guild in self.guilds:
            guild_object = discord.Object(id=guild.id)
            self.tree.clear_commands(guild=guild_object)
            synced = await self.tree.sync(guild=guild_object)
            self.logger.info(
                "Cleared stale slash commands from guild %s (%s remaining)",
                guild.id,
                len(synced),
            )

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        await self.coordinator.note_stream_voice_update(member, before, after)

    async def remote_command(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use `/remote` from a server text channel.", ephemeral=True
            )
            return

        lock_reason = self.coordinator.claim_remote_ui(
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild_id=interaction.guild.id,
        )
        if lock_reason:
            await interaction.response.send_message(lock_reason, ephemeral=True)
            _start_lockout_countdown(self.coordinator, interaction)
            return

        view = RemoteView(self.coordinator, owner_id=interaction.user.id)
        await interaction.response.send_message(
            view.content(), view=view, ephemeral=True
        )
        view.bind_countdown_to_interaction(interaction)


def build_bot(config: AppConfig) -> IChangeChannelsBot:
    return IChangeChannelsBot(config)


def _format_result(result: PowerResult, *, include_checks: bool = True) -> str:
    prefix = "OK" if result.ok else "Needs attention"
    if not include_checks or not result.checks:
        return f"{prefix}: {result.message}"

    lines = [f"{prefix}: {result.message}", ""]
    for key, value in result.checks.items():
        label = key.replace("_", " ").title()
        lines.append(f"{label}: {'OK' if value else 'Missing'}")
    return "\n".join(lines)


def _remote_content(panel: str, status: str | None, remaining_seconds: int | None) -> str:
    lines = [f"iChangeChannels remote - {PANEL_NAMES[panel]}"]
    if remaining_seconds is not None:
        lines.append(f"Remote unlocks in {_format_countdown(remaining_seconds)}")
    if status:
        lines.extend(["", status])

    content = "\n".join(lines)
    if len(content) <= 2000:
        return content

    return content[:1997] + "..."


def _format_countdown(seconds: int) -> str:
    minutes, seconds = divmod(max(0, seconds), 60)
    return f"{minutes:02}:{seconds:02}"


def _start_lockout_countdown(
    coordinator: PowerCoordinator,
    interaction: discord.Interaction,
    *,
    interval_seconds: float = REMOTE_COUNTDOWN_UPDATE_INTERVAL_SECONDS,
) -> None:
    asyncio.create_task(
        _run_lockout_countdown(coordinator, interaction, interval_seconds)
    )


async def _run_lockout_countdown(
    coordinator: PowerCoordinator,
    interaction: discord.Interaction,
    interval_seconds: float,
) -> None:
    last_content = coordinator.remote_control_lockout_message() or REMOTE_UNLOCKED_MESSAGE
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            next_content = coordinator.remote_control_lockout_message()
            if next_content is None:
                if last_content != REMOTE_UNLOCKED_MESSAGE:
                    await interaction.edit_original_response(content=REMOTE_UNLOCKED_MESSAGE)
                return
            if next_content != last_content:
                await interaction.edit_original_response(content=next_content)
                last_content = next_content
    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        return
