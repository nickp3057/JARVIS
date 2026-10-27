# JarvisCompanion redesign — plan (Phase 4, built 2026-10-26 except where noted)

Written 2026-10-25 from Nick's feedback: the chat bubbles ("chat backs") feel
off, JARVIS sounds scattered because a whole tool turn lands in one bubble,
replies didn't stream, and media adds sometimes fail. He leans toward a
hub-first app with chat as one tab.

## Already fixed on the server/app side (this branch)

- Streaming: `/chat/stream` sends `nosniff`, so short replies type out live.
- One bubble per model round (`X-Jarvis-Stream-Segments`, U+001E marker).
- Quieter tool turns (no pre-tool narration) and no guessed media adds
  (several matches → exact-title "yes" via the confirm gate).
- Media doctor endpoint and the alert stream exist for the app to use.

Check these on the phone first. Some of the "weird" may already be gone.

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

- Settings → Theme: Arc / Mark III / Hot Rod (`JarvisPalette`; Reactor removed in 1.87, Nick disliked the violet; Hot Rod added in 1.91). Mark III's red
  accent sits close to the red "critical" dot; revisit if that confuses.
- The reactor mark animates at display rate; check smoothness on device with
  Instruments → Animation Hitches (target: no hitches on the Chat empty
  state and Home header at 60 Hz). ProMotion 120 Hz would need
  `CADisableMinimumFrameDurationOnPhone`; deliberately not enabled.

## Tap-to-chat incident reports (2026-10-27, simulator tests pass)

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

- Home: hero reactor (220 pt, tap = briefing prompt in Chat), greeting by
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
- Not yet: Charles Schwab "today" glance on Home (Nick wants it eventually;
  needs a Schwab API integration on Core first).

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
