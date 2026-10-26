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
