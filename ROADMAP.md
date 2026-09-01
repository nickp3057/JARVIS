# JARVIS Roadmap

> Recommended implementation order as of August 30, 2026. This turns
> `PRODUCT_VISION_AND_ROADMAP.md` into an actionable delivery plan. It does not
> represent completed work and does not change application behavior.

## Recommendation

Do not begin with local Llama, broad autonomous memory, or dozens of new tools.
The next version of JARVIS should first become a dependable always-on core with
fast Home Assistant control and a useful phone status screen.

The recommended sequence is:

1. **Trustworthy core** — correct configuration, tests, health, persistence,
   secure remote access, and automatic startup.
2. **Fast home control** — deterministic commands and live HA state.
3. **Phone command center** — timers, devices, events, health, and media status.
4. **Proactive operations** — durable outage detection and phone alerts.
5. **Better memory** — searchable past conversations and selected Obsidian
   notes with provenance.
6. **Hybrid local intelligence** — benchmark a local model and give it jobs it
   can perform reliably.

This order creates visible improvements early while building the foundation
needed for “JARVIS runs everything.”

## Milestone 1 — Stabilize JARVIS Core

**Goal:** JARVIS starts reliably, reports its health accurately, and has a
tested foundation before more features depend on it.

**Feedback baseline shipped:** the iOS companion can submit explicit,
authenticated debug reports containing a bounded conversation, focused reply,
user description, and non-secret app context. Core stores them durably and
exposes read-only review tools to JARVIS.

### Work

- Verify the resolved Home Assistant environment compatibility on the live
  Windows deployment. Canonical `HOME_ASSISTANT_URL` /
  `HOME_ASSISTANT_TOKEN` names and legacy `HA_URL` / `HA_TOKEN` aliases are now
  supported and tested.
- Audit all `.env` settings and document which are live-reloaded versus which
  require restart.
- Add automated tests for:
  - Home Assistant command matching and failures;
  - timer persistence, expiry, pause, resume, and dismissal;
  - reminder persistence and due delivery;
  - runtime monitor transitions and deduplication;
  - Radarr/Sonarr confirmation gates;
  - memory persistence and history/session isolation.
- Add structured health information for Core, HA, Anthropic, timers,
  Radarr/Sonarr, Plex, and notification delivery.
- Persist runtime events/incidents instead of losing them at process exit.
- Define and document automatic startup, restart, and watchdog behavior on the
  main Windows PC.
- Keep remote access behind Tailscale/private networking or HTTPS; do not
  expose the current HTTP server directly to the public internet.

### Done when

- Core automatically recovers after a reboot or crash.
- Health information distinguishes configured, online, degraded, and offline.
- State and important events survive restart.
- Existing features have regression coverage sufficient to change the runtime
  safely.

## Milestone 2 — Google Home-level routine commands

**Goal:** lights, scenes, timers, and common playback commands begin acting
almost immediately.

### Work

- Instrument the complete request path:
  - input received;
  - speech finished;
  - transcription finished;
  - intent selected;
  - model request/first token;
  - tool started/finished;
  - HA request/acknowledgement;
  - TTS first audio/finished.
- Add a deterministic local path for high-confidence commands:
  - turn a known device/group on or off;
  - activate a known scene/script;
  - set/cancel/pause/resume a timer;
  - pause/resume/skip music.
- Keep ambiguous and conversational commands on the LLM path.
- Cache HA entities, friendly names, areas, and supported services.
- Replace broad polling with HA event subscriptions for current state, retaining
  periodic reconciliation as a fallback.
- Expand HA control beyond on/off:
  - brightness and color;
  - scenes, scripts, and automations;
  - climate, covers, and media players;
  - guarded support for locks, doors, and security devices.
- Stream responses/TTS in desktop GUI and voice mode where it improves perceived
  speed.

### Initial performance targets

- Typed deterministic command acknowledged within 500 ms on the LAN.
- Device action begins roughly 1–2 seconds after voice input ends.
- Timer state is committed immediately, before spoken confirmation completes.
- HA dashboard state updates within about one second of an event.

### Done when

- A measured set of common commands meets the targets at p50 and has an
  acceptable p95.
- Ambiguous entity names produce a safe clarification rather than controlling
  the wrong device.
- Sensitive actions follow an explicit confirmation policy.

## Milestone 3 — iPhone “Now” dashboard

**Goal:** the companion becomes useful before opening chat.

### Core API work

- Add a versioned capabilities endpoint.
- Add typed read APIs for:
  - active/alarming timers;
  - reminders and next calendar event;
  - favorite HA devices and summarized rooms;
  - current alerts and recent events;
  - Core and service health;
  - Radarr/Sonarr queue and Plex availability;
  - weather and current Spotify playback.
- Add a live event stream using Server-Sent Events or WebSocket.
- Add guarded mutation endpoints only after the corresponding read models are
  stable.

### iOS work

- Add a **Now** screen showing:
  - timers and alarms;
  - reminders and next calendar event;
  - important alerts;
  - favorite home devices/scenes;
  - current media/download status;
  - Core and home health.
- Keep the existing chat experience as a first-class tab.
- Clearly label stale cached data when Core is unreachable.
- Add timer/reminder controls after the read-only dashboard is trustworthy.
- Add Live Activities and widgets in a later pass.

### Done when

- The phone loads a coherent last-known snapshot quickly.
- Live changes arrive without manual refresh.
- Offline/stale state is never presented as current.
- Common timer and home actions can be completed without opening chat.

## Milestone 4 — Proactive home and system operations

**Goal:** JARVIS notices meaningful failures, waits through transient problems,
alerts once, and reports recovery.

### Foundation

- Create a common monitor, rule, incident, and notification model.
- Use durable incident states:

```text
healthy -> suspected -> confirmed -> notified -> acknowledged -> recovered
```

- Give every rule an interval, timeout, grace period, recovery threshold,
  severity, quiet-hours policy, cooldown, and notification channel.
- Store incident history and acknowledgements.

### First monitors

- JARVIS Core process and API.
- Home Assistant.
- Main PC basics.
- NAS host plus a real service/share/storage check.
- Plex, Radarr, Sonarr, and download client.
- Important HA devices becoming unavailable.
- Internet and DNS health.
- The exact 1337x/indexer dependency Nick relies on, once its health definition
  is clarified.

### Notifications

- Start with the quickest dependable push route, likely an existing Home
  Assistant mobile notification target.
- Add native APNs later if deeper actions and a fully native experience justify
  the infrastructure.
- Include acknowledgement, snooze, and recovery notices.
- Queue delivery while the phone is unreachable.

### Done when

- A controlled NAS/service outage produces one alert only after its configured
  grace period.
- Restarting JARVIS does not forget the incident.
- Recovery produces one closing notification.
- Noisy/flapping devices are suppressed without hiding sustained outages.

## Milestone 5 — Home Hub experience

**Goal:** the iPhone app replaces most routine Google Home Hub interactions.

### Work

- Add room and favorites views.
- Add device controls appropriate to each HA domain.
- Show and run HA scenes, scripts, and automations.
- Add an activity timeline with filters and incident details.
- Add a system-health screen for Core, PC, NAS, home services, and media stack.
- Show Radarr/Sonarr request, queue, import, and Plex-ready status separately.
- Add actionable notifications, Live Activities, widgets, and App Intents for
  selected common actions.

### Done when

- Nick can see and control the household's most-used devices from one app.
- Timers, alarms, automations, alerts, and media status have dedicated views.
- Chat remains available for flexible requests without being the only UI.

## Milestone 6 — Searchable personal memory

**Goal:** JARVIS accurately recalls relevant older information and shows where
it came from.

**Baseline shipped:** authorized Obsidian folder search, local lexical
retrieval, bounded per-turn excerpts with note-path provenance, query-aware
profile fact selection, and a managed Markdown mirror of structured facts.
Semantic embeddings, session summaries, contradiction handling, and memory
management UI remain below.

### Work

- Generate compact session summaries and notable-event records.
- Separate working, episodic, semantic, knowledge-source, and operational
  memory.
- Add semantic retrieval over summaries and durable facts.
- Store source, date, confidence, and whether a memory was directly stated or
  inferred.
- Add user controls to inspect, edit, pin, forget, export, and delete memories.
- Detect contradictions and stale facts.
- Index explicitly selected Obsidian folders read-only, preserving note paths,
  headings, links, and frontmatter.
- Retrieve only relevant excerpts instead of loading entire history or vaults
  into prompts.
- Create evaluation cases for real questions Nick expects JARVIS to remember.

### Done when

- JARVIS answers a test set of old-context questions accurately.
- Every recalled item can identify its source.
- Incorrect memories can be corrected cleanly.
- Private/excluded Obsidian content is never indexed.

## Milestone 7 — Local-model experiment

**Goal:** determine whether local inference materially improves privacy,
availability, latency, or cost without reducing tool safety.

### Preparation

- Inventory GPU, VRAM, RAM, CPU, operating system, power constraints, and
  desired context length.
- Define candidate jobs before selecting a model:
  - intent routing;
  - memory extraction;
  - session summarization;
  - offline/basic conversation;
  - tool planning;
  - vision.
- Put model providers behind a common boundary beneath `JarvisAgent`.

### Evaluation

- Benchmark multiple model sizes on real JARVIS requests.
- Measure latency, memory use, tool-call correctness, refusal/confirmation
  behavior, response style, and hallucination rate.
- Begin with local classification and summarization.
- Keep difficult planning on Anthropic until a local candidate passes the same
  safety and correctness tests.

### Done when

- Routing policy clearly defines deterministic, local, cloud, and fallback
  paths.
- A cloud outage still leaves JARVIS useful for core local tasks.
- Local inference is adopted only for jobs where it meets measured quality and
  latency targets.

## Immediate next work recommendation

The next implementation cycle should contain only these items:

1. Diagnose and fix the currently reported JARVIS bug.
2. Verify Home Assistant configuration compatibility and the turn-off fix
   against the live deployment.
3. Add timing instrumentation for one typed HA command, one voice HA command,
   and one timer command.
4. Add tests around those paths before optimizing them.
5. Inventory the real HA devices, NAS, media services, always-on host, remote
   networking, and main-PC hardware.
6. Write the first versioned dashboard-state API contract without building the
   entire iOS dashboard yet.

This is deliberately small. It gives the next cycle a clear finish line and
produces the measurements and inventory needed to make the following work
correctly.

## Inputs needed from Nick

- Bug symptoms, exact wording/errors, when it began, and reproduction steps.
- Main PC CPU, RAM, GPU, and VRAM.
- Which machine should run JARVIS Core continuously.
- Whether Tailscale is already installed on the Core machine and phone.
- A list of HA devices and their desired room/friendly names.
- NAS type and the services/shares that prove it is healthy.
- What “1337x is down” specifically means in the current setup.
- Preferred first notification route and desired quiet hours.
- Obsidian vault location and folders JARVIS may eventually read.

## Roadmap maintenance

Update this file when priorities or milestones change. Move shipped work into
`JARVIS.md`'s “Recently Shipped” section and keep `ARCHITECTURE.md` synchronized
whenever runtime responsibilities, monitor settings, or API ownership change.
