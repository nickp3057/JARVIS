# JarvisCompanion redesign — plan (Phase 4, built 2026-10-26 except where noted)

Written 2026-10-25 from Nick's feedback: the chat bubbles ("chat backs") feel
off, JARVIS sounds scattered because a whole tool turn lands in one bubble,
replies didn't stream, and media adds sometimes fail. He leans toward a
hub-first app with chat as one tab.

## Donna mode (6.2 / build 244, 2026-11-10)

Hold the JARVIS logo on Chat (title logo or the empty-chat orb, 0.8 s) to
open Donna, a private text-only counsellor (see JARVIS.md). Rose theme
(`DonnaTheme`), the ring morphs into a line, the overlay covers the tab bar.
No Face ID for now (Nick, 2026-11-10). Not yet seen on a device: the
morph, keyboard handling inside the overlay, check-in notifications.

## Home in the middle, one-page Home, glass Settings (6.1 / build 243, 2026-11-09)

Nick's review of 6.0: shopping list approved as is. Phone tabs back to
Media · Control · Home · Chat · Settings (Home centred, still default). Home
fits without scrolling: 160 pt orb, and the scene picker and a Charles Schwab
half-tile share the last row. The scene icon has no dark disc when nothing is
picked. Settings rows didn't respond on the phone: RootView's iPad-only swipe
and idle-tap gestures (ancestor `simultaneousGesture`s) swallowed List row
selection; they're now masked to `.subviews` on the phone, and Settings is a
ScrollView of glass cards (`jarvisSurface`) with plain-styled links.

## Phone reorganization + Lists (6.0 / build 242, 2026-11-09, branch `phone-reorg`)

Nick asked for a full reorg modelled on clean apps, with maintenance, to-dos
and a new photo shopping list kept separate (and no dedicated checklist tab).

- Tabs: Home · Control (was Hub) · Media · Chat · Settings; Home first.
- Home: reactor, Lists row (To do / Shopping / Maintenance tiles → pushed
  lists), next-up strip, scenes, portfolio. To-dos no longer include due
  chores; maintenance has its own sectioned list.
- Shopping: `brain/shopping.py` catalog + `on_list`; suggestions while
  typing, photos per item, tick off with Undo, Saved items grid, JARVIS tools.
- Control: attention and repairs first, camera/TV remote as a two-up row,
  switches, Now (without reminders).
- Settings: native grouped list with drill-down pages.
- Verified: Python suite and 44 simulator tests pass; Home rendered in the
  simulator against a stub Core (counts and next-up strip load). Not yet
  checked on device: list screens, photo capture, the iPad/Mac layouts.
- Open questions for Nick: keep Settings as a tab or move it behind a gear on
  Home; group the shopping list by store (current) or by aisle/category.

## Phone live voice (5.77 / build 220, 2026-11-03)

Hold the Home orb for 0.3 s; release to send. Slide away or leave the tab during
capture to cancel. Single tap stays inert and triple-tap Barn Door stays.
Native phone recognition streams partial transcripts to the Optiplex, with an
HQ acknowledgement in the voice panel. Release finalizes the last word, binds
to the current Chat session/request ID, then streams text plus PCM speech back.
Playback starts on received chunks, not after downloading a full recording.
Core’s existing ElevenLabs voice is used; Piper is the bounded fallback.

`PushToTalkController`, `LiveVoiceConnection`, `VoiceAudioPlayer` and
`PushToTalkView` own the client adapters. `VoiceInputController.finish()` stops
capture immediately and allows a bounded final recognition result. Native hold
ended/cancelled states keep a cancelled gesture from sending. Voice shares
Chat’s history and receipts, preserves typed drafts, ignores superseded
callbacks and stops on backgrounding/interruption. First-time speech/microphone
permission prompts may cancel a hold; grant permission then hold again. Stop
ends reception/playback; an action already committed at HQ can still finish.
This first version exposes the hold gesture on phone Home only; iPad Room
continues to follow the room microphone. All 38 simulator tests passed; Home
navigation render inspected. Live Core smoke verified actual streamed PCM,
transcript acknowledgement, cancel and receipt recovery. Signed 5.77 (220)
installed on Nick’s iPhone 17 Pro; actual microphone/hold feel remains a device
check. iPad stays on 5.76.

## Chat chrome and orb taps (5.76 / build 219, 2026-11-03)

Home’s one-tap briefing is removed; triple-tap Barn Door remains. Chat’s
navigation bar and composer surroundings are transparent. Its connection row
appears for connection issues or an active reply, without a dark strip. The
phone tab shell ignores the keyboard safe area while the selected hosting page
handles input avoidance; keyboard notifications hide/restore the tab row so it
never lifts above the keyboard. Simulator coverage includes actual text-field
focus, unchanged shell height, input lifting and tab-row restoration. All 35
iOS simulator tests passed and Chat renders were inspected. Signed 5.76 (219)
installed and reopened on Nick’s iPhone 17 Pro and iPad Air.

## Orb sizing (5.75 / build 218, 2026-11-03)

Home’s hero is 190 pt (was 170); accessibility sizes retain 130 pt. The shared
classic reactor caps arc thickness and tick depth at the phone’s 190 pt
reference, and uses an ultra-light waveform at larger sizes. Room keeps its
responsive orb size with the phone’s finer line treatment. The existing visual
snapshot test passed and the phone navigation render was checked; signed
5.75 (218) installed on Nick’s iPhone 17 Pro and iPad Air.

## Phone swipe review (5.74 / build 217, 2026-11-03)

Phone tabs use native `UITabBarController` pages with a percent-driven slide.
The outgoing and incoming pages travel together, retaining navigation and scroll
state; distance and release velocity decide completion, and cancellation returns
to the original page. Reduce Motion switches instantly on release. The tab bar,
left-edge back gestures, sliders and horizontal content scrolling retain control.
iPad/Mac retain their existing shell; idle work is bumped on release rather than
every drag update. `PhoneTabsTests` exercises native completion/cancellation,
persistent page identity and Reduce Motion. All 34 simulator tests passed,
and the rendered navigation layout was inspected. Signed 5.74 (217) was
installed on Nick’s iPhone 17 Pro; physical-device feel still needs Nick’s check.

## Already fixed on the server/app side (this branch)

- Streaming: `/chat/stream` sends `nosniff`, so short replies type out live.
- One bubble per model round (`X-Jarvis-Stream-Segments`, U+001E marker).
- Quieter tool turns (no pre-tool narration) and no guessed media adds
  (several matches → exact-title "yes" via the confirm gate).
- Media doctor endpoint and the alert stream exist for the app to use.

Check these on the phone first. Some of the "weird" may already be gone.

## Scenes and the wake alarm screen (4.4–4.6, compiled against iphoneos, not run)

- Shared by Home (phone) and Room (iPad): `Views/HomeControls.swift`
  (`SceneTile`, regular and `large`; `LightSheetID`). Model decoding for
  entities/lights/scenes/voice is covered by `HomeModelTests` (simulator).

- Home tab: a **Scenes** card under the tiles, every saved HA scene as a
  tile, the last one run lit (like picking a Focus). Tap runs it.
- Tapping the wake-up push opens `WakeRingingView` (Snooze / I'm up).
- iPad: a **Room** tab (default on iPad) — clock, next alarm/event, TV
  remote, keep-screen-on, big device tiles, scenes. No camera/security yet
  (Nick, 2026-10-28). 4.8 adds long-press brightness/colour (`LightSheet`)
  and the night screen (black + brightness 0 after dark with the lights off).
  Later: the same light sheet on the phone's Home tab, a music card, effects
  (the Govee scenes HA lists in `effect_list`). 5.30 adds hold-and-drag
  dimming / fan speed on the tiles (`LevelDragger`), `FanSheet`, and a JARVIS
  volume slider (room speaker) in the clock panel. The phone's Home tab
  still uses plain long-press for the light sheet.
  5.31: the hold expands into a big level bar; full screen locks the iPad to Room.
  5.32: drag without holding (landscape); hold-and-release opens the same big
  panel to use (`LevelPanel`, colours too); Barn Door orb = the TV's red-alert reactor.
  5.33: polling trimmed: no light/theme polls while the home stream is up, Room
  skips Hub-only sections, portfolio every 5 s only in market hours.
- Next: an iOS Home Screen / Control Center widget for scenes (needs a
  widget extension target and an App Group for the server address/token).

## Today's structure

`RootView`: TabView with Chat (default, tab 0), Hub ("Control Room": a
Today / Media / Home segmented picker over cards), Settings. Chat history is a
sheet; settings is reachable from three places (tab, chat toolbar, hub
toolbar).

## Built 2026-10-26 (uncompiled in the cloud session — build in Xcode)

- Tabs: Home (default) / Media / Chat / Settings (`RootView`); Hub focus
  picker and the Hub's settings button only appear in the legacy non-tab use.
- Chat restyle: no per-bubble "JARVIS · time" header (one caption under the
  turn), flat panel/blue fills, no shadow, tail corner only on a turn's last
  bubble, tight 4 pt spacing between round bubbles.
- Not done: conversation list as Chat's root (history stays a sheet) and a
  live SSE reader (the Hub polls `/api/alerts/recent` instead).

## Apple HIG pass (2026-10-27, compiled against the iphoneos SDK, not run)

- `JarvisTheme` colours are adaptive (dark = original cockpit look, light =
  AA-contrast daylight variants); the forced `.preferredColorScheme(.dark)` is
  gone, so the app follows the system appearance.
- Tracked, uppercase, monospaced micro-labels became sentence-case Dynamic Type
  styles (panel titles, pills, metrics, connection state, composer hints).
  Code-block language tags keep monospace.
- Opaque `toolbarBackground` overrides removed from the tab bar and nav bars so
  they use the system material; Home/Media use large titles.
- Decorative grid backdrop and heavy panel shadow dropped; panel titles are
  header-trait elements; send button has a soft haptic.
- Chat composer rebuilt (2026-10-27): rides `safeAreaInset` instead of a
  `VStack` row so the keyboard no longer resizes the whole column; one 44 pt
  grid (field, mic, send inside a single rounded box); the animated
  scroll-on-focus is gone. The keyboard "Done" bar was tried and removed
  (1.85): on iOS 26 it floats over the composer, covering half of it and the
  send button. Dismiss is interactive (swipe down) or a tap on the transcript.
  A clearly horizontal swipe (not from the left edge) moves to the
  neighbouring tab (`RootView`): the page follows the finger, rubber-bands at
  the ends, and the new tab slides in (instant with Reduce Motion). JARVIS
  replies have no avatar and use the full width (1.85); iOS 18 pins the bottom via `defaultScrollAnchor(for:
  .sizeChanges)`, iOS 17 gets one un-animated scroll.
- Check on device: light mode on every tab, AX5 Dynamic Type on Home, and
  cyan-on-panel contrast in light mode.

## Themes and motion (1.86, simulator tests pass)

- Settings → Theme (3.2): Arc, Hot Rod, Malibu (sunset orange, ocean blue,
  cyan core), Crimson (calm red on charcoal), Mark 85 (gold and crimson on
  navy; Nick's favourite), Spider-Man (suit red, web blue) and Lightning
  McQueen (3.5: lightning yellow and candy red on asphalt charcoal, "Ka-chow,
  sir." and a ⚡ on Home). Mark III, Graphite and Stealth were tried and
  retired in 3.2. `barnDoor` (Barn Door Protocol's red alert) is set by
  JARVIS only and hidden from the picker (`JarvisPalette.pickable`). Earlier: (`JarvisPalette`; Reactor removed in 1.87, Nick disliked the violet; Hot Rod added in 1.91). Mark III's red
  accent sits close to the red "critical" dot; revisit if that confuses.
- The reactor mark animates at display rate; check smoothness on device with
  Instruments → Animation Hitches (target: no hitches on the Chat empty
  state and Home header at 60 Hz). ProMotion 120 Hz would need
  `CADisableMinimumFrameDurationOnPhone`; deliberately not enabled.

## Tap-to-chat incident reports (2026-10-27, simulator tests pass)

- 5.69–5.72 (2026-11-03): the card condenses repeats of one problem into one
  row with a "3×" count (Core's `repeats`), and its ↻ button calls
  `POST /api/alerts/refresh` so fixed alerts drop off and real ones stay
  (a Clear button shipped in 5.68 and was replaced).
- Home → Needs attention rows are buttons: a tap shows a spinner, calls
  `POST /api/alerts/{id}/report`, and drops the returned prompt (report id,
  failing checks, suspected cause) into Chat's composer via `onAsk`. Any
  failure (older Core, restarted server, NAS down) uses
  `HubViewModel.fallbackPrompt` built from the alert itself.
- Home → Repairs card (1.83): pending fixes with Approve / Dismiss (the tap is
  the approval), running ones show a clock, finished ones their verified
  result. Polls `/api/repairs` with the fast sections.
- Not yet: tapping the push notification itself (needs the alert id in the
  APNs payload).

## Maintenance + Home cleanup (1.88, simulator tests pass)

- Home → Maintenance card after Now: the soonest four tasks (overdue red, due
  today / this week amber), a check button opens "Mark done" (optional note,
  optional earlier day), the row pushes `MaintenanceDetailView` (countdown,
  Mark done, swipe-to-delete history, edit/delete menu). "All N tasks" pushes
  the full list; "Add" opens `MaintenanceEditor`. Views in
  `Views/MaintenanceViews.swift`.
- The Now feed lists overdue and due-within-a-week chores under MAINTENANCE.
- Home no longer repeats Media's Launch links or Media drive cards. The
  legacy non-tab "Control Room" mode (focus picker, hub settings button,
  `.home` focus) is removed; `HubView(focus: .today | .media)`.

## Home as JARVIS (1.89, simulator tests pass)

- Home: hero reactor (190 pt as of 5.75; one-tap briefing removed in 5.76), greeting by
  time of day, date, and chips for systems and free space. The chips call
  `onOpenMedia`; `HubViewModel.mediaScrollTarget` tells the Media tab which
  card to scroll to and outline. Home hides its navigation bar; pushed
  maintenance screens keep theirs.
- Home now holds only: hero, Needs attention, Repairs, Now, Maintenance,
  quick actions. Services moved to the bottom of Media.
- Theme: status colours are palette roles; Mark III is gold accent / red
  secondary / orange warning / reactor-blue success.

## Home / Hub split (1.90, simulator tests pass)

- Tabs: Home / Hub / Media / Chat / Settings (`RootTab`). `HubView(focus:)`
  is `.home`, `.hub` or `.media`; Home's chips call `onNavigate`.
- Home: hero, Next up (soonest assessment day), To do (open reminders + due
  maintenance; quick-add field; check = gone, optimistic via
  `finishedTodoIDs`). Hub: Needs attention, Repairs, Now, Maintenance, Home
  switches.
- 1.91: tabs reordered Media / Hub / Home / Chat / Settings. Next up and
  To do became half-width tiles (To do = count + "+", list in `TodoSheet`).
  Hub's Now caps assessments at seven within 14 days and drops maintenance;
  the Maintenance card is a one-line link into `MaintenanceListView` (swipe
  right = done). Home switches are three-across tap tiles. Reactor glow no
  longer pulses. Chat's suggested prompts removed. Third theme Hot Rod
  (Mark III + cyan `reactor` role for the mark and control tint).
- Home (3.0): no "tap the reactor" hint; the storage chip is the success
  colour like Systems; the TV remote lives at the top of Hub, not on Home.
- Charles Schwab portfolio card on Home (2.5): live value, today, yesterday,
  cash; Log in when Schwab's weekly login is due. Settings > Charles Schwab.

## Proposed structure: hub-first

| Tab | Content |
|---|---|
| **Home** (default) | Status strip (NAS / arr / qBit / HA health from `/api/status/services`), "Needs attention" list from the alert stream, Now card, quick actions, an "Ask JARVIS" field that opens Chat with the text. |
| **Media** | Downloads (live progress), recently added, and a **Request** flow: search → pick → quality → confirm, calling the same server tools via chat-less endpoints (see below). Each stuck item has a "Why?" button → `/api/status/media/diagnose`. |
| **Chat** | Conversations list as the root (not a sheet), thread view pushed from it. |
| **Settings** | Only place for settings (remove the duplicate toolbar buttons). |

### Chat look

- Drop the per-bubble "JARVIS · time" header; show time on long-press or as a
  group footer. Header-in-every-bubble is the likeliest source of
  "chat backs feel weird".
- Flatter JARVIS bubbles (solid panel colour, no drop shadow); keep the
  accent-tinted user bubble. Consecutive round bubbles group tightly (6 pt),
  turns separate more (14 pt).
- Tool activity as a small inline chip ("Searching Radarr…") while a round is
  silent, driven by a server status line in the stream. Needs one new event
  type; otherwise reuse the round marker.

### Server work this needs (small, all on jarvis-api)

1. `GET /api/clients/events` already exists. Give the companion a reconnecting
   SSE reader to power "Needs attention" and live badges.
2. **Built (2026-10-26):** media request endpoints so the Media tab doesn't
   need chat (`integrations/media_requests.py`, admin token):
   `GET /api/media/search?q=&kind=movie|show` → `{results: [{kind, id, title,
   year, in_library, overview, poster}], error?}`;
   `POST /api/media/requests {kind, id, quality?}` → `{status:
   "needs_confirmation", code, description}` (or `invalid` / `exists`),
   nothing written; `POST /api/media/requests/confirm {code}` → `{status:
   "added"|"failed"|"invalid"|"expired", message}`. Always two-step through
   `brain/confirm_gate.py`, in its own session namespace so a chat "yes" can't
   approve it. The app shows the description and sends the code on tap.
   **App side built too (2026-10-26):** Hub → Media → "Request a movie or
   show" sheet (`MediaRequestView` / `MediaRequestModel`), ahead of the full
   tab restructure. Not compiled in the cloud session; build in Xcode.
3. **Built (2026-10-26):** tool status in `/chat/stream` (U+001D-delimited,
   opt-in with the segments header), shown in place of the typing dots.

### Rough cost

Server endpoints + tests ~$6–8; SwiftUI restructure (tabs, conversation list
as root, Media tab, SSE reader, bubble restyle) ~$15–25 because it can't be
compiled here, so it needs careful small steps and Nick building in Xcode
between them.

## Questions for Nick before building

1. "Chat backs": the bubble backgrounds/borders, or the back navigation?
   A screenshot settles it.
2. Should Home be the default tab, or should the app open where you left it?
3. Media requests from the Media tab without chat: wanted, or is chat fine?
