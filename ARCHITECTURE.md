# Pearson Stark JARVIS Architecture

This project is moving toward an ambient assistant: one runtime, many senses,
many tools, one coherent personality.

## Runtime Spine

`brain.runtime.JarvisRuntime` is the app-level coordination layer. GUI, text
mode, and voice mode should talk to the runtime first, not directly to low-level
modules.

Current responsibilities:

- Own the active `JarvisAgent`.
- Route conversation through the best available brain.
- Start and switch sessions.
- Provide status snapshots for the UI.
- Expose timers and due reminders for proactive announcement.
- Maintain a runtime event inbox for observations and proactive notices.
- Run the first background monitor for Home Assistant state changes.
- Keep structured home and weather snapshots for cockpit panels.
- Track whether the last turn used tools.
- Compose situational briefings (time, weather, running timers) via
  `compose_briefing` / `launch_briefing`.
- Stream replies fragment-by-fragment (`respond_streaming`) for streaming TTS.

Near-term responsibilities:

- Run proactive monitors for calendar, Home Assistant, weather, files, and
  system state.
- Add more monitor workers that feed the event inbox: calendar, files, and
  system health.
- Store task state for multi-step work.
- Decide when to interrupt, notify quietly, or stay silent.

## Target Layers

1. Perception
   - Voice, chat, Home Assistant state, timers, calendar, weather, computer
     state, files, and future sensors.

2. Memory
   - Durable facts, preferences, people, projects, routines, and active goals.
   - Short-term conversation context stays separate from long-term memory.

3. Reasoning
   - Conversational response, tool planning, verification, and follow-up.
   - The agent should report outcomes, not narrate internals.

4. Agency
   - Tools that can change the world: home, music, reminders, files, browser,
     research, coding, and automations.

5. Presence
   - GUI cockpit, orb state, voice pacing, interruption, proactive notices, and
     a stable personality.

## Design Rule

New features should plug into the runtime as capabilities or monitors. Avoid
making UI widgets or speech loops own business logic directly. The more the
runtime knows, the more JARVIS feels like one mind instead of several scripts.

## Runtime Monitor Settings

- `HOME_MONITOR_ENABLED`: true/false, defaults to true.
- `HOME_MONITOR_INTERVAL_S`: polling interval in seconds, defaults to 20.
- `HOME_MONITOR_DOMAINS`: comma-separated domains, defaults to
  `light,switch,fan,media_player`.
- `WEATHER_MONITOR_ENABLED`: true/false, defaults to true.
- `WEATHER_MONITOR_INTERVAL_S`: weather refresh interval in seconds, defaults
  to 1800.
