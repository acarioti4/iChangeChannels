# iChangeChannels Agent Memory

This file is the project memory for future agent work. Keep it aligned with
`README.md`; when behavior changes, update both where appropriate.

## Product North Star

iChangeChannels is a Windows-focused Discord bot that lets people in a Discord
voice channel control one shared Android TV/VLC stream without touching the
streaming PC, Discord desktop client, VLC, or the physical remote.

Primary user story:

> As a Discord server member watching with friends, I want to run `/remote` and
> control a shared Android TV stream from Discord, so that everyone in my voice
> channel can watch and navigate TV together without touching the streaming PC,
> Discord desktop client, VLC, or the physical remote.

The main track is:

1. User opens the Discord remote.
2. User claims temporary control.
3. User powers on the shared watch setup.
4. App ensures Android TV, VLC, Discord voice, and Go Live are all active.
5. Users control Android TV from Discord.
6. App powers down cleanly when requested or when nobody is watching.

## Scope Guardrails

This app is:

- A Discord remote for a single shared Android TV/VLC streaming setup.
- A coordinator for one dedicated stream account logged in on Discord desktop.
- A safety layer that keeps the active stream tied to one server/channel at a
  time.
- A practical automation tool for watch-party control.

This app is not:

- A general-purpose Discord music/media bot.
- A full home automation dashboard.
- A replacement Discord client.
- A multi-TV or multi-stream orchestration platform unless that scope is
  explicitly chosen later.
- A system that should hide desktop automation failures with coordinate,
  browser, or URL-opening fallbacks.

When considering new features, prefer work that strengthens the core loop:
Discord remote -> start shared VLC stream -> control Android TV -> clean
shutdown.

## Current Behavior To Preserve

- Registers a `/remote` slash command.
- Shows an ephemeral Discord remote UI to the user who ran `/remote`.
- Shows the ephemeral remote UI even when another user holds the lock; locked
  out controls are disabled and the UI names the current lock holder.
- Provides three remote panels:
  - `Nav`: power/status, directions, back, home, menu, volume, mute,
    play/pause.
  - `Media`: play/pause, stop, rewind, fast-forward, previous, next, channel,
    info, guide, settings, search.
  - `Numpad`: digits 0-9 plus back, home, and OK.
- Locks the remote to one user at a time and releases it after 5 minutes
  without button presses.
- Keeps the ephemeral remote UI visible when the 5-minute lock expires; the bot
  should not remove the controls solely because the lease ended.
- Allows configured admins to use the remote without lock timers, lock-holder
  messages, or disabled controls unless they click `Take Control`.
- Controls Android TV over WiFi using Android TV Remote Protocol v2.
- Persists the active session in `data/state.json`.
- Enforces one active stream location at a time.
- Blocks remote key presses from other servers while the stream account is
  active elsewhere.
- Shows live logs in a Tkinter window and writes rotating logs to `LOG_FILE`.

## Power Rules

Power On is complete only when all of these are true:

- Android TV reports powered on.
- VLC is running locally.
- The stream account is in the selected voice channel.
- Discord reports the stream account as Go Live streaming.

Power On should fix missing pieces without restarting or moving things that are
already correct.

Power Off should disconnect the stream account through the Discord API, then
power off Android TV if possible. It should not close VLC.

If the last viewer leaves and the stream account is alone in voice, the app
should disconnect the stream account, power off Android TV, and release the
remote lock.

If the stream account is manually disconnected outside the bot, the app should
clear its state and leave Android TV alone.

## Discord Flow

1. A user runs `/remote` in a server text channel.
2. The bot opens an ephemeral remote UI. If another non-admin user holds the
   lock, the UI remains visible but controls are disabled and the lock holder is
   named. Configured admins bypass this lock display and keep enabled controls
   unless they click `Take Control`.
3. Non-admin button presses, and admin presses after `Take Control`, refresh
   that user's 5-minute lease. When the lease expires, the UI stays visible and
   can claim the remote again on the next button press.
4. The user clicks `Power On`.
5. The bot chooses the requester's voice channel, or the first usable public
   voice/stage channel.
6. The bot records requester, guild, voice channel, join URL, and timestamp.
7. The bot refuses to move the stream account if it is already active elsewhere.
8. The bot ensures Android TV is on and VLC is open.
9. The bot DMs the stream account a Discord voice-channel link.
10. Desktop automation uses Discord desktop to join voice and start Go Live with
    VLC.
11. The bot waits for Discord voice state to confirm join and streaming.

## Strict Desktop Automation

Desktop automation should stay strict. It should use Discord's quick switcher to
open the configured DM, then find and click the expected Discord UI elements
through Windows UI Automation:

- Discord desktop window.
- DM opened through `DISCORD_DM_SEARCH`.
- Exact join link text or Discord's rendered `Join Voice` button.
- `Share Your Screen` or `Share Screen`.
- Configured `VLC_WINDOW_TITLE` in the stream picker.
- `Go Live` or `Start Streaming`.

Do not add coordinate, browser, or URL-opening fallbacks without an explicit
product decision. Stage-specific failures are useful because they point to
window state, permissions, title, or Discord layout problems.

## Project Map

- `run.py`: starts the log window and Discord bot thread.
- `src/ichannel/config.py`: loads `.env` and validates configuration.
- `src/ichannel/bot.py`: defines `/remote`, remote panels, and button behavior.
- `src/ichannel/orchestrator.py`: coordinates Power On, Power Off, status,
  remote locks, key limits, and Discord checks.
- `src/ichannel/android_tv.py`: connects to and controls Android TV.
- `src/ichannel/vlc.py`: starts VLC and applies saved placement when launching.
- `src/ichannel/discord_desktop.py`: drives Discord desktop through UI
  Automation.
- `src/ichannel/logging_ui.py`: shows live logs and writes rotating logs.
- `src/ichannel/state.py`: persists active session state.
- `scripts/pair_android_tv.py`: discovers/pairs Android TV and can update
  `.env`.
- `scripts/capture_vlc_window.py`: captures VLC window placement.
- `scripts/debug_vlc_windows.py`: diagnoses VLC window visibility.
- `tests/`: behavior and regression tests.

## Development Notes

- Prefer the existing small-module structure over broad refactors.
- Keep changes focused on the Discord remote, Android TV control, VLC startup,
  stream-account orchestration, and safe shutdown loop.
- Maintain clear user-facing status messages for partial failures.
- Preserve one-active-stream-location semantics unless intentionally redesigning
  the product.
- Update tests when changing lock behavior, power lifecycle behavior, desktop
  automation assumptions, config validation, or state persistence.
- Treat `.env`, certificates, keys, logs, and `data/state.json` as local runtime
  data. Do not commit secrets.
