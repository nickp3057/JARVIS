# JARVIS TV — notes for whoever works on this TV next

This file lives on the TV at `/sdcard/JARVIS_TV.md` (read it with
`adb -s <tv>:5555 shell cat /sdcard/JARVIS_TV.md`) and in the JARVIS repo at
`tv/README.md`. `tv/launcher/deploy.sh` copies the repo version to the TV on
every deploy, so edit the repo copy, not the TV's.

## The TV

- TCL Google TV "Nick's Room TV", model G10_4K_US_NF, Android 12, Realtek SoC.
  UI is 1920x1080 at density 320 (960x540 dp).
- adb over Wi-Fi on port 5555; the Mac's key is authorized. The DHCP address
  drifts (10.0.0.44 until Oct 2026, 10.0.0.46 since). If it moved, scan:
  `for i in $(seq 1 254); do (nc -z -G1 10.0.0.$i 5555 && echo 10.0.0.$i) & done; wait`
- Inputs: PS5 on HDMI1, Switch on HDMI2, Wii U on HDMI3 (eARC). No soundbar.
- Nick uses it mostly for Plex (NAS at 10.0.0.147:32400) and Spotify.

## What runs on it

`com.tvhome` is the HOME app: JARVIS's home screen. Source:
`tv/launcher/` in the JARVIS repo (hand-built Java, no Gradle).

- **Home screen** (`HomeActivity`): greeting, reactor (port of the phone's
  `JarvisCoreMark`), clock; cards Next up / School / Systems / Music, each
  opens a detail list (Back closes). Bottom row: Plex, Spotify, Wake-up,
  Settings (corner). Back on the home screen hides the highlight.
- **Data**: JARVIS Core on the Optiplex (`http://10.0.0.81:8000`, DHCP too;
  the TV rescans its /24 if Core moves). `GET /api/glance` every minute and the
  live alert stream `GET /api/clients/events` (SSE). Token `nicks-room-tv`,
  scopes `events:read status:read chat settings:write wake:write
  playback:write` (never the admin token). Set by
  `tv/provision.sh` (run by Nick: SSH writes to the Optiplex need him).
- **Theme** (`ThemeStream`): follows Core's `/api/home/stream`, whose events
  carry the shared theme, so Barn Door turns the screen red at once (the 5 s
  `/api/theme` poll is the fallback).
- **Banners** (`AlertStream`, `Banner`): fresh warning/critical alerts appear
  top-right over any app, Plex included, without taking focus.
- **Answer screens** (`AnswerCard`, 9.5): `event: card` frames on the same
  stream (Core's `brain/answer_cards.py`: weather_today, calendar_day,
  timers) draw a readout on the right of the screen over any app, without
  focus, in the current theme; it fades out after the card's 45 s and a
  new card replaces it. Screen on only; unknown card types are skipped.
- **Play for Core** (9.8, 2026-11-15): a `tv_play` card ({rating_key, type,
  title}, 20 s TTL; light sync calibration's test clip) isn't drawn: the
  launcher wakes the screen, force-stops Plex over its own adb (Plex ignores
  a deep link while it's open), opens the title page and lets `PlexWatch`
  press Watch / Resume (`PlexWatch.playFresh`). Screen on or off.
- **Voice** (`Voice`, via `RemoteBridgeService`): "Jarvis, ..." on the remote
  mic. The Assistant hears it too; the launcher reads its transcript from
  logcat, sends the rest to Core `POST /chat`, shows the reply as a banner
  (read aloud only if turned on in Settings), and backs out of any Google app
  the Assistant opened. Other speech that names a Plex title opens it in Plex.
- **Light sync** (`PlaybackReport`, 9.9): the Core player uses a mapped TV
  monotonic clock. It watches Plex's MediaSession, advances position to
  elapsedRealtime and sends that timestamp, a process UUID, report sequence
  and previous four-stamp exchange in `POST /api/tv/playback` (scope
  `playback:write`). Three `GET /api/tv/clock` probes warm the pool and select
  the initial lowest RTT. Successful responses are drained and closed without
  disconnecting so HTTP sockets can be reused. Every change, 1s playing, 5s
  paused/buffering. Core filters queueing jitter and slews small position
  errors, never falls back to Plex timing while syncing. It releases the
  lights on stale TV heartbeat. Failures retry after 1s; 401/403 back off 5min.
- **Music** (`Music`, `MediaListener`): Spotify's media session (read and
  controlled via an enabled NotificationListener; no Spotify API keys).
- **Wake-up** (`Wake`, `WakeActivity`): Core keeps a weekly schedule (its
  own time per weekday, edited in the phone's Settings > Wake-up or by
  asking JARVIS) plus an optional one-off ("in 3 minutes"); music is a name
  Spotify searches, a link, or resume. At the time Core publishes a
  `wake_up` event; the TV turns on, goes home and plays through Spotify's
  media session (`playFromSearch` / `playFromUri`), so Spotify never takes
  the screen unless it hasn't run since boot. If it still isn't playing after
  the play presses (a stale session sitting paused on last night's track), it
  opens the link in Spotify itself (`am start VIEW`) and presses play. Volume goes straight to the
  alarm's level (no fade: Nick didn't like it creeping up), then
  `WakeActivity` shows the time with **I'm up** (stops the music and closes, 9.0;
  tells Core `POST /api/wake/awake`, which turns on the good-morning lights;
  Back does the same) and **Snooze 9 min** (pauses, then `Wake.run` again,
  resuming the song). The snooze is a main-looper Handler, so it's lost if
  the TV is switched off meanwhile. The last alarm handled is kept in prefs
  (`last_wake_at`): a replay after a restart must never wake Nick twice.
- **Every 5 s while home is showing** (`HomeActivity.moneyTick`): the shared
  theme (`/api/theme`, `Jarvis.followTheme`; a phone pick lands within ~5 s)
  and, when Core has a linked Schwab portfolio (`/api/glance` `portfolio`),
  the line under the weather with value and today's change in dollars and
  cents from `/api/portfolio` (Core streams the prices).
- **Continue** (`ContinueWatching`): Plex's Continue Watching straight from
  the NAS (`/hubs/continueWatching/items`, the TV's own Plex token): the
  newest on a bottom-row tile, the rest in its list (the newest focused);
  opening one goes to its Plex page (the show's, for an episode) with Resume
  focused, so it's OK, OK, OK from home to playing. Tested 2026-10-27 on a
  movie (Interstellar) and an episode (Suits). Refreshed with the glance.
- **Bottom row**: Plex, Spotify, Wake-up, Continue, Settings, all the same
  tile (gradient, accent outline, centred text), equal widths spread edge
  to edge, all built by `HomeActivity.wordTile`: a bold lower-case wordmark
  (`wordmark`/`setWordmark`, shrinks to fit) on one line, its spaced label
  on the next (plex / LIBRARY, spotify / MUSIC, 8:00am / WAKE-UP,
  interstellar / CONTINUE, settings / JARVIS), and small print (Tomorrow ·
  Back in Black, 2h 43m left) only while the tile is highlighted: the
  wordmark and label sit centred and slide up to make room for it. One character per wordmark is
  the theme accent: Plex's x (its logo's chevron) and Spotify's i, picked
  from the text itself for the rest so it doesn't jump around. Continue hides when Plex has nothing in
  progress.
- **Plex sign-in**: Nick stays signed in to his Plex profile, so the old PIN
  auto-entry is gone (2026-10-27); `PlexWatch` only presses Watch/Resume for
  "play ..." voice requests.
- **Phone snooze**: `wake_snooze` / `wake_dismiss` bus events (from the
  phone's notification buttons) pause and count down, or stop the music
  and close `WakeActivity` (9.0: I'm up used to leave it playing). Core
  rings a phone snooze again itself, and also sends `MEDIA_PAUSE` through
  HA on I'm up in case this stream is down.
- **Dark pool under the core (9.2, dark since 9.3)**: `PlasmaView.setAnchor`
  (set by the header core's layout listener) keeps a soft pool of the deep
  background under JARVIS and thins the flowing blobs there, so red + blue
  can't stack behind him. 9.4: a touch darker and bigger.
- **Barn Door fly-in, still plasma (9.1)**: entering Barn Door, a snapshot of
  the old home (taken in `HomeActivity.recreate()`) fades off the red home,
  the header's core flies to the middle growing into the spiky one, then the
  lockdown screen fades in behind it; standing down reverses it. Durations
  are divided by the TV's animator scale (0.5) so they play at full length.
  `NeuralView` drawn big adds a bloom pass. `PlasmaView` renders once at
  full resolution and holds still.
- **Neural core (8.6)**: when Core's glance/`/api/theme` says `core:
  "neural"` (Experimenting picked on the phone, or underneath Barn Door, with
  its Neural core switch on), the header draws `NeuralView` instead of
  `ReactorView`, and Barn Door draws it in the alert mood (red spikes)
  instead of `RedAlertView`. `NeuralNet` is a line-for-line port of the
  phone's `NeuralNetwork` (same seed, same shells, rings and moods); one
  shared instance, so recreating the screen for Barn Door morphs it rather
  than restarting it. A theme change fades the new screen in over 1.4 s
  (`HomeActivity.shownTheme`). `ThemeStream` refetches `/api/theme` when the
  stream's `look` moves, so flipping the switch on the phone reaches the TV
  at once. Both views implement `Beat.Dancer`, so Back in Black still bounces it.
  Every `NeuralView` on screen must ask for the same mood (`HomeActivity.mood()`):
  they share one mind. Barn Door's overlay fades in over the home and, on
  stand-down, a copy fades out over the restored home (no fade from black).
- **Experimenting lab (8.7)**: Core's glance/`/api/theme` `lab` lists the
  Experimenting switches showing. `plasma` puts `PlasmaView` (the phone's
  flowing red/blue, drawn at 1/8 resolution and stretched, ~0 janky frames)
  behind the home; `glass` draws cards and tiles as frosted glass
  (`HomeActivity.glassCard`: milky haze, sheen, bright rim; Android can't blur
  behind a view). `Jarvis.look` (core + lab) changing rebuilds the screen.
- **Themes**: Arc, Hot Rod, Malibu, Crimson, Mark 85, Spider-Man, McQueen
  ("Ka-chow, sir." and a ⚡ on the date), plus `barnDoor`, Barn Door
  Protocol's red alert, set by JARVIS and never in the picker. While it's
  on, `HomeActivity.addBarnDoor` covers the home screen with `RedAlertView`
  (the reactor gone hostile: turning armour, a ring of fangs, a racing
  dial, lock-on arcs and a perfectly round, flat lens at the centre (no
  glint), almost an eye but not one, that never moves and blinks once every 30 s; no
  pulsing, flashing or scanning, Nick's call), a "LOCKDOWN ACTIVE" line and
  "BARN DOOR PROTOCOL" across the bottom; no time or date. Home just comes
  back to it; only "stand down" (or a theme pick) ends it. Preview any
  theme without touching Core's: `adb shell am broadcast -n
  com.tvhome/.PreviewReceiver --es theme barnDoor [--ei seconds 20]`. The normal home keeps running underneath and returns on stand-down (`Theme.java`, dark variants of the phone's
  `JarvisPalette`). Mark III, Graphite and Stealth were retired 2026-10-27;
  Core maps a stored retired pick to Hot Rod / Arc.
- **Weather icon** (`HomeActivity.weatherIcon`): one emoji from the
  description: sun (moon at night), part-cloud, cloud, rain, snow, storm, fog.
- **Power and remote from the phone** don't touch this app: Core drives the
  TV through Home Assistant's Android TV Remote entity
  (`integrations/tv_remote.py`).
- **Easter egg** (`Beat`): while "Back in Black" plays, the reactor bounces,
  flares and spins with the audio (output-mix Visualizer, RECORD_AUDIO
  granted by deploy.sh; falls back to 94 BPM if the TV returns silence).
- **Sound fix** (`NightlyRefresh`): "off" on this TV is TCL suspend-to-RAM
  (`tcl_sys_pms_str_enabled=1`), and after long stretches audio resumes
  silent (TCL quirk NO_SOUND_AFTER_STR_RESUME). A minute after the TV is
  switched off, if it has been up 20 h+, it reboots with
  `svc power reboot quiescent` (stays dark, ~20 s). A plain `reboot` turns
  the screen ON; never use it at night.
- **Settings** (`SettingsActivity`): theme, read replies aloud, show
  downloads, nightly refresh, next wake-up, then Android settings. The theme
  is shared: the TV `PUT`s `/api/theme` (token scope `settings:write`) and
  the phone follows; `/api/glance`'s `theme` carries the phone's picks back.
  If the token can't write (provisioned before 6.5), the pick stays TV-only
  (`theme_override` pref) and is shared on the first refresh after
  `tv/provision.sh` is re-run.
- **Debloat** (`BootCleanup`): firmware restores some TCL apps every boot;
  the launcher removes them again via self-adb. `tv/restore.sh` undoes it all.

## Working on it

```bash
TV=10.0.0.46:5555 tv/launcher/deploy.sh   # build, install, re-grant, start, copy this file
TV=10.0.0.46:5555 tv/provision.sh         # Nick runs it: new token + Core URL
```

- Build needs `/opt/homebrew/share/android-commandlinetools` (build-tools 34,
  platform 31) and javac. Signing key and Plex token are outside git in
  `~/tcl-tv-cleanup`; the TV only accepts upgrades signed with that key.
- d8 34 can't read inner (non-static) classes compiled by javac 24: keep
  nested classes static, no anonymous classes.
- Bump `versionCode`/`versionName` in `AndroidManifest.xml` per change, and
  (repo rule) the iOS version fields on every commit.
- adb test hooks (DUMP-guarded, adb only):
  - `am broadcast -n com.tvhome/.NightlyRefresh --el in_ms 0 [--ez dry_run true]`
  - `am broadcast -n com.tvhome/.WakeTestReceiver [--es music spotify:...] [--ei volume 20]`
  - `am broadcast -n com.tvhome/.ProvisionReceiver --ez clear true` (unlink)
- Screenshots: `adb shell screencap -p /sdcard/x.png` then `adb pull`.
  `uiautomator dump` hangs on the home screen (the reactor never stops
  animating).

## TCL quirks (learned the hard way)

- Reinstalling resets `appops APP_AUTO_START` (TclAppBoot then blocks the
  service) and the notification-listener grant; `deploy.sh` re-applies both.
- TCL turns every app's wakeup alarm into a non-wakeup one
  (`ro.feature.alarm_wakeup_control`); app alarms can't wake the TV. Network
  traffic can (network standby), which is why wake-up comes from Core.
- Network standby isn't forever. 2026-10-31: switched off at 04:54, the
  TV's Wi-Fi dropped about once a minute, and at 06:26 the firmware took it
  from standby (STR, `ro.feature.power_str`) to a full power-off
  (`ro.feature.power_auto_standby_optimize`; the next boot reported
  `ro.boot.bootreason=cold,powerkey`), so the 09:00 alarm couldn't reach it.
  No setting for it shows up in `settings` or `getprop`. Since 8.4,
  `StayReachable` (from `RemoteBridgeService`) holds a partial wake lock and
  a low-latency Wi-Fi lock for as long as the TV has power, and `deploy.sh`
  whitelists the launcher from doze. Check after a night with
  `adb shell uptime` (a short uptime after an off night means it cold-booted
  again) and Core's journal for the TV's `/api/clients/events` reconnects.
  Core's wake-up alarm rings the room speaker if the TV still doesn't come on.
- The log buffer is 64 KiB: logs from a few minutes ago are gone.
- The launcher's own adb key is approved on the TV (self-adb). If the "Allow
  debugging?" prompt appears behind the launcher: `am task lock <id>`, then
  `am task lock stop`.
- `hdmi_earc_connected=1` is the Wii U on HDMI3, not a soundbar.

## Etiquette

Nick may be watching. Check `dumpsys activity activities | grep -m1 mResumedActivity`
and media sessions before injecting keys, waking or rebooting; ask first if
something is playing. Restore volume after any audio test.
