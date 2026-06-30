# iChangeChannels

iChangeChannels is a Windows-focused Discord bot for controlling an Android TV
box and coordinating a logged-in Discord desktop account that streams VLC into a
voice channel.

The bot handles Discord API work directly and uses desktop automation only for
the actions Discord does not expose to bot accounts: making the logged-in
streaming desktop account open a voice-channel link and start Go Live.

Throughout this document "the stream account" means the dedicated Discord
account, logged in on the streaming PC's desktop client, that joins voice and
goes live with VLC. You configure which account that is through `STREAM_USER_ID`
(and an optional display name in `STREAM_USERNAME`).

## Current Behavior

- Registers a `/remote` slash command.
- Sends an ephemeral Discord remote UI to the user who ran `/remote`.
- Locks the remote UI to one user at a time, then releases it after 5 minutes
  without button presses so another user can claim it.
- Still shows the ephemeral remote UI to users who are locked out, with the
  controls disabled and a message naming the current lock holder.
- Keeps the ephemeral remote UI visible when the 5-minute lock expires; the bot
  does not remove the controls just because the lease ended.
- Allows configured admins to use the remote without lock timers, lock-holder
  messages, or disabled controls unless they click `Take Control`, which moves
  the lock to themselves.
- Provides three remote panels:
  - `Nav`: power/status, directional keys, back, home, menu, volume, mute, play/pause.
  - `Media`: play/pause, stop, rewind, fast-forward, previous, next, channel, info, guide, settings, search.
  - `Numpad`: digits 0-9 plus back, home, and OK.
- Controls Android TV over WiFi using the Android TV Remote Protocol v2.
- Records the Power On requester, guild, voice channel, join URL, and timestamp in `data/state.json`.
- Enforces one active stream location at a time:
  - The stream account cannot be moved to another guild or channel while already in voice.
  - Power Off must be run from the active server.
  - If the stream account is alone in voice, Power Off can disconnect it immediately from any server.
  - Remote key presses are blocked from other servers while the stream account is active elsewhere.
- Shows live logs in a Tkinter app window and writes rotating logs to `LOG_FILE`.

## Power Rules

Power On means all of these are true:

- Android TV reports powered on.
- VLC is running locally.
- The stream account is in the selected voice channel.
- Discord reports the stream account as Go Live streaming.

Power On only fixes missing pieces. For example, if VLC is already running and
the stream account is already in the correct channel and streaming, the bot does
not restart VLC or move the stream account.

Power Off disconnects the stream account from voice through the Discord API and
then powers off the Android TV box (it sends POWER only if the TV reports as on).
It does not close VLC. If the Android TV power-off fails, the stream account is
still disconnected and the result is reported as needing attention.

If the last viewer leaves and the stream account is the only account left in the
voice channel, the bot automatically disconnects the stream account, powers off
the Android TV box, and releases the remote lock immediately, even if the
previous user's 5-minute idle timer has not expired.

The Android TV is powered off whenever the bot itself disconnects the stream
account (Power Off or the automatic alone-in-voice disconnect). If the stream
account is disconnected manually from Discord, the bot only clears its state and
leaves the Android TV alone.

## Important Limits

Discord's bot API can verify voice-channel membership and the `self_stream`
state. It cannot make a normal user account Go Live, and it cannot prove which
desktop window is being streamed. This app selects the VLC window through Windows
UI Automation, then verifies the API-visible Go Live state.

Android TV must be reachable on the network. If the box is fully off and its
remote service is unavailable, software cannot wake it unless the device still
accepts network remote commands in standby.

## Setup

1. Create and configure the Discord bot.
   - Give it the permissions you intend to use, including permission to disconnect members.
   - Enable the Server Members intent in the Discord Developer Portal.
   - Invite it to each server where `/remote` should be available.

2. Install Python 3.11+.

3. Install dependencies:

   ```powershell
   py -m pip install -r requirements.txt
   ```

4. Create `.env` from `.env.example` if it does not already exist, then fill in:
   - `DISCORD_BOT_TOKEN`
   - `STREAM_USER_ID`
   - `DISCORD_ADMIN_USERS` if you want admin takeover users
   - `ANDROID_TV_HOST` (or leave it blank and let the pairing helper in step 5
     autopopulate it)
   - VLC settings for the dedicated server

5. Pair the Android TV:

   ```powershell
   py scripts\pair_android_tv.py
   ```

   The script can use `ANDROID_TV_HOST`, discover Android TV Remote Protocol
   devices with mDNS, or ask for a manual host. It can update `ANDROID_TV_HOST`
   in `.env`, then prompts for the pairing code shown on the TV.

6. Start the app:

   ```powershell
   py run.py
   ```

## Configuration

Core `.env` values:

```env
DISCORD_BOT_TOKEN=
STREAM_USER_ID=
STREAM_USERNAME=the stream account
DISCORD_ADMIN_USERS=
DISCORD_DM_SEARCH=iChangeChannels
COMMAND_SYNC_ON_START=true

# Leave blank to let scripts\pair_android_tv.py discover and fill it in.
ANDROID_TV_HOST=
ANDROID_TV_CERTFILE=data/androidtv_cert.pem
ANDROID_TV_KEYFILE=data/androidtv_key.pem
ANDROID_TV_CLIENT_NAME=iChangeChannels

VLC_PATH=C:\Program Files\VideoLAN\VLC\vlc.exe
VLC_ARGS=dshow:// :dshow-fps=60 --preferred-resolution=1080p
VLC_PROCESS_NAMES=vlc.exe,vlc
VLC_WINDOW_TITLE=VLC media player
VLC_WINDOW_RECT=
VLC_WINDOW_SHOW_CMD=

ANDROID_TV_POWER_TIMEOUT_SECONDS=12
DISCORD_JOIN_TIMEOUT_SECONDS=20
DISCORD_STREAM_TIMEOUT_SECONDS=25
VLC_START_TIMEOUT_SECONDS=8
DESKTOP_AUTOMATION_ENABLED=true

REMOTE_KEY_RATE_PER_SECOND=10
REMOTE_KEY_BURST=5
REMOTE_NUMBER_RATE_PER_SECOND=6
REMOTE_NUMBER_BURST=3
REMOTE_KEY_QUEUE_LIMIT=5

DATA_DIR=data
LOG_FILE=data/ichannel.log
```

## VLC Window Capture

Keep VLC visible in the background rather than minimized. Discord's stream picker
is more reliable when VLC has a visible top-level window.

To record the current VLC window placement:

```powershell
py scripts\capture_vlc_window.py
```

The script finds the largest visible VLC-owned top-level window and prints:

- `VLC_WINDOW_RECT=left,top,width,height`
- `VLC_WINDOW_SHOW_CMD`
- `VLC_ARGS`, if VLC exposes launch arguments
- the detected window title, which you can use to tighten `VLC_WINDOW_TITLE`

If `.env` exists, the script can write `VLC_WINDOW_RECT`,
`VLC_WINDOW_SHOW_CMD`, and detected `VLC_ARGS`. It does not automatically write
`VLC_WINDOW_TITLE`; set that manually if you want the Discord stream picker to
match a more specific title.

The bot applies saved VLC placement only when it launches VLC. If VLC is already
running, it leaves the existing process and window alone.

If capture cannot find a VLC window, use:

```powershell
py scripts\debug_vlc_windows.py
```

This prints VLC process IDs and any VLC-owned top-level windows visible from the
current Windows permission context. If VLC or Discord is running elevated, run
the bot and helper scripts with matching permissions.

## Discord Flow

1. A user runs `/remote` in a server text channel.
2. The bot replies with an ephemeral `iChangeChannels remote - Nav` UI. If
   nobody else has pressed remote buttons in the last 5 minutes, it locks the
   remote to that user.
   - If another user holds the lock, the UI still appears with its controls
     disabled and a message naming the current lock holder.
   - Admins configured in `DISCORD_ADMIN_USERS` bypass the lock entirely: they
     do not see lock timers or lock-holder messages, and their controls remain
     enabled. The `Take Control` button overrides the lock and makes the admin
     the current lock holder.
3. The user can switch between `Nav`, `Media`, and `Numpad` panels.
4. Any remote button press refreshes that user's 5-minute remote lock. When the
   lock expires, the existing ephemeral UI remains visible and can claim the
   remote again on the next button press.
5. The user clicks `Power On`.
6. The bot chooses the voice channel:
   - the requester's current voice channel, if they are in one;
   - otherwise, the first public voice or stage channel the stream account can join.
7. The bot saves the requester and channel details.
8. The bot checks whether the stream account is already in another voice channel.
9. The bot ensures Android TV is on and VLC is open.
10. The bot DMs the stream account a link in this format:

   ```text
   https://discord.com/channels/GUILD_ID/CHANNEL_ID
   ```

11. Desktop automation opens the DM in Discord desktop, clicks the exact join
    link or Discord's rendered `Join Voice` button, selects the VLC window in
    the stream picker, and clicks Go Live.
12. The bot waits for Discord voice state to confirm the account joined and is
    streaming.

## Strict Desktop Automation

Desktop automation intentionally has no coordinate, browser, or URL-opening
fallbacks. It uses Discord's quick switcher to open the configured DM, then must
find and click the expected Discord UI elements through Windows UI Automation:

- a Discord desktop window;
- the DM opened through `DISCORD_DM_SEARCH`;
- the exact join link text or Discord's rendered `Join Voice` button;
- `Share Your Screen` or `Share Screen`;
- the configured `VLC_WINDOW_TITLE` text in the stream picker;
- `Go Live` or `Start Streaming`.

If any step fails, the bot raises a stage-specific error, returns it to the
ephemeral interaction, and logs it. Fix the window state, permissions, title, or
Discord layout directly rather than masking the failure.

## Project Files

- `run.py`: starts the log window and Discord bot thread.
- `src/ichannel/config.py`: loads `.env` and validates configuration.
- `src/ichannel/bot.py`: defines `/remote`, the remote panels, and button behavior.
- `src/ichannel/orchestrator.py`: coordinates Power On, Power Off, status, locks, and Discord checks.
- `src/ichannel/android_tv.py`: connects to and controls Android TV.
- `src/ichannel/vlc.py`: starts VLC and applies saved window placement when launching it.
- `src/ichannel/discord_desktop.py`: drives Discord desktop through strict UI Automation.
- `src/ichannel/logging_ui.py`: shows live logs and writes rotating log files.
- `src/ichannel/state.py`: persists the active session in `data/state.json`.
- `scripts/pair_android_tv.py`: discovers/pairs Android TV devices and can update `.env`.
- `scripts/capture_vlc_window.py`: captures the live VLC window placement.
- `scripts/debug_vlc_windows.py`: diagnoses VLC window visibility.
- `requirements.txt`: Python dependencies.

## Troubleshooting

- If `/remote` does not appear, restart the bot with `COMMAND_SYNC_ON_START=true`
  and refresh Discord after the global command sync completes.
- If Android TV pairing fails, rerun `scripts\pair_android_tv.py` and make sure
  the TV is on the same network.
- If VLC is already running but in the wrong place, close VLC and let the bot
  launch it so saved placement can be applied.
- If Discord automation cannot see Discord or VLC, make sure the bot, Discord,
  and VLC are running in the same Windows desktop session and permission level.
- If the stream account is stuck in another channel, run Power Off from the
  active server before trying to Power On elsewhere.
