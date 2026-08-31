# JARVIS Product Vision, Repository Audit, and Roadmap

> Planning document only. Created August 30, 2026 after reviewing the current
> desktop runtime, tools, background monitors, companion API, iOS app, tests,
> and canonical project documentation. This document proposes no code changes.

## North star

JARVIS should become the single, dependable interface to Nick's digital life
and home: available by voice when nearby, by phone when away, aware enough to
notice trouble, fast enough to replace dedicated smart-home controls, and
capable of remembering useful context across months without becoming creepy or
unreliable.

The shortest version of the product goal is:

> One JARVIS runtime, reachable everywhere, controlling the home, watching the
> systems that matter, remembering the right things, and presenting the same
> truth on desktop, voice, and phone.

## Requested outcomes

The initial wish list can be organized into seven outcomes:

1. **Vision** — JARVIS can see the desktop now and eventually understand other
   explicitly authorized visual sources.
2. **Remote access** — use text and the companion app away from home.
3. **Whole-home control** — every useful smart device is exposed through Home
   Assistant and controllable through JARVIS from any client.
4. **Low latency** — lights, timers, and alarms feel as immediate as a Google
   Home command.
5. **Phone command center** — the app shows timers, alarms, devices,
   automations, health, media, events, and notifications rather than only chat.
6. **Proactive operations** — JARVIS notices outages and important home events,
   waits through sensible grace periods, and notifies Nick without spamming.
7. **Deeper intelligence** — optional local Llama-class inference plus durable,
   searchable knowledge that can reference older conversations, projects, and
   notes.

## Executive assessment

This is not a blank-slate project. The repository already has the correct
central architectural idea: all front ends route through one `JarvisRuntime`,
and integrations are tools or monitors behind that runtime. Several requested
features are already partially or substantially present.

| Area | Current state | What remains |
|---|---|---|
| Desktop vision | **Working baseline** | Better privacy controls, visual history, camera/device sources, and task-oriented vision |
| Remote text/app chat | **Working baseline** | Production-grade remote deployment, TLS/VPN guidance, background reliability, and richer API capabilities |
| Smart-home control | **Partial** | Correct configuration docs, onboard all devices into HA, support more services than on/off, safer entity resolution, areas/scenes/automations |
| HA/timer latency | **Partial** | Fast deterministic command path, event-driven HA updates, measurements, concurrency, and remote push |
| Phone dashboard | **Not started beyond chat** | Dashboard data API and native screens/widgets/live activities |
| Radarr/Sonarr requests | **Working baseline through chat** | Status/progress views, queue visibility, richer error handling, and app-native controls |
| Proactive home/system alerts | **Early partial** | Durable rules, outage grace windows, system/NAS/site monitors, push delivery, acknowledgement and deduplication |
| Local Llama | **Not implemented** | Define its role, select runtime/model after hardware inventory, add provider routing and quality/fallback policy |
| Long-term intelligence | **Basic facts only** | Searchable episodic memory, summaries, embeddings/retrieval, Obsidian ingestion, provenance, corrections, privacy controls |

## What exists today

### Runtime and brain

- `brain.runtime.JarvisRuntime` is the shared coordination layer for GUI, voice,
  text, and server modes.
- `JarvisAgent` uses Anthropic by default and falls back to a small keyword
  router if the API is unavailable.
- The agent supports a six-round tool loop and sends the complete custom tool
  list on every model turn.
- Conversation history is stored in SQLite by session. The active model context
  is capped at the most recent 20 turns.
- Replies can stream, but desktop GUI and voice mostly use the blocking path;
  streaming is currently most useful to the iOS client and optional text-mode
  TTS.
- Runtime tasks and events are useful UI telemetry, but they are in-memory and
  disappear when the process exits.

### Vision

- `look_at_screen` captures either the whole virtual desktop or primary display
  and sends a PNG to a configurable Claude vision model.
- Qt capture is the main path; PowerShell is the Windows/headless fallback.
- It is an explicit, on-demand screenshot tool. There is no continuous vision,
  camera ingestion, visual memory, OCR index, or phone-camera workflow.
- The screenshot is sent to Anthropic, so “fully local” currently describes the
  application process and storage—not all inference or visual data handling.

### Home Assistant

- JARVIS can test HA, list entities, and fuzzy-match natural-language on/off
  commands across lights, switches, and fans.
- A pooled HTTP session reduces connection overhead.
- The runtime polls selected HA domains every 20 seconds by default, builds a
  summarized snapshot, and records state-change events.
- The only proactive home rule is a once-per-day notice when at least four
  lights remain on after 11 PM.
- `unknown` and `unavailable` transitions are deliberately filtered out of the
  event feed, which conflicts with the goal of alerting when devices die.
- Device control is limited to `turn_on` and `turn_off`; brightness, color,
  temperature, covers, locks, climate, scenes, scripts, and automations do not
  have first-class handling.
- The matcher queries HA once per candidate domain per command and uses a small
  hand-written synonym list. It has no area/device registry cache and no
  explicit confirmation policy for sensitive devices such as locks or garage
  doors.

### Timers, alarms, reminders, and calendar

- Timers persist to JSON and support set, pause, resume, cancel, expiry,
  alarming, and dismiss states.
- Timers that expire while JARVIS is closed rehydrate as alarming on the next
  launch.
- Reminders/to-dos persist and timed reminders are announced when due.
- Google Calendar is read-only, with today's agenda, next-event lookup, and a
  lead-time monitor.
- There is no separate alarm-clock model, recurring alarm scheduling, snooze,
  phone Live Activity, or remote timer/alarm API.
- Due announcements depend on a running desktop/server runtime. There is no
  always-on service guarantee or phone-side backup scheduling.

### Remote server and iOS companion

- The FastAPI server provides bearer-authenticated health, session CRUD,
  blocking chat, and streaming chat.
- Session selection is protected by a runtime lock, preventing conversation
  context from bleeding between remote requests.
- The SwiftUI app provides chat, streaming replies, Stop, retry, conversation
  history/search/rename/delete, local drafts, connection state, and server
  history synchronization.
- The API token is stored in the iOS Keychain. The server address is in
  `UserDefaults`.
- The iOS README is designed around LAN or Tailscale access, which is a sensible
  personal-assistant deployment shape.
- The app currently has one main product surface: chat. It has no Home,
  Timers, Automations, Media, Events, or System Health dashboard.
- The server offers no dashboard snapshot endpoint, live event stream, device
  command endpoint, timer/reminder CRUD endpoints, media status endpoints, or
  push-notification registration.

### Media library

- Radarr movie lookup/add and Sonarr series lookup/add are implemented.
- Adding is intentionally split into lookup and confirm-gated mutation.
- Quality profiles and root folders are resolved against live service data.
- Chat from the iOS app reaches the same tools, so asking JARVIS remotely to add
  media should already work when the server can reach Radarr/Sonarr.
- There is no native media request form, download queue/progress, recently
  added view, failed-download alert, or Plex availability check.

### Current memory

- Short-term transcripts live in SQLite and are session-scoped.
- Long-term facts live in `memory/profile.json`; the model can add and remove
  them through tools.
- Up to 60 recent facts are injected directly into every system prompt by
  default.
- There is no semantic retrieval over old sessions, automated session
  summaries, relationship graph, confidence, source/provenance, fact merging,
  stale-fact detection, or Obsidian vault connector.
- The present approach is suitable for dozens of preferences, not for becoming
  “super smart and references old stuff” at personal-knowledge-base scale.

## Important audit findings

These should be resolved before broad feature expansion because they affect the
reliability of the existing foundation.

### 1. Home Assistant configuration drift was resolved

The audit found that `JARVIS.md` documented `HA_URL` and `HA_TOKEN`, while the
integration read `HOME_ASSISTANT_URL` and `HOME_ASSISTANT_TOKEN`. The canonical
long names are now documented and read live; the short names remain supported
as migration aliases, with focused tests for precedence and compatibility.

### 2. “Hot-reloaded settings” is only partly true

The repository guidance says settings are read live inside functions, but
several modules cache configuration at import time, including the Anthropic
client/model, Home Assistant credentials, Spotify credentials/device, vision
model, much of speech capture, and voice-loop thresholds. The settings tool
itself warns that some changes require restart. Documentation should state the
actual behavior, or the relevant modules should eventually be made uniformly
live.

### 3. Device-offline states are suppressed

The Home monitor counts unavailable entities in its snapshot, but state-change
events involving `unknown` or `unavailable` are rejected as noise. This makes
the current monitor unsuitable for the requested dead-device alerts. Availability
needs its own delayed state machine rather than ordinary change-event filtering.

### 4. Remote access is authenticated, not fully hardened

Bearer auth and Keychain storage are solid first steps. Direct HTTP on a LAN or
tailnet still needs an explicit threat model. The project should prefer a VPN
such as Tailscale or HTTPS, avoid public port forwarding, rate-limit auth
failures, redact secrets from logs, and define token rotation/revocation.

### 5. The runtime is a process, not yet a dependable home service

Proactive awareness only works while the relevant JARVIS process is running.
There is no documented Windows service/task configuration, watchdog, automatic
restart, health supervision, or durable event queue. “JARVIS runs everything”
requires the core to behave like infrastructure, not only a desktop app.

### 6. Tests cover only a narrow edge

The current test suite focuses on server authentication/session behavior. The
home matcher, timers, reminders, monitor state machines, persistence recovery,
memory, Radarr/Sonarr safety gates, and iOS view model behavior lack visible
automated coverage in this repository.

## Target product architecture

The existing layered design should remain. The main evolution is to make the
runtime an always-on core with typed state and events, while every client becomes
a view and command surface over it.

```text
Voice / Desktop GUI / iPhone / future widgets
                    |
        authenticated command + state API
                    |
              JarvisRuntime core
       +------------+-------------+
       |            |             |
 deterministic   LLM planner   event/rule engine
 fast commands   + memory      + notification policy
       |            |             |
       +------ capability adapters-+
       | HA | timers | media | calendar | system health |
                    |
          durable state + event journal
```

Three rules matter:

1. **Fast paths for known commands.** “Turn off bedroom lights” and “set a
   10-minute timer” should not require a full generative round trip when a
   deterministic intent can be resolved safely.
2. **The LLM remains the flexible planner.** Ambiguous language, conversation,
   multi-step requests, summaries, and exceptions still go through the model.
3. **Clients never own the truth.** The core owns devices, timers, alerts,
   sessions, and rules; phone and desktop cache enough state for resilience and
   render it consistently.

## Capability plans

### A. JARVIS Vision

#### Near-term definition

- Keep screen capture explicit and user-initiated.
- Add selectable source: active window, display, region, or all monitors.
- Return structured observations alongside the spoken summary so the UI can
  show what was inspected.
- Add privacy exclusions for password managers, banking apps, private browser
  windows, and named monitors/windows.
- Never persist screenshots by default. If visual history is enabled, make its
  retention, encryption, and delete-all controls obvious.

#### Later possibilities

- “Use phone camera” as a one-shot attachment sent through the companion.
- Home-camera snapshots only from explicitly selected cameras and only for
  named rules such as package detection or checking a garage door.
- OCR/document understanding routed into the knowledge system with a source
  link and user confirmation before durable storage.

Continuous ambient recording should not be the default. It creates a much
larger privacy and security problem than on-demand vision.

### B. Remote communication

The chat path already exists. Productionizing it means:

- Run JARVIS Core automatically at boot under supervision.
- Use Tailscale/private networking or HTTPS; do not expose port 8000 directly
  to the public internet.
- Add explicit server capability/version reporting so clients know which
  screens and commands are supported.
- Make events and state available through REST plus Server-Sent Events or a
  WebSocket for live updates.
- Persist an outbox/inbox so an alert produced while the phone is unreachable
  is delivered later.
- Decide whether remote voice notes, image attachments, and Siri/App Intents
  belong in the first or a later app milestone.

### C. Whole-home control

Home Assistant should remain the device abstraction. JARVIS should not grow
one-off vendor integrations unless a device truly cannot be represented in HA.

Suggested onboarding sequence:

1. Bring the Govee lights and every target device into HA.
2. Create clean HA entity names, areas, labels, groups, scenes, and scripts.
3. Build a cached entity/area/service catalog in JARVIS.
4. Expand commands to brightness, color, temperature, media, climate, covers,
   vacuums, scenes, scripts, and automations.
5. Introduce risk classes: lights are immediate; locks, doors, security, and
   destructive automations require stronger confirmation and audit logging.
6. Add a diagnostic screen listing unsupported, unavailable, duplicate, or
   ambiguously named entities.

Home Assistant's event/state interfaces should eventually replace broad
20-second polling for live state. Polling remains useful as a reconciliation
fallback.

### D. Google Home-level latency

Latency must first be measured by stage:

```text
input -> intent -> model/tool selection -> integration request -> device ack
```

For voice, also measure wake detection, end-of-speech detection, STT, first
model token, first TTS audio, and total response time.

Likely high-value improvements, in order:

1. Add a deterministic local fast path for high-confidence device, timer,
   alarm, playback, and scene commands.
2. Cache the HA entity registry and reuse a single state snapshot instead of
   fetching each domain independently during matching.
3. Acknowledge immediately after HA accepts the service call; update final
   state asynchronously.
4. Use event-driven HA state updates for the dashboard.
5. Stream model output and TTS in GUI/voice for commands that still require the
   LLM.
6. Run a small local classifier/router if rules become too brittle.
7. Set performance budgets and track p50/p95 rather than optimizing by feel.

Proposed initial performance targets:

- Typed deterministic HA command: acknowledged in under 500 ms on LAN.
- Voice deterministic HA command: action begins in roughly 1–2 seconds after
  end of speech, hardware permitting.
- Timer creation: local state committed immediately; spoken confirmation may
  follow.
- Dashboard state: normally within 1 second of an HA event.

### E. Phone command center

The app can grow from chat into five top-level areas:

1. **Home** — favorites, rooms, device availability, scenes, and quick actions.
2. **Now** — alarms, timers, reminders, next calendar event, weather, active
   media/downloads, and current alerts.
3. **Chat** — the existing conversation experience.
4. **Activity** — a chronological, filterable event/notification history with
   acknowledgements.
5. **System** — JARVIS Core, PC, NAS, HA, Radarr, Sonarr, Plex, network, and
   watched-site health.

Useful native iOS additions after the API exists:

- Timer/alarm Live Activities and lock-screen controls.
- Home-screen widgets for favorites and system health.
- Actionable notifications: acknowledge, snooze, run scene, or open details.
- App Intents/Shortcuts for common commands.
- Local cache showing the last-known state clearly when Core is offline.

The first dashboard should be read-heavy and conservative about mutation. Chat
already provides flexible control while dedicated control screens mature.

### F. Plex, Radarr, and Sonarr

The requested chat workflow is substantially present:

```text
phone text -> Core chat -> lookup -> explicit selection -> add -> search
```

The next useful layer is operational visibility:

- Requested item status and download progress.
- Radarr/Sonarr queue failures and stalled items.
- Plex library scan/availability confirmation.
- “Recently added” and “ready to watch.”
- Clear separation between request, download, import, and Plex availability.
- Notification only on meaningful transitions or failures.

### G. Proactive operations and notifications

This should be a general monitoring platform, not a collection of bespoke
threads.

Each monitor should emit normalized observations. A durable rule engine turns
them into incidents with these states:

```text
healthy -> suspected -> confirmed -> notified -> acknowledged -> recovered
```

Every rule needs:

- target and check method;
- interval and timeout;
- failure grace period (for example, 20 minutes);
- recovery threshold;
- severity and quiet-hours policy;
- notification channels;
- deduplication/cooldown;
- acknowledgement and snooze;
- escalation behavior;
- durable history.

Initial monitor set:

- JARVIS Core self-health and process uptime.
- Home Assistant availability.
- NAS host reachability plus a service-level check, not merely ping.
- Plex, Radarr, Sonarr, and their download client.
- Important HA entities becoming unavailable.
- Internet/DNS health.
- A configurable HTTP/DNS check for the “1337x dies” requirement. The exact
  target and meaning of “down” need definition: public website, local proxy,
  indexer integration, DNS resolution, or an application/API dependency.

Alert delivery options:

- **Best native experience:** APNs through a small trusted push provider or a
  self-hosted push bridge.
- **Simpler first version:** an existing notification service (for example an
  HA notification target) while the native push pipeline is built.
- **Fallback:** in-app event inbox with live SSE/WebSocket updates.

The system must send recovery notices as carefully as failure notices. “NAS is
back online after 24 minutes” closes the loop and builds trust.

### H. Local Llama on the main PC

Local inference is worth exploring, but “use Llama” is an implementation choice,
not yet a product requirement. First decide which jobs it should own:

- fast intent classification;
- memory extraction and session summaries;
- private/offline conversation;
- simple tool planning;
- full replacement for the cloud model;
- vision.

A hybrid route is the lowest-risk starting point:

- deterministic code handles obvious commands;
- a small local model handles routing, summarization, and offline/basic turns;
- the cloud model handles difficult planning and high-quality conversation;
- policy decides based on privacy, complexity, latency, availability, and cost.

Before choosing model size or serving software, record the main PC's GPU model,
VRAM, RAM, CPU, operating system, acceptable power draw, and desired context
length. Evaluate candidates using JARVIS-specific tasks rather than generic
benchmarks. Tool-call reliability and latency matter more here than leaderboard
scores.

The provider boundary should live below `JarvisAgent`, so front ends and tools
do not care whether a turn is handled by Anthropic, a local OpenAI-compatible
server, or the fallback router.

### I. “Super smart” long-term memory and Obsidian

Obsidian can be a valuable knowledge source, but it should not be the entire
brain. Treat it as one user-owned corpus with Markdown-native provenance.

A scalable memory system needs distinct stores:

1. **Working memory** — the current conversation and active task.
2. **Episodic memory** — session summaries and notable past events.
3. **Semantic memory** — stable facts, preferences, people, places, and
   relationships.
4. **Knowledge sources** — Obsidian notes, selected files, calendars, and
   future email/documents.
5. **Operational state** — timers, incidents, device state, media requests;
   this should stay structured and not be treated as prose memory.

Recommended retrieval flow:

```text
new request
  -> classify what context could help
  -> search summaries/facts/authorized notes
  -> rerank a small set
  -> inject only relevant excerpts with source and date
  -> answer
```

Guardrails:

- Store provenance and timestamps for every extracted memory.
- Let Nick inspect, edit, pin, and delete memories.
- Distinguish direct statements from model inferences.
- Avoid silently writing speculative facts.
- Detect contradictions and ask which fact is current.
- Encrypt or exclude sensitive categories.
- Add retention rules and a full export/delete path.
- Keep raw history out of every prompt; retrieve only relevant context.

An Obsidian first milestone can be read-only indexing of explicitly selected
folders, respecting frontmatter and links. Write-back and autonomous note edits
should wait until retrieval quality and backups are proven.

## Recommended delivery phases

### Phase 0 — Make the foundation trustworthy

- Reconcile HA environment variable names and documentation.
- Inventory real hardware/services and bring target devices into HA.
- Document and implement an always-on deployment model for Core.
- Add structured logging, health checks, latency instrumentation, and durable
  event storage.
- Expand automated tests around state and safety-critical paths.
- Establish secure remote access through a private network or HTTPS.

**Exit condition:** JARVIS Core survives restarts, is reachable remotely, and
can reliably control a known set of HA devices with measured behavior.

### Phase 1 — Fast home control and useful phone status

- Add deterministic fast commands and cached HA metadata.
- Add typed state/snapshot APIs for timers, reminders, home, calendar, media,
  and system health.
- Add live server events.
- Build the iOS “Now” dashboard as read-only first.
- Expose native timer/reminder controls after the read path stabilizes.

**Exit condition:** the phone shows a trustworthy current snapshot and common
lights/timers feel near-instant.

### Phase 2 — Proactive, durable notifications

- Create the common monitor/rule/incident model.
- Add Core, HA, NAS, Plex stack, important-device, internet, and watched-endpoint
  monitors.
- Deliver push notifications with grace periods, acknowledgement, recovery,
  cooldown, and quiet hours.
- Add Activity and System screens to iOS.

**Exit condition:** a simulated outage creates one timely alert, persists across
restart, and produces one recovery notice without spam.

### Phase 3 — Home Hub phone experience

- Home favorites/rooms/scenes and guarded device controls.
- Timer/alarm Live Activities and widgets.
- Media queue/progress and ready-to-watch notifications.
- Automation visibility and safe execution.
- App Intents/Shortcuts for a handful of common actions.

**Exit condition:** the companion replaces most routine Google Home Hub checks
and controls for Nick's actual household.

### Phase 4 — Deeper intelligence

- Session summaries and searchable episodic memory.
- Semantic retrieval with provenance and user-facing memory controls.
- Read-only Obsidian indexing for selected folders.
- Local-model provider experiment and JARVIS-specific evaluation harness.
- Hybrid routing only after quality and tool safety meet defined thresholds.

**Exit condition:** JARVIS can accurately reference a relevant older fact or
note, show where it came from, and function usefully during a cloud outage.

### Phase 5 — Broader vision and automation

- Phone-camera and selected HA-camera one-shot vision.
- Task-oriented visual workflows with explicit consent.
- Multi-step automation planning with preview, confirmation, execution log,
  and rollback where possible.

## Suggested first backlog

This is an order of dependency, not a commitment to a specific sprint size.

1. Verify the resolved HA environment-name compatibility against the live
   Windows deployment and add a live connection smoke test.
2. Record a complete device/service inventory and name entities cleanly in HA.
3. Define Core's always-on Windows deployment and remote-network threat model.
4. Add latency timestamps around text, voice, model, tool, HA, and TTS stages.
5. Define typed API contracts for a unified dashboard snapshot and event feed.
6. Design a durable event/incident schema before adding more monitor threads.
7. Add Core/HA/NAS/Plex-stack health checks with configurable grace periods.
8. Select a first notification channel and test failure/recovery delivery.
9. Build the iOS Now screen from read-only state.
10. Add deterministic fast paths for a small set of lights, scenes, and timers.
11. Add first-class timer/reminder mutations to the API and iOS app.
12. Add media queue and Plex-ready status.
13. Build session summarization and retrieval evaluation fixtures.
14. Inventory PC hardware and benchmark a small local model on real JARVIS
    intents, memory extraction, and tool calls.

## Decisions to make before implementation

- Which machine is the always-on JARVIS Core, and can it remain awake 24/7?
- Is remote access Tailscale-only, LAN-only, or internet-facing HTTPS?
- Which exact Govee devices and other smart-home platforms must join HA?
- Which actions may happen instantly, and which require confirmation?
- What does “1337x dies” mean technically, and what endpoint proves health?
- What counts as NAS failure: host down, storage unavailable, critical SMART
  condition, a particular share offline, or all of these?
- Which notification route should be first: HA mobile notifications, native
  APNs, or another personal push service?
- Are quiet hours desired, and which alerts may override them?
- Should a timer continue and notify if the main PC is shut down? If yes, which
  always-on component owns it?
- Which Obsidian vault folders may JARVIS read? May it ever write?
- What hardware is in the main PC, especially GPU and VRAM?
- Which data must never leave the machine, even for cloud reasoning?

## Definition of “JARVIS runs everything”

The phrase should eventually mean all of the following, not merely that many
tools exist:

- one authoritative runtime and state model;
- automatic startup, supervision, backup, and recovery;
- secure access from home and away;
- measurable low latency for routine commands;
- graceful behavior when cloud APIs or individual services fail;
- durable alerts with acknowledgement and recovery;
- clear safety boundaries for consequential actions;
- consistent desktop, voice, and phone experiences;
- explainable memory with provenance and user control;
- enough automated testing and observability to trust it unattended.

That is the path from a capable personal assistant application to dependable
personal infrastructure.
