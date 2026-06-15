from __future__ import annotations

import asyncio
import math
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import discord

from .android_tv import AndroidTVController, AndroidTVError, AndroidTVPairingRequired
from .config import AppConfig
from .discord_desktop import (
    DesktopAutomationConfig,
    DesktopAutomationError,
    DiscordDesktopController,
)
from .state import ActiveSession, StateStore, now_iso
from .vlc import VLCError, VLCManager


REMOTE_CONTROL_IDLE_TIMEOUT_SECONDS = 5 * 60


@dataclass
class StreamLocation:
    guild: discord.Guild
    channel: discord.abc.GuildChannel
    member: discord.Member


@dataclass
class PowerResult:
    ok: bool
    message: str
    checks: dict[str, bool]


@dataclass
class _TokenBucket:
    tokens: float
    updated_at: float


@dataclass
class RemoteControlLease:
    user_id: int
    username: str
    guild_id: int | None
    last_activity_at: float


@dataclass
class RemoteControlLockResult:
    allowed: bool
    holder: RemoteControlLease | None
    remaining_seconds: int = 0


class RemoteControlLock:
    def __init__(
        self,
        *,
        timeout_seconds: float = REMOTE_CONTROL_IDLE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._lease: RemoteControlLease | None = None

    def claim_or_refresh(
        self,
        *,
        user_id: int,
        username: str,
        guild_id: int | None,
    ) -> RemoteControlLockResult:
        now = self._clock()
        lease = self._lease
        if lease and lease.user_id != user_id and not self._is_expired(lease, now):
            remaining = self.timeout_seconds - (now - lease.last_activity_at)
            return RemoteControlLockResult(
                allowed=False,
                holder=lease,
                remaining_seconds=max(1, math.ceil(remaining)),
            )

        self._lease = RemoteControlLease(
            user_id=user_id,
            username=username,
            guild_id=guild_id,
            last_activity_at=now,
        )
        return RemoteControlLockResult(allowed=True, holder=self._lease)

    def _is_expired(self, lease: RemoteControlLease, now: float) -> bool:
        return now - lease.last_activity_at >= self.timeout_seconds

    def release(self) -> None:
        self._lease = None


class RemoteKeyRateLimiter:
    def __init__(
        self,
        *,
        key_rate_per_second: float,
        key_burst: int,
        number_rate_per_second: float,
        number_burst: int,
    ) -> None:
        self.key_rate_per_second = key_rate_per_second
        self.key_burst = key_burst
        self.number_rate_per_second = number_rate_per_second
        self.number_burst = number_burst
        self._buckets: dict[tuple[str, str], _TokenBucket] = {}

    def consume(self, scope: str, action: str) -> tuple[bool, str]:
        category = "number" if action.startswith("num_") else "key"
        if category == "number":
            rate = self.number_rate_per_second
            burst = self.number_burst
        else:
            rate = self.key_rate_per_second
            burst = self.key_burst

        if burst <= 0:
            return False, f"{category} input is disabled by rate-limit configuration."

        now = time.monotonic()
        bucket_key = (scope, category)
        bucket = self._buckets.get(bucket_key)
        if bucket is None:
            bucket = _TokenBucket(tokens=float(burst), updated_at=now)

        elapsed = max(0.0, now - bucket.updated_at)
        tokens = min(float(burst), bucket.tokens + elapsed * rate)
        if tokens < 1:
            bucket.tokens = tokens
            bucket.updated_at = now
            self._buckets[bucket_key] = bucket
            return False, f"{category} input rate limit exceeded."

        bucket.tokens = tokens - 1
        bucket.updated_at = now
        self._buckets[bucket_key] = bucket
        return True, ""


class PowerCoordinator:
    def __init__(self, bot: discord.Client, config: AppConfig) -> None:
        self.bot = bot
        self.config = config
        self.logger = logging.getLogger("ichannel.power")
        self.state = StateStore(config.state_file)
        self.tv = AndroidTVController(
            host=config.android_tv_host,
            certfile=config.android_tv_certfile,
            keyfile=config.android_tv_keyfile,
            client_name=config.android_tv_client_name,
            power_timeout_seconds=config.android_tv_power_timeout_seconds,
            logger=logging.getLogger("ichannel.android_tv"),
        )
        self.vlc = VLCManager(
            vlc_path=config.vlc_path,
            vlc_args=config.vlc_args,
            process_names=config.vlc_process_names,
            window_rect=config.vlc_window_rect,
            window_show_cmd=config.vlc_window_show_cmd,
            start_timeout_seconds=config.vlc_start_timeout_seconds,
            logger=logging.getLogger("ichannel.vlc"),
        )
        self.desktop = DiscordDesktopController(
            DesktopAutomationConfig(
                dm_search=config.discord_dm_search,
                vlc_window_title=config.vlc_window_title,
            ),
            logging.getLogger("ichannel.desktop"),
        )
        self._lock = asyncio.Lock()
        self._active_lifecycle_action: str | None = None
        self._status_running = False
        self._key_lock = asyncio.Lock()
        self._key_pending = 0
        self._key_limiter = RemoteKeyRateLimiter(
            key_rate_per_second=config.remote_key_rate_per_second,
            key_burst=config.remote_key_burst,
            number_rate_per_second=config.remote_number_rate_per_second,
            number_burst=config.remote_number_burst,
        )
        self._remote_control_lock = RemoteControlLock()

    async def power_on(self, interaction: discord.Interaction) -> PowerResult:
        blocked = self._begin_lifecycle_action("Power On")
        if blocked:
            return blocked

        try:
            async with self._lock:
                return await self._power_on_locked(interaction)
        finally:
            self._end_lifecycle_action("Power On")

    async def _power_on_locked(self, interaction: discord.Interaction) -> PowerResult:
            if interaction.guild is None or not isinstance(interaction.user, discord.Member):
                return PowerResult(False, "Run this from a server text channel.", {})

            target_channel = await self._resolve_target_channel(interaction.guild, interaction.user)
            if target_channel is None:
                return PowerResult(
                    False,
                    "No usable voice channel was found. Join a voice channel and try again.",
                    {},
                )

            requested_by = interaction.user
            join_url = f"https://discord.com/channels/{interaction.guild.id}/{target_channel.id}"
            session = ActiveSession(
                requested_by_user_id=requested_by.id,
                requested_by_username=str(requested_by),
                guild_id=interaction.guild.id,
                guild_name=interaction.guild.name,
                channel_id=target_channel.id,
                channel_name=target_channel.name,
                join_url=join_url,
                started_at=now_iso(),
            )

            self.logger.info(
                "Power On requested by %s (%s), guild=%s (%s), channel=%s (%s)",
                session.requested_by_username,
                session.requested_by_user_id,
                session.guild_name,
                session.guild_id,
                session.channel_name,
                session.channel_id,
            )

            current_location = self.find_stream_location()
            if current_location and current_location.channel.id != target_channel.id:
                return PowerResult(
                    False,
                    "mr.veeseeksbox is already active in "
                    f"{current_location.guild.name} / {current_location.channel.name}. "
                    "Power Off there before summoning it anywhere else.",
                    {"stream_account_locked": True},
                )

            self.state.set_active(session)

            checks = {
                "android_tv_on": False,
                "vlc_open": False,
                "stream_account_in_channel": False,
                "stream_active": False,
            }
            errors: list[str] = []

            try:
                await self.tv.ensure_on()
                checks["android_tv_on"] = True
            except AndroidTVPairingRequired as exc:
                self.logger.error("Android TV pairing required: %s", exc)
                errors.append(str(exc))
            except AndroidTVError as exc:
                self.logger.error("Android TV power-on failed: %s", exc)
                errors.append(str(exc))

            try:
                await self.vlc.ensure_open()
                checks["vlc_open"] = True
            except VLCError as exc:
                self.logger.error("VLC check failed: %s", exc)
                errors.append(str(exc))

            if checks["vlc_open"]:
                stream_result = await self._ensure_streaming(session, target_channel)
                checks.update(stream_result.checks)
                if not stream_result.ok:
                    errors.append(stream_result.message)
            else:
                message = "VLC is not open, so stream automation was not attempted."
                self.logger.error(message)
                errors.append(message)

            if errors:
                self.logger.error("Power On incomplete: %s", " | ".join(errors))
                return PowerResult(
                    False,
                    "Power On incomplete: " + " | ".join(errors),
                    checks,
                )

            self.logger.info("Power On complete")
            return PowerResult(True, "Powered On and streaming VLC.", checks)

    async def power_off(self, interaction: discord.Interaction) -> PowerResult:
        blocked = self._begin_lifecycle_action("Power Off")
        if blocked:
            return blocked

        try:
            async with self._lock:
                return await self._power_off_locked(interaction)
        finally:
            self._end_lifecycle_action("Power Off")

    async def _power_off_locked(self, interaction: discord.Interaction) -> PowerResult:
            self.logger.info("Power Off requested by %s", interaction.user)
            location = self.find_stream_location()
            if location is None:
                self.state.clear_active()
                return PowerResult(True, "Powered Off. mr.veeseeksbox was not in voice.", {})

            stream_account_alone = self._stream_account_is_alone(location)
            if (
                interaction.guild is not None
                and interaction.guild.id != location.guild.id
                and not stream_account_alone
            ):
                return PowerResult(
                    False,
                    "mr.veeseeksbox is active in "
                    f"{location.guild.name} / {location.channel.name}. "
                    "Power Off must be run from the active server.",
                    {"stream_account_disconnected": False},
                )

            if stream_account_alone and interaction.guild is not None:
                self.logger.info(
                    "Power Off bypassing active-server check because stream account is alone "
                    "in %s / %s",
                    location.guild.name,
                    location.channel.name,
                )

            result = await self._disconnect_stream_account(
                location,
                reason="iChangeChannels Power Off",
                message="Powered Off. VLC and Android TV were left alone.",
            )
            if result.ok and stream_account_alone:
                self._release_remote_control_lock(
                    "Power Off disconnected stream account while it was alone"
                )
            return result

    async def _disconnect_stream_account(
        self,
        location: StreamLocation,
        *,
        reason: str,
        message: str,
    ) -> PowerResult:
            try:
                await location.member.move_to(None, reason=reason)
            except discord.Forbidden:
                return PowerResult(
                    False,
                    "I do not have permission to disconnect mr.veeseeksbox.",
                    {"stream_account_disconnected": False},
                )
            except discord.HTTPException as exc:
                return PowerResult(
                    False,
                    f"Discord refused the disconnect: {exc}",
                    {"stream_account_disconnected": False},
                )

            self.state.clear_active()
            self.logger.info(
                "Disconnected stream account from %s / %s",
                location.guild.name,
                location.channel.name,
            )
            return PowerResult(
                True,
                message,
                {"stream_account_disconnected": True},
            )

    async def status(self) -> PowerResult:
        if self._status_running:
            self.logger.warning("Status rejected: status check already in progress")
            return PowerResult(
                False,
                "Status is already in progress; no new check started.",
                {"action_rejected_in_progress": True},
            )

        self._status_running = True
        try:
            return await self._status_impl()
        finally:
            self._status_running = False

    async def _status_impl(self) -> PowerResult:
        checks = {
            "android_tv_on": False,
            "vlc_open": self.vlc.is_running(),
            "stream_account_in_channel": False,
            "stream_active": False,
        }
        try:
            checks["android_tv_on"] = await self.tv.is_on()
        except Exception as exc:
            self.logger.warning("Status could not read Android TV state: %s", exc)

        active = self.state.get_active()
        location = self.find_stream_location()
        if active and location:
            checks["stream_account_in_channel"] = location.channel.id == active.channel_id
            checks["stream_active"] = bool(
                location.member.voice and location.member.voice.self_stream
            )
        elif location:
            checks["stream_account_in_channel"] = True
            checks["stream_active"] = bool(
                location.member.voice and location.member.voice.self_stream
            )

        ok = all(checks.values())
        return PowerResult(ok, _format_checks(checks, active), checks)

    async def send_tv_key(
        self,
        *,
        action: str,
        key: str,
        user_id: int,
        guild_id: int | None,
    ) -> PowerResult:
        scope = f"{guild_id or 'dm'}:{user_id}"
        if self._key_pending >= self.config.remote_key_queue_limit:
            self.logger.warning(
                "TV key rejected: queue full user_id=%s guild_id=%s action=%s pending=%s",
                user_id,
                guild_id,
                action,
                self._key_pending,
            )
            return PowerResult(
                False,
                "Input rejected: remote key queue is full.",
                {"key_rejected_queue_full": True},
            )

        allowed, reason = self._key_limiter.consume(scope, action)
        if not allowed:
            self.logger.warning(
                "TV key rejected: rate limit user_id=%s guild_id=%s action=%s reason=%s",
                user_id,
                guild_id,
                action,
                reason,
            )
            return PowerResult(
                False,
                f"Input rejected: {reason}",
                {"key_rejected_rate_limit": True},
            )

        self._key_pending += 1
        try:
            async with self._key_lock:
                try:
                    await self.tv.send_key(key)
                except AndroidTVPairingRequired as exc:
                    self.logger.error("TV key failed: pairing required action=%s error=%s", action, exc)
                    return PowerResult(False, str(exc), {"key_send_failed": True})
                except AndroidTVError as exc:
                    self.logger.error("TV key failed: action=%s error=%s", action, exc)
                    return PowerResult(False, str(exc), {"key_send_failed": True})
        finally:
            self._key_pending -= 1
        return PowerResult(True, f"Sent {key}.", {"android_tv_key_sent": True})

    def claim_remote_ui(
        self,
        *,
        user_id: int,
        username: str,
        guild_id: int | None,
    ) -> str | None:
        result = self._remote_control_lock.claim_or_refresh(
            user_id=user_id,
            username=username,
            guild_id=guild_id,
        )
        if result.allowed or result.holder is None:
            return None

        return (
            f"{result.holder.username} is using the remote right now. "
            "It unlocks after 5 minutes without button presses "
            f"({self._format_remote_lock_remaining(result.remaining_seconds)} left)."
        )

    def remote_control_block_reason(self, guild: discord.Guild | None) -> str | None:
        if guild is None:
            return "Use the remote from a server."

        location = self.find_stream_location()
        if location and location.guild.id != guild.id:
            return (
                "The remote is active in "
                f"{location.guild.name} / {location.channel.name}. "
                "Power Off there before controlling it from another server."
            )
        return None

    async def note_stream_voice_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.id != self.config.stream_user_id:
            await self._auto_power_off_if_stream_account_is_alone(member, before, after)
            return

        before_name = getattr(before.channel, "name", None)
        after_name = getattr(after.channel, "name", None)
        self.logger.info(
            "Stream account voice update: before=%s after=%s streaming=%s",
            before_name,
            after_name,
            after.self_stream,
        )

        if after.channel is None:
            self.state.clear_active()

    async def _auto_power_off_if_stream_account_is_alone(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel is None or before.channel == after.channel:
            return

        async with self._lock:
            location = self.find_stream_location()
            if location is None or location.channel.id != before.channel.id:
                return
            if not self._stream_account_is_alone(location):
                return

            self.logger.info(
                "Auto Power Off: %s left %s / %s, leaving stream account alone",
                member,
                location.guild.name,
                location.channel.name,
            )
            result = await self._disconnect_stream_account(
                location,
                reason="iChangeChannels Auto Power Off: stream account alone",
                message="Powered Off because mr.veeseeksbox was alone in voice.",
            )
            if not result.ok:
                self.logger.warning("Auto Power Off failed: %s", result.message)
                return

            self._release_remote_control_lock(
                "Auto Power Off disconnected stream account after last viewer left"
            )

    def find_stream_location(self) -> StreamLocation | None:
        for guild in self.bot.guilds:
            for channel in [*guild.voice_channels, *guild.stage_channels]:
                for member in channel.members:
                    if member.id == self.config.stream_user_id:
                        return StreamLocation(guild=guild, channel=channel, member=member)
        return None

    def _stream_account_is_alone(self, location: StreamLocation) -> bool:
        members = list(location.channel.members)
        return (
            len(members) == 1
            and members[0].id == self.config.stream_user_id
        )

    def _release_remote_control_lock(self, reason: str) -> None:
        self._remote_control_lock.release()
        self.logger.info("Remote control lock released: %s", reason)

    def _begin_lifecycle_action(self, action: str) -> PowerResult | None:
        if self._active_lifecycle_action is not None:
            self.logger.warning(
                "%s rejected: %s already in progress",
                action,
                self._active_lifecycle_action,
            )
            return PowerResult(
                False,
                f"{self._active_lifecycle_action} is already in progress; no new actions started.",
                {"action_rejected_in_progress": True},
            )

        self._active_lifecycle_action = action
        self.logger.info("%s accepted", action)
        return None

    def _end_lifecycle_action(self, action: str) -> None:
        if self._active_lifecycle_action != action:
            self.logger.warning(
                "%s ended while %s was marked active",
                action,
                self._active_lifecycle_action,
            )
        self._active_lifecycle_action = None

    def _format_remote_lock_remaining(self, seconds: int) -> str:
        minutes, seconds = divmod(max(0, seconds), 60)
        if minutes and seconds:
            return f"{minutes}m {seconds}s"
        if minutes:
            return f"{minutes}m"
        return f"{seconds}s"

    async def _ensure_streaming(
        self, session: ActiveSession, target_channel: discord.abc.GuildChannel
    ) -> PowerResult:
        location = self.find_stream_location()
        if location and location.channel.id == target_channel.id:
            in_channel = True
            streaming = bool(location.member.voice and location.member.voice.self_stream)
            if streaming:
                self.logger.info("Stream account is already in target channel and streaming")
                return PowerResult(
                    True,
                    "Stream account is already in target channel and streaming.",
                    {"stream_account_in_channel": True, "stream_active": True},
                )
        else:
            in_channel = False
            streaming = False

        if not in_channel:
            try:
                await self._send_join_dm(session)
            except discord.Forbidden:
                self.logger.error("Could not DM stream account; Discord returned Forbidden")
                return PowerResult(
                    False,
                    "I could not DM mr.veeseeksbox. Check DM privacy settings and shared servers.",
                    {
                        "stream_account_in_channel": in_channel,
                        "stream_active": streaming,
                    },
                )
            except discord.HTTPException as exc:
                self.logger.error("Could not DM stream account: %s", exc)
                return PowerResult(
                    False,
                    f"Discord failed while sending the join DM: {exc}",
                    {
                        "stream_account_in_channel": in_channel,
                        "stream_active": streaming,
                    },
                )

            if not self.config.desktop_automation_enabled:
                return PowerResult(
                    False,
                    "Desktop automation is disabled, so I sent the DM but cannot join/start stream.",
                    {
                        "stream_account_in_channel": in_channel,
                        "stream_active": streaming,
                    },
                )

            try:
                await self.desktop.join_voice_from_dm_link(session.join_url)
            except DesktopAutomationError as exc:
                self.logger.error("Discord join automation failed: %s", exc)
                return PowerResult(
                    False,
                    f"Could not automate Discord joining the channel: {exc}",
                    {"stream_account_in_channel": False, "stream_active": False},
                )

            in_channel = await self._wait_for_stream_account_in_channel(target_channel.id)

        if not in_channel:
            return PowerResult(
                False,
                "Timed out waiting for mr.veeseeksbox to join the target voice channel.",
                {"stream_account_in_channel": False, "stream_active": False},
            )

        location = self.find_stream_location()
        if location and location.member.voice and location.member.voice.self_stream:
            return PowerResult(
                True,
                "Stream account joined and was already streaming.",
                {"stream_account_in_channel": True, "stream_active": True},
            )

        if not self.config.desktop_automation_enabled:
            return PowerResult(
                False,
                "Desktop automation is disabled, so I cannot start the VLC stream.",
                {"stream_account_in_channel": True, "stream_active": False},
            )

        try:
            await self.desktop.start_vlc_stream()
        except DesktopAutomationError as exc:
            self.logger.error("Discord stream automation failed: %s", exc)
            return PowerResult(
                False,
                f"Could not automate Discord Go Live: {exc}",
                {"stream_account_in_channel": True, "stream_active": False},
            )

        streaming = await self._wait_for_streaming(target_channel.id)
        if not streaming:
            return PowerResult(
                False,
                "Timed out waiting for Discord to report that mr.veeseeksbox is streaming.",
                {"stream_account_in_channel": True, "stream_active": False},
            )

        return PowerResult(
            True,
            "Stream account joined and started streaming.",
            {"stream_account_in_channel": True, "stream_active": True},
        )

    async def _send_join_dm(self, session: ActiveSession) -> None:
        user = self.bot.get_user(self.config.stream_user_id)
        if user is None:
            user = await self.bot.fetch_user(self.config.stream_user_id)
        message = (
            f"Power On requested by {session.requested_by_username}\n"
            f"Guild: {session.guild_name} ({session.guild_id})\n"
            f"Voice channel: {session.channel_name} ({session.channel_id})\n"
            f"{session.join_url}"
        )
        await user.send(message)
        self.logger.info("Sent join DM to %s with %s", self.config.stream_username, session.join_url)

    async def _wait_for_stream_account_in_channel(self, channel_id: int) -> bool:
        deadline = asyncio.get_running_loop().time() + self.config.discord_join_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            location = self.find_stream_location()
            if location and location.channel.id == channel_id:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _wait_for_streaming(self, channel_id: int) -> bool:
        deadline = asyncio.get_running_loop().time() + self.config.discord_stream_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            location = self.find_stream_location()
            if (
                location
                and location.channel.id == channel_id
                and location.member.voice
                and location.member.voice.self_stream
            ):
                return True
            await asyncio.sleep(0.5)
        return False

    async def _resolve_target_channel(
        self, guild: discord.Guild, requester: discord.Member
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        if requester.voice and requester.voice.channel:
            return requester.voice.channel

        stream_member = guild.get_member(self.config.stream_user_id)
        if stream_member is None:
            try:
                stream_member = await guild.fetch_member(self.config.stream_user_id)
            except discord.HTTPException:
                stream_member = None

        channels: list[discord.VoiceChannel | discord.StageChannel] = [
            *guild.voice_channels,
            *guild.stage_channels,
        ]
        for channel in channels:
            default_permissions = channel.permissions_for(guild.default_role)
            if not default_permissions.connect:
                continue
            if stream_member and not channel.permissions_for(stream_member).connect:
                continue
            return channel
        return None


def _format_checks(checks: dict[str, bool], active: ActiveSession | None) -> str:
    lines = ["Status:"]
    for key, value in checks.items():
        label = key.replace("_", " ").title()
        lines.append(f"- {label}: {'OK' if value else 'Missing'}")
    if active:
        lines.append(f"Active channel: {active.guild_name} / {active.channel_name}")
        lines.append(f"Requested by: {active.requested_by_username}")
    return "\n".join(lines)
