# Jarvis Companion (iOS)

A native SwiftUI companion for JARVIS Core, with synchronized Chat, native
dictation, and Home and Media dashboards. Layered navy telemetry, cyan glass
panels, and amber activity share the desktop cockpit aesthetic; text scales
with Dynamic Type.

Requires iOS 17+ and Xcode 16 or newer.

## Opening the project

```bash
open ios/JarvisCompanion/JarvisCompanion.xcodeproj
```

The `JarvisCompanion` scheme is shared and preselected. There are no packages to
resolve and no `pod install` step.

The app target uses a file-system-synchronized group, so anything you drop into
`JarvisCompanion/` is picked up automatically — no need to add files to the
project manually.

## Running on the Mac

The same target builds for Mac Catalyst (`SUPPORTS_MACCATALYST`, macOS 14+,
same bundle ID so pushes reach it too). In Xcode pick **My Mac (Mac
Catalyst)** and Run; the first time, let Xcode register the Mac in the
developer account (it signs with `JarvisCompanion-Mac.entitlements`:
sandbox, network client, microphone, push). Then **Product › Archive ›
Distribute App › Custom › Copy App** gives a `.app` for `/Applications`.

On the Mac, Desk is the first tab (everything at once in columns, with
weather, the agenda and an ask bar); Room and Home are iPad/iPhone only,
and the wall-panel habits (night screen, keep awake, full screen,
tap-to-wake, back to Room) are off.
The menu bar gets a **Go** menu (⌘1–⌘6 for the tabs) and ⌘, opens Settings.
Connect it exactly like the phone: `http://100.69.8.11:8000` and the API
token (it has to be on Tailscale away from home).

## First launch

The app ships with no server baked in. On first launch you get a "Set up your
server" prompt:

1. Tap **Open Settings** (or the **⋯** menu in the top right → **Settings**).
2. **Server** — the base URL of JARVIS Core, *without* the `/chat` path, e.g.
   `http://100.92.14.7:8000`. A bare `host:port` works too; `http://` is
   assumed. A trailing slash is fine.
3. **API token** — the bearer token JARVIS Core expects. Sent as
   `Authorization: Bearer <token>` on every request.
4. The **Status** row reads "Ready" once both are usable, and shows the exact
   endpoint the app will POST to. Worth a glance — it's the fastest way to catch
   a typo'd Tailscale IP.

The address is stored in `UserDefaults`; the token is stored in the iOS
Keychain. Existing installations automatically migrate the legacy
UserDefaults token and remove the insecure copy.

## Navigation

Hub-first (1.77+): **Home** (default), **Media**, **Chat**, **Settings** are
peer tabs. Home and Media are dedicated `HubView`s (`isTab: true`) over one
shared `HubViewModel`; polling is reference-counted (`retainPolling` /
`releasePolling`) so switching between them never stops it. Home shows Needs
attention, Now, the briefing shortcut, quick actions, storage, services and
links; Media shows the Request button, storage, transfers, queues (with
**Why?**) and links. The briefing shortcut opens a draft in Chat for review,
preserving any draft already there. Settings has its own tab (Chat's menu keeps
a shortcut for first-run setup) and includes a connection test, secure token entry, study
notification controls, and the current iOS notification permission status.

## Architecture

This is a **thin client** — deliberately so. JARVIS Core owns the personality,
conversation context and history, the memory tier, and all 45+ tools (Home Assistant,
Spotify, Radarr/Sonarr, timers, and the rest). The app knows none of that. It
sends one string and renders one string back; if a turn involved six server-side
tool calls, the app is none the wiser, with a three-minute streaming timeout (30 seconds for ordinary API requests). The layering is: `JarvisAPIClient` (the only type that knows
the wire format) → `ChatViewModel` (owns the transcript and the single in-flight
request) → `ChatView`/`SettingsView` (dumb rendering). Persistence sits behind
the `ChatStore` protocol with a plain JSON-file implementation. It is an offline
cache of server conversations and migrates the original single-transcript
format automatically. Server history is synchronized whenever health checks
succeed.

```
JarvisCompanion/
  JarvisCompanionApp.swift     app entry, wires settings + view model
  Models/ChatMessage.swift     transcript line + local conversation/archive models
  Storage/ChatStore.swift      ChatStore protocol + JSON archive implementation
  Storage/AppSettings.swift    base URL + Keychain-backed token
  Networking/JarvisAPIClient.swift   health, sessions, chat + streaming
  ViewModels/ChatViewModel.swift     history sync, drafts, streaming, retry
  ViewModels/VoiceInputController.swift  permission-gated native dictation
  Views/ChatView.swift         transcript, composer, empty state
  Views/MessageBubble.swift    bubbles + typing indicator
  Views/SettingsView.swift     server configuration
```

Failed turns become an inline error notice in the transcript rather than an
alert, with a **Try again** button that re-sends without retyping. That retry
survives an app restart — the pending text is recovered from the transcript.

Use **⋯ → Send to AI to debug** to submit the current conversation, or
long-press a particular JARVIS response to focus the report on it. Failed-turn
notices also show a visible debug button. A review sheet requires a description
of what went wrong and explains exactly what will be included before anything
is uploaded, shows progress while sending, and confirms the saved report ID.
Presentation is deferred until menus finish dismissing so the sheet opens
reliably. The authenticated report contains at most 100 current-conversation
messages plus app version, device type, OS version, connection state, and the
selected response ID. It never contains the API token or server address. JARVIS
Core stores reports separately from personality memory for later inspection.
If Core predates `/debug-reports`, the app explicitly asks for a server update
instead of leaving the action looking inert.

## Local chat history

The **history** button opens synchronized conversations. Search matches titles
and message text. Swipe or long-press a row to rename or delete it. **New chat**
starts a blank, isolated server session without deleting older conversations.
Long-press a message to copy or share it.

When the app remains inactive or closed for at least five minutes, returning to
it starts a clean conversation automatically. The previous conversation stays
in History. Short app switches keep the current chat open. Drafts are saved per
conversation. Replies stream into the JARVIS bubble as they arrive; the send
button becomes Stop while generation is active. Stream fragments are delivered
to SwiftUI in short batches, and the archive is persisted at turn boundaries,
so long histories do not trigger a full JSON rewrite for every character.

The connection banner reports server reachability only. Sending a chat no
longer changes a known-good connection to “Connecting…”; the typing indicator
and Stop button communicate per-turn activity independently.

Each queued Radarr/Sonarr item in Hub → Media has a **Why?** button that
opens `MediaDiagnosisView` (`GET /api/status/media/diagnose`): the stalled
stage in plain English plus every stage checked. Read-only; fixes stay in chat
behind the confirm gate.

Hub → Media → **Request a movie or show** opens `MediaRequestView`: search
Radarr/Sonarr (`GET /api/media/search`), tap a result to open a server-side
proposal (`POST /api/media/requests`, nothing added yet), then **Add** in the
confirmation dialog sends its code (`POST /api/media/requests/confirm`). No
model in the loop, so no guessed titles; default quality comes from the
server's `*_DEFAULT_QUALITY`.

Streamed replies are split into one bubble per model round: the app sends
`X-Jarvis-Stream-Segments: 1` and the server puts U+001E between rounds (also
saved in history, so synced transcripts split the same way). `MessageBubble`
renders each segment as its own bubble; copy/share and the chat-list preview
join them with plain whitespace. Older servers ignore the header. With the same
header the server also sends `U+001D<status>U+001D` while tools run ("Searching
Radarr"); `ChatViewModel.appendChunk` strips it (even when split across
chunks) into `toolStatus`, which replaces the typing dots until text resumes.
It is never stored in history.

For rolling upgrades, a missing `/chat/stream` route falls back to `/chat`
with the same session and request ID. A missing session is surfaced as an
error rather than silently starting unrelated context. Old servers can still
answer, but exact-ID recovery/replay protection requires the updated Core.

Streaming turns use a three-minute timeout because local tool chains can be
silent while Radarr, Sonarr, Home Assistant, or another service works. After a
stream completes or disconnects, the companion reconciles the answer with
Core's authoritative session history. If Core finished successfully, that full
response replaces a temporary stream error automatically instead of requiring
an app restart.

## Running JARVIS Core

Set `JARVIS_API_TOKEN` in the root `.env`, install `requirements.txt`, then run:

```bash
python -m app.main --server
```

The server defaults to `0.0.0.0:8000`. `JARVIS_SERVER_HOST` and
`JARVIS_SERVER_PORT` override the bind address. It exposes authenticated health,
session CRUD/history, `/chat`, `/chat/stream`, and write-only `/debug-reports`
endpoints. `GET /api/capabilities` reports the versioned chat, dashboard, and
action contract for rolling upgrades. Each chat request carries a server
session ID so New Chat genuinely starts clean context.

## Networking notes

`Info.plist` sets `NSAppTransportSecurity → NSAllowsArbitraryLoads`. This is
required: JARVIS Core is reached over plain `http://` at a Tailscale CGNAT
address (`100.64.0.0/10`), which ATS blocks by default. `NSAllowsLocalNetworking`
does **not** cover it — that key only exempts loopback, `.local` and link-local
addresses. The traffic is already encrypted by WireGuard inside the Tailscale
tunnel and this app is not App Store bound, so the blanket exception is the
pragmatic call. If the server ever gets a real certificate (Tailscale Serve /
MagicDNS), delete the whole `NSAppTransportSecurity` dict.

### Export compliance

`Info.plist` declares `ITSAppUsesNonExemptEncryption = false`, which suppresses
the export-compliance questionnaire on every TestFlight/App Store upload instead
of answering it by hand each time. The declaration is accurate for this app: it
makes no use of encryption beyond what the OS provides for standard networking
(HTTPS via `URLSession`, and the Tailscale tunnel, which is outside the app), and
it implements no cryptography of its own. That falls under the exemption for apps
using only standard/exempt encryption.

Two things to know. Comments do not survive in this file — Xcode rewrites the
source `Info.plist` when it merges the generated keys in, so this README is the
place for the rationale. And if the app ever adds its own cryptography, this
declaration stops being true: the value must become `true`, and Apple will then
issue a code to record alongside it as `ITSEncryptionExportComplianceCode`
(a string). Only add that key once Apple has actually issued a code — an invented
value will fail review.

### Networking

The phone must be **connected to your tailnet** for the app to reach the server —
the Tailscale app installed, logged into the same account, and its VPN toggle on.
There is no fallback to a LAN address.

Academic countdowns use local iOS notifications. JARVIS alerts (arr stack,
indexers, recovery results) arrive as **remote push** via APNs:

- The target has the Push Notifications capability
  (`JarvisCompanion.entitlements`, `aps-environment`; Xcode swaps in
  `production` when exporting a distribution build).
- `Push/PushRegistrar.swift` asks for notification permission once a server
  is configured, registers with APNs on every foreground, and sends the token
  to `POST /api/push/register` whenever the token or server identity changes.
  The environment (sandbox/production) is read from the embedded provisioning
  profile; JARVIS Core also self-corrects on `BadDeviceToken`.
- `PushAppDelegate` (via `@UIApplicationDelegateAdaptor`) receives the token
  and shows alerts as banners while the app is open.
- Settings shows the registration state ("Push alerts on").

Server setup (APNs key, `APNS_*` in the Optiplex `.env`) is in
`OVERNIGHT_HANDOFF.md`. With automatic signing, the first build after pulling
lets Xcode add Push Notifications to the App ID.

## Voice input

Tap the microphone in the Chat composer to dictate into the current draft.
Partial recognition appears live, and existing draft text is preserved. Tap
the microphone again to stop, review or edit the text, then use Send normally;
dictation never submits a turn on its own. The first use requests iOS Speech
Recognition and Microphone permissions. Audio capture ends when dictation
stops or Chat leaves the screen.

## Control room

The Home tab opens with two compact telemetry tiles, followed by focused
glass-panel cards. Today shows Now, media storage, a briefing shortcut, and
service health; Media shows storage, transfers, queues, and service links;
Home shows allowlisted toggles, service health, and links. It refreshes while visible and
foregrounded and supports pull-to-refresh. Paused/stopped torrents stay visible
so Resume remains available. Each transfer shows its total size next to the
percentage ("42% of 2.0 GB", as qBittorrent's Size column; hidden until a
magnet's metadata arrives), and Radarr/Sonarr queue rows show theirs next to
the status. Service failures retain stale data with an error.

Arr-stack alerts arrive grouped: every push from one incident shares an APNs
`thread-id` and `apns-collapse-id` (so an update replaces the previous
banner), and recoveries, summaries and the daily indexer digest use
`interruption-level: passive`. All three are handled by iOS itself; the app
has no code for them.

The Now card is backed by the typed `/api/now` snapshot and shows
active/alarming timers, open reminders, the next calendar event, and upcoming
calendar items whose titles contain quiz, test, exam, midterm, or final. Those
assessments show days remaining and schedule one daily local study brief during
the final 14 days through assessment day. Delivery defaults to 9 AM and can be
changed or disabled in Settings. Multiple assessments are combined into one
brief; unchanged notifications are retained, and calendar outages or older
Core responses cannot erase the schedule. The first assessment refresh asks
for iOS notification permission. Its last
successful payload is cached in `UserDefaults` under a connection-specific key; after a relaunch or failed
refresh the card says **Stale** and includes its update time instead of
presenting cached information as live.

Overlapping requests for the same section are suppressed. Transfers, services,
home entities, and Now refresh every five seconds; media refreshes every third
poll cycle and storage every sixth. The calendar feed itself is cached for five
minutes in Core and never prompts for OAuth consent from dashboard polling.
Returning to the foreground refreshes Now for notification reconciliation.
Timers tick between refreshes; stale snapshots retain their last known value.

The Media drive card uses `/api/status/storage` to show free, used, and total
space for one server-mounted video filesystem. Set `MEDIA_STORAGE_PATH` and an
optional `MEDIA_STORAGE_LABEL`; when the path is blank Core falls back to
`RADARR_ROOT_FOLDER`, then `SONARR_ROOT_FOLDER`. No storage API key is needed,
and an unavailable mount appears as an inline error rather than hiding the Hub.

## Reliable turns and history

New turns create a server session before sending tool-capable requests, then
carry a UUID saved with the local user message. Core stores that request ID
with the completed transcript and a durable receipt. Recovery matches the ID,
never the text; retrying a completed request returns its original answer.
An interrupted server receipt refuses automatic re-execution because a tool
may already have acted. Start a new request only after checking its outcome.

Stop ends phone reception; it does not undo or cancel server-side actions.
Core finishes the turn on one worker and saves the result for reconciliation.
Switching conversations cannot let old callbacks modify the new chat.
History refresh preserves unfinished local messages and stable server IDs.

Rename/delete edits are queued on the device, survive restarts, and retry on
reconnection. Failures appear in Chat instead of silently reverting edits.
Multiline paste remains an unsent draft; use the Send button when ready.
Starter prompts also populate the composer without submitting anything.

Streaming replies follow the bottom of the transcript until you scroll up;
the Latest button returns to the reply. Assistant text renders inline Markdown
and gives fenced code blocks horizontal scrolling and a Copy action. Dictation
stops when changing conversations or backgrounding, and late recognition
callbacks cannot overwrite another draft. Controls provide larger touch areas,
wider layouts have bounded reading widths, and motion honors Reduce Motion.

## Validation

The shared Xcode scheme includes `JarvisCompanionTests`, using in-memory token
storage and archives plus mocked network responses; unsigned simulator tests
do not require Keychain entitlements. Production tokens remain Keychain-backed. Run
Product → Test with a compatible installed iOS Simulator runtime. Server
regressions run with:

```bash
.venv/bin/python -m unittest tests.test_companion_reliability tests.test_server tests.test_status_hub tests.test_qbittorrent tests.test_session_summaries -v
```

When the installed SDK has no matching simulator runtime, type-check the app
and test sources against the device SDK; this does not replace visual or
on-device validation.

- Companion history sync uses the server’s `last_message_id` revision to skip unchanged transcripts while still updating titles. Revisions are cached in the local archive; older servers without revisions still fetch normally. Live turns are skipped before fetching, message matching is indexed, and date formatters are reused.

- Experimenting theme (5.45): a lab palette for trying visuals on real devices. `Views/Experiments.swift` holds each experiment (`Experiment` enum + its view) and its Settings switch; `JarvisBackdrop` and `JarvisCoreMark` swap in the experiment views only while the palette is `experimenting`. Current experiments: Flowing plasma background, Neural core (a firing 3D brain replacing the reactor), Orbit orb. The switches sync through Core (`/api/theme` `experiments`).
