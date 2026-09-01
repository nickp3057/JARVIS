# Jarvis Companion (iOS)

A native SwiftUI chat client for JARVIS Core. It sends text to the server's
`/chat` endpoint, shows what comes back, and keeps device-local conversation
history.

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

## Architecture

This is a **thin client** — deliberately so. JARVIS Core owns the personality,
conversation context and history, the memory tier, and all 45+ tools (Home Assistant,
Spotify, Radarr/Sonarr, timers, and the rest). The app knows none of that. It
sends one string and renders one string back; if a turn involved six server-side
tool calls, the app is none the wiser, which is exactly why the request timeout
is a generous 30s. The layering is: `JarvisAPIClient` (the only type that knows
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
is uploaded. The authenticated report contains at most 100 current-conversation
messages plus app version, device type, OS version, connection state, and the
selected response ID. It never contains the API token or server address. JARVIS
Core stores reports separately from personality memory for later inspection.

## Local chat history

The **history** button opens synchronized conversations. Search matches titles
and message text. Swipe or long-press a row to rename or delete it. **New chat**
starts a blank, isolated server session without deleting older conversations.
Long-press a message to copy or share it.

When the app remains inactive or closed for at least five minutes, returning to
it starts a clean conversation automatically. The previous conversation stays
in History. Short app switches keep the current chat open. Drafts are saved per
conversation. Replies stream into the JARVIS bubble as they arrive; the send
button becomes Stop while generation is active.

For rolling upgrades, the app retries without a cached session when the server
returns 404, then falls back from `/chat/stream` to the legacy `/chat` endpoint
when streaming is not deployed yet. An HTTP route error does not mark the
otherwise reachable server offline.

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
endpoints. Each chat request carries a server session ID so New Chat genuinely
starts clean context.

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

Not implemented, by design: push notifications, device dashboards, and voice
input.
