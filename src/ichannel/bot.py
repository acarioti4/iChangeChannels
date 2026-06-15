from __future__ import annotations

import logging

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
    def __init__(self, coordinator: PowerCoordinator, owner_id: int, panel: str = "nav") -> None:
        super().__init__(timeout=15 * 60)
        self.coordinator = coordinator
        self.owner_id = owner_id
        self.panel = panel if panel in PANEL_NAMES else "nav"

        self.add_item(RemoteButton("Power On", "power_on", row=0, style=discord.ButtonStyle.success))
        self.add_item(RemoteButton("Power Off", "power_off", row=0, style=discord.ButtonStyle.danger))
        self.add_item(RemoteButton("Status", "status", row=0, style=discord.ButtonStyle.secondary))

        if self.panel == "nav":
            self._add_nav_panel()
        elif self.panel == "media":
            self._add_media_panel()
        else:
            self._add_numpad_panel()

        self._add_tabs()

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
            await interaction.response.send_message(lock_reason, ephemeral=True)
            return

        if action.startswith("tab_"):
            panel = action.removeprefix("tab_")
            view = RemoteView(self.coordinator, owner_id=self.owner_id, panel=panel)
            await interaction.response.edit_message(
                content=f"iChangeChannels remote - {PANEL_NAMES[view.panel]}",
                view=view,
            )
            return

        if action == "power_on":
            await interaction.response.defer(ephemeral=True, thinking=True)
            result = await self.coordinator.power_on(interaction)
            await interaction.followup.send(_format_result(result), ephemeral=True)
            return

        if action == "power_off":
            await interaction.response.defer(ephemeral=True, thinking=True)
            result = await self.coordinator.power_off(interaction)
            await interaction.followup.send(_format_result(result), ephemeral=True)
            return

        if action == "status":
            await interaction.response.defer(ephemeral=True, thinking=True)
            result = await self.coordinator.status()
            await interaction.followup.send(result.message, ephemeral=True)
            return

        block_reason = self.coordinator.remote_control_block_reason(interaction.guild)
        if block_reason:
            await interaction.response.send_message(block_reason, ephemeral=True)
            return

        key = TV_KEYS[action]
        await interaction.response.defer(ephemeral=True)
        result = await self.coordinator.send_tv_key(
            action=action,
            key=key,
            user_id=interaction.user.id,
            guild_id=interaction.guild.id if interaction.guild else None,
        )
        if not result.ok:
            await interaction.followup.send(_format_result(result), ephemeral=True)


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
                if self.config.sync_commands_to_guild_id:
                    guild = discord.Object(id=self.config.sync_commands_to_guild_id)
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    self.logger.info(
                        "Synced %s slash command(s) to guild %s",
                        len(synced),
                        self.config.sync_commands_to_guild_id,
                    )
                else:
                    synced = await self.tree.sync()
                    self.logger.info("Synced %s global slash command(s)", len(synced))
                self._synced = True
            except Exception:
                self.logger.exception("Slash command sync failed")

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
            return

        view = RemoteView(self.coordinator, owner_id=interaction.user.id)
        await interaction.response.send_message(
            "iChangeChannels remote - Nav", view=view, ephemeral=True
        )


def build_bot(config: AppConfig) -> IChangeChannelsBot:
    return IChangeChannelsBot(config)


def _format_result(result: PowerResult) -> str:
    prefix = "OK" if result.ok else "Needs attention"
    if not result.checks:
        return f"{prefix}: {result.message}"

    lines = [f"{prefix}: {result.message}", ""]
    for key, value in result.checks.items():
        label = key.replace("_", " ").title()
        lines.append(f"{label}: {'OK' if value else 'Missing'}")
    return "\n".join(lines)
