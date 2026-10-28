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
  scopes `events:read status:read chat` (never the admin token). Set by
  `tv/provision.sh` (run by Nick: SSH writes to the Optiplex need him).
- **Banners** (`AlertStream`, `Banner`): fresh warning/critical alerts appear
  top-right over any app, Plex included, without taking focus.
- **Voice** (`Voice`, via `RemoteBridgeService`): "Jarvis, ..." on the remote
  mic. The Assistant hears it too; the launcher reads its transcript from
  logcat, sends the rest to Core `POST /chat`, shows the reply as a banner
  (read aloud only if turned on in Settings), and backs out of any Google app
  the Assistant opened. Other speech that names a Plex title opens it in Plex.
- **Music** (`Music`, `MediaListener`): Spotify's media session (read and
  controlled via an enabled NotificationListener; no Spotify API keys).
- **Wake-up** (`Wake`, `WakeActivity`): Core keeps a weekly schedule (its
  own time per weekday, edited in the phone's Settings > Wake-up or by
  asking JARVIS) plus an optional one-off ("in 3 minutes"); music is a name
  Spotify searches, a link, or resume. At the time Core publishes a
  `wake_up` event; the TV turns on, goes home and plays through Spotify's
  media session (`playFromSearch` / `playFromUri`), so Spotify never takes
  the screen unless it hasn't run since boot. Volume goes straight to the
  alarm's level (no fade: Nick didn't like it creeping up), then
  `WakeActivity` shows the time with **I'm up** (closes; music plays on;
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
  to edge, and all read the same way: the big thing on top, its spaced
  label under it, small print last (plex / LIBRARY, 8:00am / WAKE-UP /
  Tomorrow · Back in Black, Interstellar / CONTINUE / 2h 43m left, Settings
  / JARVIS). The wordmarks' accent letter (Plex's x, its logo's chevron;
  Spotify's i) is the theme accent. Continue hides when Plex has nothing in
  progress.
- **Plex sign-in**: Nick stays signed in to his Plex profile, so the old PIN
  auto-entry is gone (2026-10-27); `PlexWatch` only presses Watch/Resume for
  "play ..." voice requests.
- **Phone snooze**: `wake_snooze` / `wake_dismiss` bus events (from the
  phone's notification buttons) pause and count down, or close
  `WakeActivity`. Core rings a phone snooze again itself.
- **Themes**: Arc, Hot Rod, Malibu, Crimson, Mark 85 (Nick's pick),
  Spider-Man, McQueen (`Theme.java`, dark variants of the phone's
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
- The log buffer is 64 KiB: logs from a few minutes ago are gone.
- The launcher's own adb key is approved on the TV (self-adb). If the "Allow
  debugging?" prompt appears behind the launcher: `am task lock <id>`, then
  `am task lock stop`.
- `hdmi_earc_connected=1` is the Wii U on HDMI3, not a soundbar.

## Etiquette

Nick may be watching. Check `dumpsys activity activities | grep -m1 mResumedActivity`
and media sessions before injecting keys, waking or rebooting; ask first if
something is playing. Restore volume after any audio test.
