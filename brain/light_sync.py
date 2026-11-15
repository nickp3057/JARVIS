"""Light sync: Nick's room lights follow the film on the room TV.

Ported from the light-sync prototype's ``sync.py`` (2026-11-09). A film's
light track (brain/light_tracks.py, one colour per 0.1 s) plays in step with
Plex: the Govee strip and desk lights stream over the LAN "razer" mode (UDP
4003, 30 frames a second), the lamp gets ``colorwc`` at most 5 times a
second and only when its colour moves, reading ``LIGHT_SYNC_LAMP_LEAD_MS``
ahead because it fades by itself (~0.1 s up, ~0.4 s down).

Hard-won rules from the prototype, kept here:

- The razer ENABLE blips the strip for one frame. It is only re-sent on a
  hard cut (at most every 20 s, where the blip hides) or right after a resume.
- The strips stay in streaming mode, ignoring Home Assistant and its scenes,
  until they get DISABLE (``bb 00 01 b1 00`` + xor). Every stop path sends it
  three times: a normal stop, a crash in the player, Barn Door, Core shutting
  down (``shutdown``, and ``atexit`` as a last resort).

New in JARVIS:

- **Flash limiter** (``FlashLimiter``): colours may change fast, but at most
  ~3 big brightness jumps a second (the photosensitivity guideline; Nick
  hates flashing). A jump over the budget is held to a small step.
- **Intensity**: Subtle / Medium / Intense scale the per-light levels and the
  blend. Medium is the prototype's tuned values (strip 0.35, desk 0.35,
  lamp 0.6, blend 80 ms).
- **Position**, best first: the TV launcher's own report (``POST
  /api/tv/playback``) while it is fresh, else Plex's ``/status/sessions`` for
  the room TV's player (``LIGHT_SYNC_PLEX_PLAYER``, else the only movie
  playing), interpolated between readings.
- **Timing**: the effective offset is the global offset (``memory/light_sync.json``,
  first defaulting to ``LIGHT_SYNC_LEAD_MS``) plus the film's own (the track
  index), positive = lights earlier, each clamped to +/-2000 ms. A nudge
  reaches the running player at once (``Player.lead_ms``).
- **Calibration**: ``calibrate`` plays the flash test clip (``LIGHT_SYNC_TEST_TITLE``,
  building its track first, ahead of the queue) on the TV through
  ``tv_remote.play_plex`` and syncs it whatever the scene; nudges go to the
  global offset; ``end_calibration`` (Done / stop) stops the clip and hands
  the room back as a stop does.
- **When**: in movie mode (the room's current scene is ``scene.movie``), a film
  with a track starts the sync on its own; no track, nothing happens. Stop,
  a film without a track, the end of movie mode or Barn Door release the
  strips and put the Movie scene back (only while movie mode is still on).
  Pause holds the colour. Barn Door always wins; the sync comes back after
  stand-down if it still applies. Nick can start it by hand from the iPad
  or by voice for the film that's playing, movie mode or not.

The auto switch and intensity live in ``memory/light_sync.json`` (the iPad
changes them); device addresses and timings are .env settings, read live.
"""
from __future__ import annotations

import atexit
import base64
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any, Callable

import numpy as np

from brain import light_tracks
from jarvis_logger import get_logger

log = get_logger(__name__)

GOVEE_PORT = 4003
ENABLE = [0xBB, 0x00, 0x01, 0xB1, 0x01]   # razer (streaming) mode on -> "uwABsQEK"
DISABLE = [0xBB, 0x00, 0x01, 0xB1, 0x00]  # off -> "uwABsQAL"
FRAME_S = 1 / 30
RETAKE_EVERY_S = 20.0
CUT = 80  # a target change this big (0-255, any channel) is a hard cut
TV_FRESH_S = 3.0          # a playing report from the TV is trusted this long
TV_FRESH_PAUSED_S = 15.0  # a paused one longer: the TV only repeats it every 5 s
PLEX_FRESH_S = 10.0
PLEX_REANCHOR_S = 2.0     # Plex's viewOffset is coarse: only jump to it when it disagrees this much
MOVIE_SCENE = "scene.movie"
INTENSITIES = ("subtle", "medium", "intense")
OFFSET_STEP_MS = 25           # the iPad's "Lights earlier / later" step
CALIBRATION_START_S = 90.0    # the clip has this long to show up on the TV before calibration gives up
# level multiplier, blend multiplier, big jumps allowed per second
_PRESETS = {"subtle": (0.6, 2.5, 2), "medium": (1.0, 1.0, 3), "intense": (1.6, 0.6, 3)}

_STATE_FILE = Path(__file__).resolve().parents[1] / "memory" / "light_sync.json"


# --- Settings ----------------------------------------------------------------

def _num(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        return float(default)
    return value if math.isfinite(value) else float(default)


def enabled() -> bool:
    """``LIGHT_SYNC``: the master switch (on unless switched off)."""
    return os.getenv("LIGHT_SYNC", "1").strip().lower() not in ("0", "off", "false", "no")


def _ips(name: str, default: str) -> list[str]:
    return [p.strip() for p in os.getenv(name, default).split(",") if p.strip()]


@dataclass(frozen=True)
class Tuning:
    strips: tuple[str, ...]
    lamp: str
    segments: int
    lead_ms: float
    blend_ms: float
    lamp_lead_ms: float
    strip_level: float
    lamp_level: float
    jumps_per_s: int


def tuning(intensity: str = "medium") -> Tuning:
    """The live .env values with ``intensity`` applied."""
    level_x, blend_x, jumps = _PRESETS.get(intensity, _PRESETS["medium"])
    return Tuning(
        strips=tuple(_ips("LIGHT_SYNC_STRIPS", "10.0.0.125,10.0.0.221")),
        lamp=os.getenv("LIGHT_SYNC_LAMP", "10.0.0.164").strip(),
        segments=max(1, min(50, int(_num("LIGHT_SYNC_SEGMENTS", 15)))),
        lead_ms=_num("LIGHT_SYNC_LEAD_MS", 0),
        blend_ms=max(1.0, _num("LIGHT_SYNC_BLEND_MS", 80) * blend_x),
        lamp_lead_ms=_num("LIGHT_SYNC_LAMP_LEAD_MS", 150),
        strip_level=max(0.0, min(1.0, _num("LIGHT_SYNC_STRIP_LEVEL", 0.35) * level_x)),
        lamp_level=max(0.0, min(1.0, _num("LIGHT_SYNC_LAMP_LEVEL", 0.6) * level_x)),
        jumps_per_s=jumps,
    )


def _default_offset() -> int:
    """The global offset before Nick ever nudges it: ``LIGHT_SYNC_LEAD_MS``."""
    return light_tracks.clamp_offset(_num("LIGHT_SYNC_LEAD_MS", 0))


def load_state() -> dict[str, Any]:
    """{"auto": bool, "intensity": str, "offset_ms": int}, defaults when the
    file is missing or bad (``offset_ms`` from ``LIGHT_SYNC_LEAD_MS`` until saved)."""
    state = {"auto": True, "intensity": "medium", "offset_ms": _default_offset()}
    try:
        data = json.loads(_STATE_FILE.read_text())
    except (OSError, ValueError):
        return state
    if isinstance(data, dict):
        if isinstance(data.get("auto"), bool):
            state["auto"] = data["auto"]
        if data.get("intensity") in INTENSITIES:
            state["intensity"] = data["intensity"]
        if isinstance(data.get("offset_ms"), (int, float)) and not isinstance(data.get("offset_ms"), bool):
            state["offset_ms"] = light_tracks.clamp_offset(data["offset_ms"])
    return state


def save_state(state: dict[str, Any]) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(".json.tmp")
        body = {"auto": bool(state["auto"]), "intensity": state["intensity"]}
        if "offset_ms" in state:
            body["offset_ms"] = light_tracks.clamp_offset(state["offset_ms"])
        tmp.write_text(json.dumps(body))
        tmp.replace(_STATE_FILE)
    except OSError as exc:
        log.error("Could not save light sync state: %s", exc)


# --- Govee LAN ---------------------------------------------------------------

def razer_packet(raw: list[int]) -> str:
    """A razer payload with its xor checksum, base64 for the ``pt`` field."""
    x = 0
    for byte in raw:
        x ^= byte
    return base64.b64encode(bytes(raw) + bytes([x])).decode()


class GoveeLan:
    """Fire-and-forget UDP to Govee lights. A Wi-Fi blip skips a frame, never raises."""

    def __init__(self, sock: socket.socket | None = None) -> None:
        self.sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.errors: dict[str, int] = {}

    def send(self, ip: str, cmd: str, data: dict[str, Any]) -> None:
        try:
            self.sock.sendto(json.dumps({"msg": {"cmd": cmd, "data": data}}).encode(), (ip, GOVEE_PORT))
        except OSError as exc:
            self.errors[ip] = self.errors.get(ip, 0) + 1
            if self.errors[ip] % 300 == 1:
                log.warning("Light sync send to %s failed: %s", ip, exc)

    def razer(self, raw: list[int], ips: tuple[str, ...] | list[str]) -> None:
        packet = razer_packet(raw)
        for ip in ips:
            self.send(ip, "razer", {"pt": packet})

    def wake(self, ips: list[str]) -> None:
        for ip in ips:
            self.send(ip, "turn", {"value": 1})
            self.send(ip, "brightness", {"value": 100})

    def enable(self, ips: tuple[str, ...]) -> None:
        self.razer(ENABLE, ips)

    def disable(self, ips: tuple[str, ...]) -> None:
        """Out of streaming mode, three times (UDP may drop one)."""
        for _ in range(3):
            self.razer(DISABLE, ips)

    def strip(self, ip: str, rgb: list[int], segments: int) -> None:
        self.razer([0xBB, 0x00, 2 + 3 * segments, 0xB0, 0x01, segments] + list(rgb) * segments, (ip,))

    def lamp(self, ip: str, rgb: list[int]) -> None:
        self.send(ip, "colorwc", {"color": dict(zip("rgb", (int(v) for v in rgb))), "colorTemInKelvin": 0})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# --- Flash limiter -----------------------------------------------------------

class FlashLimiter:
    """At most ``per_second`` big brightness jumps in any second.

    Brightness is a colour's brightest channel (0-255). The output is
    followed as runs of one direction (up or down); a run that moves more
    than ``jump`` from where it began is one big jump, however many frames
    the blend spreads it over. Over the budget, a run is held to ``jump``
    from its start (keeping its hue) until the oldest jump is a second old,
    so a strobing scene becomes a gentle shimmer. Hue changes at the same
    brightness are free.
    """

    def __init__(self, per_second: int = 3, jump: float = 64.0) -> None:
        self.per_second, self.jump = per_second, jump
        self.jumps: list[float] = []
        self.anchor: float | None = None  # brightness where this run began
        self.direction = 0
        self.counted = False
        self.last_b: float | None = None
        self.last_out = np.zeros(3, np.float32)

    def apply(self, rgb: np.ndarray, now: float) -> np.ndarray:
        rgb = np.asarray(rgb, np.float32)
        b = float(rgb.max())
        if self.last_b is None:
            self.anchor = self.last_b = b
            self.last_out = rgb
            return rgb
        delta = b - self.last_b
        if abs(delta) > 0.5 and (1 if delta > 0 else -1) != self.direction:
            # A new run (a reversal): it starts where the light is now.
            self.anchor, self.direction, self.counted = self.last_b, (1 if delta > 0 else -1), False
        out = rgb
        if abs(b - self.anchor) > self.jump and not self.counted:
            self.jumps = [t for t in self.jumps if now - t < 1.0]
            if len(self.jumps) < self.per_second:
                self.jumps.append(now)
                self.counted = True
            else:
                target = self.anchor + self.direction * self.jump
                if b > 1e-3:
                    out = rgb * (target / b)
                elif float(self.last_out.max()) > 1e-3:
                    out = self.last_out * (target / float(self.last_out.max()))
                else:
                    out = np.full(3, target, np.float32)
        out = np.asarray(out, np.float32).clip(0, 255)
        self.last_b = float(out.max())
        self.last_out = out
        return out


# --- Where the film is -------------------------------------------------------

@dataclass
class Playback:
    rating_key: str
    title: str
    state: str          # playing / paused / buffering / stopped
    position_s: float   # at ``at``
    speed: float
    at: float           # time.monotonic() of the reading
    source: str         # "tv" or "plex"

    @property
    def moving(self) -> bool:
        return self.state == "playing" and self.speed > 0

    def position(self, now: float) -> float:
        return self.position_s + (now - self.at) * self.speed if self.moving else self.position_s


class PlaybackClock:
    """The film's position: the TV's report while fresh, else Plex, interpolated."""

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self.now = now
        self.tv: Playback | None = None
        self.plex: Playback | None = None
        self.plex_polled_at = -1e9
        self._lock = threading.Lock()

    def tv_report(self, *, state: str, position_ms: float, speed: float = 1.0, title: str = "",
                  rating_key: str = "") -> None:
        state = state if state in ("playing", "paused", "buffering", "stopped") else "stopped"
        key = str(rating_key or "")
        if not key and title:
            plex = self.plex
            if plex is not None and _same_title(plex.title, title):
                key = plex.rating_key
            else:
                key = light_tracks.find_by_title(title) or ""
        with self._lock:
            self.tv = Playback(key, title or "", state, max(0.0, position_ms / 1000),
                               float(speed) if state == "playing" else 0.0, self.now(), "tv")

    def plex_report(self, session: dict[str, Any] | None) -> None:
        now = self.now()
        with self._lock:
            self.plex_polled_at = now
            if not session:
                self.plex = None
                return
            state = session.get("state") or "playing"
            state = state if state in ("playing", "paused", "buffering") else "stopped"
            pos = session.get("view_offset_ms", 0) / 1000
            speed = 1.0 if state == "playing" else 0.0
            old = self.plex
            if (old is not None and old.rating_key == session.get("rating_key") and old.state == state
                    and abs(old.position(now) - pos) <= PLEX_REANCHOR_S):
                return  # close enough: keep the smoother interpolation
            self.plex = Playback(str(session.get("rating_key") or ""), str(session.get("title") or ""),
                                 state, pos, speed, now, "plex")

    def tv_fresh(self) -> bool:
        tv = self.tv
        if tv is None:
            return False
        limit = TV_FRESH_S if tv.state in ("playing", "stopped") else TV_FRESH_PAUSED_S
        return self.now() - tv.at <= limit

    def current(self) -> Playback | None:
        """What's on the room TV now, or None when nothing is (or nobody knows)."""
        with self._lock:
            tv, plex = self.tv, self.plex
            plex_fresh = plex is not None and self.now() - self.plex_polled_at <= PLEX_FRESH_S
        if tv is not None and self.tv_fresh():
            if tv.state == "stopped":
                return None
            key = tv.rating_key
            if not key and plex_fresh and _same_title(plex.title, tv.title):
                key = plex.rating_key
            if key or tv.title:
                return Playback(key, tv.title or (plex.title if plex_fresh else ""), tv.state, tv.position_s,
                                tv.speed, tv.at, "tv")
        if plex_fresh and plex.state != "stopped":
            return plex
        return None


def _same_title(a: str, b: str) -> bool:
    from integrations.plex import _title_key
    return bool(a and b) and _title_key(a) == _title_key(b)


def pick_session(sessions: list[dict[str, Any]], player: str = "") -> dict[str, Any] | None:
    """The room TV's movie session: the one whose player matches
    ``LIGHT_SYNC_PLEX_PLAYER`` (title, product or machineIdentifier), else
    the only movie playing on any player."""
    movies = [s for s in sessions if s.get("type") == "movie"]
    wanted = player.strip().lower()
    if wanted:
        for s in movies:
            p = s.get("player") or {}
            names = [str(p.get(k, "")).lower() for k in ("title", "product", "machine_id")]
            if wanted in names or wanted in names[0]:
                return s
        return None
    return movies[0] if len(movies) == 1 else None


# --- The player --------------------------------------------------------------

class Player:
    """Streams one track to the lights at 30 Hz until stopped. Whatever ends
    it (stop, an error), the strips get DISABLE on the way out."""

    def __init__(self, track: np.ndarray, rating_key: str, clock: Callable[[], tuple[float, float] | None],
                 tune: Callable[[], Tuning], sender: GoveeLan, *, now: Callable[[], float] = time.monotonic,
                 on_exit: Callable[["Player"], None] | None = None, lead_ms: float | None = None) -> None:
        self.track = np.asarray(track, np.float32)
        # The timing offset (ms, positive = lights earlier), read every frame so
        # a nudge lands at once; None = the tuning's LIGHT_SYNC_LEAD_MS.
        self.lead_ms = lead_ms
        self.rating_key = rating_key
        self.clock, self.tune, self.sender, self.now = clock, tune, sender, now
        self.on_exit = on_exit
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._released = threading.Lock()
        self._released_done = False
        self.strips: tuple[str, ...] = ()
        self.colour = np.zeros(3, np.float32)
        self.retake = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="light-sync")
        self._thread.start()

    def stop(self, wait: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(wait)
        self.release()

    def release(self) -> None:
        """DISABLE to every strip this player took; once."""
        with self._released:
            if self._released_done:
                return
            self._released_done = True
        strips = self.strips or self.tune().strips
        self.sender.disable(strips)
        log.info("Light sync released %s", ", ".join(strips))

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:  # noqa: BLE001 - lights must never stay stuck in streaming mode
            log.exception("Light sync player crashed")
        finally:
            self.release()
            if self.on_exit:
                try:
                    self.on_exit(self)
                except Exception:  # noqa: BLE001
                    log.exception("Light sync exit hook failed")

    def _loop(self) -> None:
        tune = self.tune()
        self.strips = tune.strips
        self.sender.wake(list(tune.strips) + ([tune.lamp] if tune.lamp else []))
        self.sender.enable(tune.strips)
        enabled_at = tuned_at = last = self.now()
        limiter, lamp_limiter = FlashLimiter(tune.jumps_per_s), FlashLimiter(tune.jumps_per_s)
        prev_tgt = cur = None
        lamp_last, lamp_sent, sent_at = -1e9, None, -1e9
        was_moving = None
        frames = len(self.track)
        while not self._stop.wait(FRAME_S):
            now = self.now()
            dt, last = now - last, now
            if now - tuned_at >= 1.0:
                tune, tuned_at = self.tune(), now
                limiter.per_second = lamp_limiter.per_second = tune.jumps_per_s
            reading = self.clock()
            if reading is None:
                continue  # don't know where the film is: hold
            pos, speed = reading
            moving = speed > 0
            if moving and was_moving is False:
                self.retake = True  # resumed: the strips may have been taken back meanwhile
            was_moving = moving
            if not moving and now - sent_at < 0.2:
                continue  # paused: hold the colour, a few refreshes a second is plenty
            lead = tune.lead_ms if self.lead_ms is None else self.lead_ms
            f = (pos + lead / 1000) * light_tracks.FPS
            i = int(f)
            if not 0 <= i < frames - 1:
                continue
            tgt = self.track[i] + (self.track[i + 1] - self.track[i]) * (f - i)
            if cur is None:
                cur, prev_tgt = tgt.copy(), tgt
            cur = cur + (tgt - cur) * (1 - math.exp(-dt / tune.blend_ms * 1000))
            cur = limiter.apply(cur.clip(0, 255), now)
            self.colour = cur
            # Re-take the strips (something outside keeps pulling them back to
            # their scene). Each enable blips the strip for one frame: so only
            # on a hard cut, where it hides, at most every 20 s; or after a resume.
            cut = float(np.abs(tgt - prev_tgt).max()) > CUT
            if self.retake or (cut and now - enabled_at > RETAKE_EVERY_S):
                self.sender.enable(tune.strips)
                self.retake, enabled_at = False, now
            prev_tgt = tgt
            rgb = cur.astype(int)
            for ip in tune.strips:
                self.sender.strip(ip, [int(v * tune.strip_level) for v in rgb], tune.segments)
            sent_at = now
            if tune.lamp:
                # The lamp fades on its own (~0.1 s up, ~0.4 s down): it reads ahead.
                lf = f + tune.lamp_lead_ms / 1000 * light_tracks.FPS
                li = min(max(int(lf), 0), frames - 2)
                lrgb = (self.track[li] + (self.track[li + 1] - self.track[li]) * (lf - li)).clip(0, 255)
                if now - lamp_last > 0.2 and (lamp_sent is None or float(np.abs(lrgb - lamp_sent).max()) > 6):
                    shown = lamp_limiter.apply(lrgb, now)
                    self.sender.lamp(tune.lamp, [int(v * tune.lamp_level) for v in shown])
                    lamp_last, lamp_sent = now, lrgb


# --- The controller ----------------------------------------------------------

@dataclass
class _Session:
    rating_key: str
    title: str
    player: Player
    paused_follow: bool = False
    started_at: float = field(default_factory=time.time)


class LightSync:
    """Decides when the lights sync and owns the one player. The runtime's
    monitor calls ``tick`` about once a second; the tools and the API call
    ``start`` / ``stop`` / ``set_intensity`` / ``set_auto``."""

    def __init__(self, sender_factory: Callable[[], GoveeLan] = GoveeLan,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.sender_factory = sender_factory
        self.now = now
        self.clock = PlaybackClock(now)
        self.session: _Session | None = None
        self.manual_key: str | None = None      # Nick started it by hand for this film
        self.suppressed_key: str | None = None  # Nick stopped it for this film
        self.movie_mode = False
        self.barn_door = False
        self.reason = ""
        # Calibrating with the flash test clip: {rating_key, title, phase
        # ("building" | "playing"), since (monotonic), seen (on the TV yet)}.
        self.calibration: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._revision = 0
        self._sender: GoveeLan | None = None
        light_tracks.add_listener(self.bump)

    # -- state the apps see
    def bump(self) -> None:
        self._revision += 1

    def revision(self) -> int:
        return self._revision

    @property
    def running(self) -> bool:
        return self.session is not None

    def armed(self) -> bool:
        """Worth asking Plex every second: movie mode, a manual start, or running."""
        return enabled() and (self.movie_mode or self.manual_key is not None or self.session is not None
                              or self.calibration is not None)

    def poll_plex(self) -> None:
        from integrations import plex
        if not plex.is_configured():
            return
        ok, found = plex.sessions()
        if not ok:
            log.debug("Light sync: Plex sessions unavailable: %s", found)
            return
        self.clock.plex_report(pick_session(found, os.getenv("LIGHT_SYNC_PLEX_PLAYER", "")))

    def tv_report(self, **report: Any) -> None:
        before = self._film_signature()
        self.clock.tv_report(**report)
        if self._film_signature() != before:
            self.bump()

    def _film_signature(self) -> tuple[str, str, str] | None:
        pb = self.clock.current()
        return (pb.rating_key, pb.title, "paused" if not pb.moving else "playing") if pb else None

    # -- the decision
    def tick(self, *, movie_mode: bool, barn_door: bool) -> None:
        with self._lock:
            before = self._film_signature()
            if movie_mode and not self.movie_mode:
                self.suppressed_key = None  # a fresh movie night forgets an old "stop"
            self.movie_mode, self.barn_door = movie_mode, barn_door
            pb = self.clock.current()
            film = pb.rating_key if pb else None
            self._calibration_tick(film)
            if self.manual_key and self.manual_key != film:
                self.manual_key = None  # the film it was started for has ended or changed
            if self.suppressed_key and self.suppressed_key != film:
                self.suppressed_key = None
            want, reason = self._wanted(pb)
            self.reason = reason
            if self.session and (not want or self.session.rating_key != film):
                self._stop_session(restore=not barn_door)
            if want and self.session is None and pb is not None:
                self._start_session(pb)
            if self._film_signature() != before:
                self.bump()

    def _wanted(self, pb: Playback | None) -> tuple[bool, str]:
        if not enabled():
            return False, "Light sync is switched off (LIGHT_SYNC)."
        if pb is None or not pb.rating_key:
            return False, "Calibrating: waiting for the test clip." if self.calibration \
                else "Nothing is playing on the TV."
        if not light_tracks.has_track(pb.rating_key):
            return False, f"{pb.title or 'This film'} has no light sync yet."
        if self.barn_door:
            return False, "Barn Door has the lights."
        if self.calibration and self.calibration["rating_key"] == pb.rating_key:
            return True, ""  # a manual start: whatever the scene, auto or not
        if self.manual_key == pb.rating_key:
            return True, ""
        if self.suppressed_key == pb.rating_key:
            return False, "Stopped for this film."
        if not self.movie_mode:
            return False, "Not in movie mode."
        if not load_state()["auto"]:
            return False, "Auto light sync is off."
        return True, ""

    def _start_session(self, pb: Playback) -> None:
        track = light_tracks.load(pb.rating_key)
        if track is None:
            self.reason = "The light track couldn't be read."
            return
        if self._sender is None:
            self._sender = self.sender_factory()
        key = pb.rating_key
        meta = light_tracks.get(key) or {}
        title = pb.title or meta.get("title") or "the film"

        def clock() -> tuple[float, float] | None:
            now_pb = self.clock.current()
            if now_pb is None or now_pb.rating_key != key:
                return None
            return now_pb.position(self.now()), now_pb.speed if now_pb.moving else 0.0

        player = Player(track, key, clock, lambda: tuning(load_state()["intensity"]), self._sender,
                        now=self.now, on_exit=self._player_exited, lead_ms=self.effective_offset(key))
        session = _Session(key, title, player)
        session.paused_follow = _pause_follow()
        self.session = session
        player.start()
        log.info("Light sync on: %s (%s)", title, pb.source)
        self.bump()

    def _player_exited(self, player: Player) -> None:
        """The player's thread ended on its own (a crash): tidy up like a stop."""
        if player._stop.is_set():
            return  # a normal stop: whoever stopped it is tidying up (and may hold the lock)
        with self._lock:
            if self.session is not None and self.session.player is player and not player._stop.is_set():
                log.warning("Light sync player ended unexpectedly; handing the room back")
                self._stop_session(restore=not self.barn_door)

    def _stop_session(self, *, restore: bool) -> None:
        session, self.session = self.session, None
        if session is None:
            return
        session.player.stop()  # DISABLE to the strips, whatever happens next
        if session.paused_follow and not _barn_door_engaged():
            _resume_follow()  # Barn Door turns it back on at its own stand-down
        if restore and self.movie_mode:
            _restore_movie_scene()
        log.info("Light sync off: %s", session.title)
        self.bump()

    # -- what Nick asks for
    def start(self) -> tuple[bool, str]:
        """Start for the film playing now, movie mode or not."""
        if not enabled():
            return False, "Light sync is switched off in my settings, sir (LIGHT_SYNC)."
        if not self.clock.tv_fresh():
            self.poll_plex()
        pb = self.clock.current()
        if pb is None or not pb.rating_key:
            return False, "Nothing's playing on the TV, sir."
        title = pb.title or "that film"
        if not light_tracks.has_track(pb.rating_key):
            return False, f"There's no light sync for {title} yet, sir. I can make one if you like."
        if _barn_door_engaged():
            return False, "Barn Door has the lights, sir. Stand down first."
        with self._lock:
            if self.session and self.session.rating_key == pb.rating_key:
                return True, f"Light sync is already running for {title}, sir."
            self.manual_key, self.suppressed_key = pb.rating_key, None
            self.tick(movie_mode=_movie_mode(), barn_door=False)
            if self.session is None:
                return False, f"I couldn't start the light sync for {title}, sir. {self.reason}".strip()
        return True, f"Light sync on for {title}, sir."

    def stop(self) -> tuple[bool, str]:
        if self.calibration is not None:
            return self.end_calibration()
        with self._lock:
            self.manual_key = None
            if self.session is None:
                return False, "Light sync isn't running, sir."
            self.suppressed_key = self.session.rating_key
            title = self.session.title
            self._stop_session(restore=True)
        return True, f"Light sync off for {title}, sir. The Movie scene's back." if self.movie_mode \
            else f"Light sync off for {title}, sir."

    def set_intensity(self, intensity: str) -> None:
        intensity = (intensity or "").strip().lower()
        if intensity not in INTENSITIES:
            raise ValueError(f"intensity must be one of {', '.join(INTENSITIES)}")
        state = load_state()
        state["intensity"] = intensity
        save_state(state)
        self.bump()

    def set_auto(self, auto: bool) -> None:
        state = load_state()
        state["auto"] = bool(auto)
        save_state(state)
        self.bump()

    # -- timing
    def global_offset(self) -> int:
        return int(load_state()["offset_ms"])

    def effective_offset(self, rating_key: str | None) -> int:
        """Global + this film's offset (ms, positive = lights earlier), clamped."""
        return light_tracks.clamp_offset(self.global_offset() + light_tracks.film_offset(rating_key))

    def _target_film(self) -> tuple[str, str] | None:
        """(ratingKey, title) a film-scope nudge applies to: the film syncing,
        else the one on the TV if it has a track."""
        session = self.session
        if session is not None:
            return session.rating_key, session.title
        pb = self.clock.current()
        if pb is not None and pb.rating_key and light_tracks.has_track(pb.rating_key):
            meta = light_tracks.get(pb.rating_key) or {}
            return pb.rating_key, pb.title or str(meta.get("title") or "the film")
        return None

    def _apply_offset(self) -> None:
        """Hand the running player its new offset (it reads it every frame)."""
        session = self.session
        if session is not None:
            session.player.lead_ms = self.effective_offset(session.rating_key)
        self.bump()

    def _resolve_scope(self, scope: str | None) -> str:
        scope = (scope or "").strip().lower()
        if scope in ("film", "global"):
            return scope
        return "global" if self.calibration is not None or self._target_film() is None else "film"

    def nudge(self, ms: float, scope: str | None = None) -> tuple[bool, str]:
        """Move the lights' timing by ``ms`` (positive = earlier). ``scope``
        film (the film syncing) or global; by default the film, or global
        while calibrating or when no film with a track is on."""
        try:
            ms = float(ms)
        except (TypeError, ValueError):
            return False, "How far, sir? Give me a number of milliseconds."
        if not math.isfinite(ms) or abs(ms) < 0.5:
            return False, "How far, sir? Give me a number of milliseconds."
        with self._lock:
            scope = self._resolve_scope(scope)
            if scope == "film":
                target = self._target_film()
                if target is None:
                    return False, "There's no film with a light sync on, sir. Say globally to move the default timing."
                key, title = target
                before = light_tracks.film_offset(key)
                after = light_tracks.set_film_offset(key, before + ms)
                if after is None:
                    return False, f"{title} has no light sync to adjust, sir."
                self._apply_offset()
                return True, self._offset_said(after - before, f"for {title}", after, key)
            state = load_state()
            before = int(state["offset_ms"])
            state["offset_ms"] = light_tracks.clamp_offset(before + ms)
            save_state(state)
            self._apply_offset()
            return True, self._offset_said(state["offset_ms"] - before, "globally", state["offset_ms"], None)

    def reset_offset(self, scope: str | None = None) -> tuple[bool, str]:
        """Back to 0: the film's offset, or the global one while calibrating."""
        with self._lock:
            scope = self._resolve_scope(scope)
            if scope == "film":
                target = self._target_film()
                if target is None:
                    return False, "There's no film with a light sync on, sir."
                light_tracks.set_film_offset(target[0], 0)
                self._apply_offset()
                return True, f"Timing for {target[1]} reset, sir. The global offset is {_ms(self.global_offset())}."
            state = load_state()
            state["offset_ms"] = 0
            save_state(state)
            self._apply_offset()
            return True, "Global light timing reset to 0 ms, sir."

    def _offset_said(self, moved: float, where: str, now_ms: int, key: str | None) -> str:
        if moved == 0:
            edge = "earlier" if now_ms > 0 else "later"
            return f"That's as far {edge} as I'll go {where}, sir: {_ms(now_ms)}."
        way = "earlier" if moved > 0 else "later"
        said = f"Lights {abs(int(round(moved)))} ms {way} {where}, sir: now {_ms(now_ms)}"
        if key is not None and self.global_offset():
            said += f" (effective {_ms(self.effective_offset(key))})"
        return said + "."

    def offsets(self) -> dict[str, Any]:
        target = self._target_film()
        film_ms = light_tracks.film_offset(target[0]) if target else 0
        return {"global_ms": self.global_offset(), "film_ms": film_ms,
                "effective_ms": self.effective_offset(target[0] if target else None),
                "film_title": target[1] if target else None,
                "scope": self._resolve_scope(None), "step_ms": OFFSET_STEP_MS,
                "limit_ms": light_tracks.OFFSET_LIMIT_MS}

    # -- calibration
    def calibrate(self) -> tuple[bool, str]:
        """Play the flash test clip on the TV and sync it, whatever the
        scene; nudges go to the global offset until Done."""
        from integrations import plex
        if not enabled():
            return False, "Light sync is switched off in my settings, sir (LIGHT_SYNC)."
        if _barn_door_engaged():
            return False, "Barn Door has the lights, sir. Stand down first."
        if not plex.is_configured():
            return False, "I can't reach Plex to find the test clip, sir (PLEX_URL / PLEX_TOKEN aren't set)."
        title = test_title()
        ok, film = plex.find_movie(title)
        if not ok or not isinstance(film, dict):
            return False, f"I can't find the calibration clip ({title}) on Plex, sir."
        key, name = str(film["rating_key"]), str(film.get("title") or title)
        with self._lock:
            if self.session is not None and self.session.rating_key != key:
                self.manual_key = None
                self._stop_session(restore=False)  # the clip takes the lights next
            self.calibration = {"rating_key": key, "title": name, "phase": "building", "since": self.now(),
                                "seen": False}
            self.suppressed_key = None
            self.bump()
        if not light_tracks.has_track(key):
            # Ahead of the queue (or moved up if it was already waiting).
            started, message = light_tracks.start_build(film, front=True, quiet=True)
            if not started and not light_tracks.pending(key):
                with self._lock:
                    self.calibration = None
                    self.bump()
                return False, message
            return True, ("Building the calibration track first, sir; it's a short clip. I'll start it on the TV "
                          "the moment it's ready.")
        return self._play_calibration()

    def _play_calibration(self) -> tuple[bool, str]:
        from integrations import plex, tv_remote
        calibration = self.calibration
        if calibration is None:
            return False, "Calibration isn't on, sir."
        sent, detail = tv_remote.play_plex(calibration["rating_key"], plex.server_id())
        with self._lock:
            if not sent:
                self.calibration = None
                self.bump()
                log.warning("Light sync calibration: the TV didn't take the clip: %s", detail)
                return False, f"The TV didn't take the test clip, sir: {detail}"
            calibration.update(phase="playing", since=self.now(), seen=False)
            self.bump()
        return True, (f"Calibrating, sir: {calibration['title']} is starting on the TV. Watch the flashes and "
                      "tell me if the lights are early or late; I'll move the global timing. Say done when they match.")

    def _calibration_tick(self, film: str | None) -> None:
        """Called from ``tick`` (lock held): the clip's track is ready -> play
        it; it ended on the TV (or never started) -> calibration is over."""
        calibration = self.calibration
        if calibration is None:
            return
        key = calibration["rating_key"]
        if calibration["phase"] == "building":
            if light_tracks.has_track(key):
                self._play_calibration()
            elif not light_tracks.pending(key):
                log.warning("Light sync calibration: the test clip's track didn't build")
                self.calibration, self.reason = None, "The calibration track didn't build."
                self.bump()
            return
        if film == key:
            calibration["seen"] = True
        elif calibration["seen"] or self.now() - calibration["since"] > CALIBRATION_START_S:
            log.info("Light sync calibration over (%s)", "clip ended" if calibration["seen"] else "clip never started")
            self.calibration = None
            self.bump()

    def end_calibration(self) -> tuple[bool, str]:
        """Done: stop syncing, stop the clip if it's still on, hand the room back."""
        with self._lock:
            calibration, self.calibration = self.calibration, None
            if calibration is None:
                return False, "I'm not calibrating, sir."
            pb = self.clock.current()
            on_tv = pb is not None and pb.rating_key == calibration["rating_key"] and pb.state != "stopped"
            if self.manual_key == calibration["rating_key"]:
                self.manual_key = None
            if on_tv:
                self.suppressed_key = calibration["rating_key"]  # no auto restart on the clip's last seconds
            if self.session is not None and self.session.rating_key == calibration["rating_key"]:
                self._stop_session(restore=True)
            self.bump()
        if on_tv:
            from integrations import tv_remote
            try:
                tv_remote.stop_playback()
            except Exception:  # noqa: BLE001 - the lights are already handed back
                log.warning("Light sync couldn't stop the test clip", exc_info=True)
        return True, f"Calibration done, sir. The global timing is {_ms(self.global_offset())}."

    def release_for_barn_door(self) -> None:
        """Barn Door is engaging: let go of the strips now (not at the next
        tick), so its red reaches them. No Movie scene: Barn Door owns the room."""
        with self._lock:
            self.barn_door = True
            if self.session is not None:
                self.session.paused_follow = False  # Barn Door's stand-down turns it back on
                self._stop_session(restore=False)

    def shutdown(self) -> None:
        """Core is stopping: release the strips (and the fan's automation)."""
        with self._lock:
            session, self.session = self.session, None
        if session is not None:
            session.player.stop(wait=1.0)
            if session.paused_follow and not _barn_door_engaged():
                _resume_follow()

    def status(self) -> dict[str, Any]:
        state = load_state()
        pb = self.clock.current()
        film = None
        if pb is not None and (pb.rating_key or pb.title):
            meta = light_tracks.get(pb.rating_key) if pb.rating_key else None
            film = {"rating_key": pb.rating_key or None, "title": pb.title or (meta or {}).get("title") or "",
                    "state": pb.state, "source": pb.source}
        session = self.session
        return {
            "enabled": enabled(),
            "auto": state["auto"],
            "intensity": state["intensity"],
            "intensities": list(INTENSITIES),
            "running": session is not None,
            "running_title": session.title if session else None,
            "paused": bool(session and pb is not None and not pb.moving),
            "movie_mode": self.movie_mode,
            "film": film,
            "has_track": bool(pb and light_tracks.has_track(pb.rating_key)),
            "building": light_tracks.building(),
            "queue": light_tracks.queue(),
            "tracks": light_tracks.listing(),
            "offset": self.offsets(),
            "calibrating": ({"title": self.calibration["title"], "phase": self.calibration["phase"]}
                            if self.calibration else None),
            "reason": "" if session else self.reason,
            "revision": self._revision,
        }


def test_title() -> str:
    """``LIGHT_SYNC_TEST_TITLE``: the flash test clip on Plex used to calibrate."""
    return os.getenv("LIGHT_SYNC_TEST_TITLE", "").strip() or "Light Sync Test"


def _ms(value: float) -> str:
    value = int(round(value))
    return f"+{value} ms" if value > 0 else f"{value} ms"


# --- Home Assistant and the room ----------------------------------------------

def _movie_mode() -> bool:
    try:
        from app import status_hub
        return status_hub.home_scenes().get("current") == MOVIE_SCENE
    except Exception:  # noqa: BLE001 - HA down: not movie mode
        return False


def _barn_door_engaged() -> bool:
    try:
        from brain import protocols
        return protocols.engaged()
    except Exception:  # noqa: BLE001
        return False


def _pause_follow() -> bool:
    """The fan ring follows the strip (HA automation); not while syncing.
    True if it was switched off here (so the stop turns it back on)."""
    if _barn_door_engaged():
        return False
    try:
        from brain.protocols import _FOLLOW
        from integrations import home_assistant
        return bool(home_assistant.call_service("automation", "turn_off", _FOLLOW)[0])
    except Exception:  # noqa: BLE001
        log.warning("Light sync couldn't pause the fan's follow automation", exc_info=True)
        return False


def _resume_follow() -> None:
    try:
        from brain.protocols import _FOLLOW
        from integrations import home_assistant
        home_assistant.call_service("automation", "turn_on", _FOLLOW)
    except Exception:  # noqa: BLE001
        log.warning("Light sync couldn't resume the fan's follow automation", exc_info=True)


def _restore_movie_scene() -> None:
    try:
        from app import status_hub
        result = status_hub.activate_scene(MOVIE_SCENE)
        if result.get("error"):
            log.warning("Light sync couldn't put the Movie scene back: %s", result["error"])
    except Exception:  # noqa: BLE001
        log.warning("Light sync couldn't put the Movie scene back", exc_info=True)


# --- The one controller -----------------------------------------------------

controller = LightSync()


def monitor_pass() -> float:
    """One pass of the runtime's monitor; returns seconds until the next."""
    if not enabled():
        if controller.running:
            controller.tick(movie_mode=_movie_mode(), barn_door=_barn_door_engaged())
        return 5.0
    movie_mode = _movie_mode()
    barn_door = _barn_door_engaged()
    if movie_mode or controller.armed():
        # The TV's report carries the position; Plex still names the film now and then.
        if not controller.clock.tv_fresh() or controller.now() - controller.clock.plex_polled_at > 5:
            controller.poll_plex()
    controller.tick(movie_mode=movie_mode, barn_door=barn_door)
    return 1.0 if controller.armed() else 5.0


def release_for_barn_door() -> None:
    try:
        controller.release_for_barn_door()
    except Exception:  # noqa: BLE001 - Barn Door must engage regardless
        log.exception("Light sync couldn't release for Barn Door")


def shutdown() -> None:
    try:
        controller.shutdown()
    except Exception:  # noqa: BLE001
        log.exception("Light sync shutdown failed")


atexit.register(shutdown)
