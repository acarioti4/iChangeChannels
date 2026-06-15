# iChangeChannels

iChangeChannels is a Windows-focused Discord bot for controlling an Android TV
box and coordinating a logged-in Discord desktop account that streams VLC into a
voice channel.

The bot handles Discord API work directly and uses desktop automation only for
the actions Discord does not expose to bot accounts: making the logged-in
`mr.veeseeksbox` desktop account open a voice-channel link and start Go Live.

## Current Behavior

- Registers a `/remote` slash command.
- Sends an ephemeral Discord remote UI to the user who ran `/remote`.
- Locks the remote UI to one user at a time, then releases it after 5 minutes
  without button presses so another user can claim it.
- Provides three remote panels:
  - `Nav`: power/status, directional keys, back, home, menu, volume, mute, play/pause.
  - `Media`: play/pause, stop, rewind, fast-forward, previous, next, channel, info, guide, settings, search.
  - `Numpad`: digits 0-9 plus back, home, and OK.
- Controls Android TV over WiFi using the Android TV Remote Protocol v2.
- Records the Power On requester, guild, voice channel, join URL, and timestamp in `data/state.json`.
- Enforces one active stream location at a time:
  - `mr.veeseeksbox` cannot be moved to another guild or channel while already in voice.
  - Power Off must be run from the active server.
  - If `mr.veeseeksbox` is alone in voice, Power Off can disconnect it immediately from any server.
  - Remote key presses are blocked from other servers while the stream account is active elsewhere.
- Shows live logs in a Tkinter app window and writes rotating logs to `LOG_FILE`.

## Power Rules

Power On means all of these are true:

- Android TV reports powered on.
- VLC is running locally.
- `mr.veeseeksbox` is in the selected voice channel.
- Discord reports `mr.veeseeksbox` as Go Live streaming.

Power On only fixes missing pieces. For example, if VLC is already running and
the stream account is already in the correct channel and streaming, the bot does
not restart VLC or move the stream account.

Power Off disconnects `mr.veeseeksbox` from voice through the Discord API. It
does not close VLC and does not power off the Android TV.

If the last viewer leaves and `mr.veeseeksbox` is the only account left in the
voice channel, the bot automatically disconnects the stream account and releases
the remote lock immediately, even if the previous user's 5-minute idle timer has
not expired.

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
   - `ANDROID_TV_HOST` or run the pairing helper and let it update the host
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
STREAM_USERNAME=mr.veeseeksbox
DISCORD_DM_SEARCH=iChangeChannels
SYNC_COMMANDS_TO_GUILD_ID=
COMMAND_SYNC_ON_START=true

ANDROID_TV_HOST=192.168.1.50
ANDROID_TV_CERTFILE=data/androidtv_cert.pem
ANDROID_TV_KEYFILE=data/androidtv_key.pem
ANDROID_TV_CLIENT_NAME=iChangeChannels

VLC_PATH=C:\Program Files\VideoLAN\VLC\vlc.exe
VLC_ARGS=dshow:// :dshow-fps=60 --preferred-resolution=1080p
VLC_PROCESS_NAMES=vlc.exe,vlc
VLC_WINDOW_TITLE=VLC media player
VLC_WINDOW_RECT=
VLC_WINDOW_SHOW_CMD=

POWER_ON_TIMEOUT_SECONDS=45
ANDROID_TV_POWER_TIMEOUT_SECONDS=12
DISCORD_JOIN_TIMEOUT_SECONDS=20
DISCORD_STREAM_TIMEOUT_SECONDS=25
VLC_START_TIMEOUT_SECONDS=8
DESKTOP_AUTOMATION_ENABLED=true

DATA_DIR=data
LOG_FILE=data/ichannel.log
```

Use `SYNC_COMMANDS_TO_GUILD_ID` during testing for fast slash-command sync. Leave
it blank for global command sync.

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
2. If nobody else has pressed remote buttons in the last 5 minutes, the bot
   replies with an ephemeral `iChangeChannels remote - Nav` UI and locks the
   remote to that user.
3. The user can switch between `Nav`, `Media`, and `Numpad` panels.
4. Any remote button press refreshes that user's 5-minute remote lock.
5. The user clicks `Power On`.
6. The bot chooses the voice channel:
   - the requester's current voice channel, if they are in one;
   - otherwise, the first public voice or stage channel the stream account can join.
7. The bot saves the requester and channel details.
8. The bot checks whether `mr.veeseeksbox` is already in another voice channel.
9. The bot ensures Android TV is on and VLC is open.
10. The bot DMs `mr.veeseeksbox` a link in this format:

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
fallbacks. It must find and click the expected Discord UI elements through
Windows UI Automation:

- a Discord desktop window;
- the DM found through `DISCORD_DM_SEARCH`;
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

- If `/remote` does not appear, set `SYNC_COMMANDS_TO_GUILD_ID` to a test server
  ID and restart the bot for immediate guild-level sync.
- If Android TV pairing fails, rerun `scripts\pair_android_tv.py` and make sure
  the TV is on the same network.
- If VLC is already running but in the wrong place, close VLC and let the bot
  launch it so saved placement can be applied.
- If Discord automation cannot see Discord or VLC, make sure the bot, Discord,
  and VLC are running in the same Windows desktop session and permission level.
- If the stream account is stuck in another channel, run Power Off from the
  active server before trying to Power On elsewhere.
