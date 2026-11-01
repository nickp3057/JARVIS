# Pearson Stark JARVIS

A personal AI assistant modelled on Tony Stark's JARVIS (with a bit of TARS from *Interstellar*). It listens for "Hey JARVIS", answers in a dry British voice, and controls the house: lights, scenes, the TV, music, the media server, alarms, reminders and more.

JARVIS runs as one Python runtime ("JARVIS Core") with several front ends: a desktop window with an animated orb, a headless always-listening voice loop, an authenticated HTTP API for the iOS/iPad companion app, and a custom home screen for the living-room Google TV.

## Features

- **Voice**: wake word (openWakeWord), speech-to-text (faster-whisper), ElevenLabs text-to-speech with a Piper fallback, voiceprint speaker ID, and follow-up listening without repeating the wake word.
- **Claude-powered brain**: Anthropic Claude (newest Haiku by default) with a tool loop of 45+ tools, prompt caching, streaming replies and a keyword fallback when offline.
- **Smart home**: Home Assistant control, scenes, live state questions, and staged automation edits that show a diff and need approval before they apply.
- **Media stack**: request movies and shows through Radarr and Sonarr, follow downloads in qBittorrent, and a "media doctor" that traces a request through each stage to Plex and names where it stalled.
- **Self-healing**: health monitors for the NAS and the *arr stack, incident reports, and an allowlist of fixes that run only after you approve them.
- **Memory**: SQLite conversation history, long-term facts with revision history, standing orders, habit learning, and an Obsidian vault for notes and a journal.
- **Daily life**: timers, reminders, wake alarms, maintenance chores, morning briefings, weather, Google Calendar, Spotify, price watching and a study countdown.
- **Read-only finance**: Charles Schwab holdings and live prices.

## Repository layout

| Path | What's in it |
|---|---|
| `app/` | Entry point (`main.py`), the companion HTTP API (`server.py`), scoped client tokens |
| `brain/` | Agent, tool schemas and dispatch, runtime, memory, timers, reminders, confirmation gates, self-repair |
| `integrations/` | Home Assistant, Radarr/Sonarr, qBittorrent, Plex, Spotify, Google Calendar, Schwab, NAS health |
| `speech/` | Mic capture, wake word, STT, TTS, headless voice loop |
| `ui/` | PyQt6 desktop window: orb, chat bubbles, status panels |
| `ios/JarvisCompanion/` | SwiftUI companion app for iPhone, iPad and Mac Catalyst ([its own README](ios/JarvisCompanion/README.md)) |
| `tv/` | Android launcher that turns the Google TV home screen into a JARVIS dashboard |
| `ops/` | Host setup for the Optiplex server and the NAS recovery scripts ([Optiplex](ops/optiplex/README.md), [NAS](ops/nas/README.md)) |
| `tests/` | `unittest` suite |
| `memory/` | Runtime data (history, profile, habits, vault). Created on first run |

## Getting started

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in your keys
```

At minimum `.env` needs `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`. Every integration (Home Assistant, Radarr, Sonarr, qBittorrent, Spotify, Plex, ...) is optional and turns on once its URL and key are set. `.env.example` lists every setting.

Optional: for the full interactive browser tool, run `python -m playwright install --with-deps chromium` once.

## Running

```bash
python -m app.main            # desktop window (orb + chat + status panels)
python -m app.main --voice    # headless, always listening
python -m app.main --text     # keyboard REPL; works without Claude via keyword fallback
python -m app.main --server   # authenticated HTTP API for the companion app and TV
```

In production, server mode runs on a Debian Optiplex as `jarvis-api.service`.

## Tests

```bash
.venv/bin/python -m unittest            # full suite, from the repo root
.venv/bin/python -m unittest tests.test_server -v
```

## More documentation

- [JARVIS.md](JARVIS.md): full project context, every tool and file
- [ARCHITECTURE.md](ARCHITECTURE.md): runtime layers and design direction
- [ROADMAP.md](ROADMAP.md) and [PRODUCT_VISION_AND_ROADMAP.md](PRODUCT_VISION_AND_ROADMAP.md): where it's going
- [CLAUDE.md](CLAUDE.md): working rules for coding agents

## Security

Never commit `.env` or anything in `memory/`. The companion API uses per-client scoped tokens (`python -m app.client_tokens`), and actions with side effects ask for confirmation first.
