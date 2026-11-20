"""Questions the room voice answers itself, like a smart speaker, with no
model in the loop: the time and date, the weather right now, today's
calendar, timers ("ten minute pasta timer", "how long's left", "cancel the
timer"), the speaker's volume ("volume 4", "louder"), Spotify ("pause",
"next", "what's playing", "play Back in Black"), every light on or off,
"stop" and "say that again".

``answer(text)`` returns an ``Answer`` or None (not one of these: the
conversation handles it). Anything about later ("weather tomorrow",
"forecast for the weekend") is left to the conversation, which can look
further ahead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from brain import timers
from speech import room_volume

_STRIP = re.compile(r"\b(hey|ok|okay|jarvis|please|for me|could you|can you|would you)\b")


@dataclass
class Answer:
    say: str = ""        # spoken; empty plays only the sound
    sound: str = ""      # "done", "error" or "" (none)
    stop: bool = False   # "stop": end alarms and speech, say nothing
    kind: str = ""       # for the log: time, weather, timer, volume...


def normalize(text: str) -> str:
    text = re.sub(r"[^\w\s%:'.]", " ", (text or "").lower()).replace("’", "'")
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # keep 1.5, drop full stops
    return re.sub(r"\s+", " ", _STRIP.sub(" ", text)).strip()


# --- Numbers and durations ---------------------------------------------------------

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
          "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "a": 1, "an": 1}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_NUMBER = (r"(?:\d+(?:\.\d+)?|(?:" + "|".join(_TENS) + r")(?:[ -](?:" + "|".join(_UNITS) + r"))?|"
           + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")")
_HALF = r"(?:\s+and\s+a\s+half)?"
_PART = re.compile(rf"\b({_NUMBER}){_HALF}\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b{_HALF}")


def number(word: str) -> float | None:
    word = word.strip().replace("-", " ")
    try:
        return float(word)
    except ValueError:
        pass
    parts = word.split()
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS:
        return float(_TENS[parts[0]] + _UNITS[parts[1]])
    if len(parts) == 1:
        if parts[0] in _TENS:
            return float(_TENS[parts[0]])
        if parts[0] in _UNITS:
            return float(_UNITS[parts[0]])
    return None


def parse_duration(text: str) -> int | None:
    """Seconds in "10 minutes", "an hour and a half", "1 hour 20 minutes",
    "half an hour", "ninety seconds"; None if there's no duration."""
    text = text.replace("half an hour", "30 minutes").replace("half a minute", "30 seconds")
    total = 0.0
    found = False
    for match in _PART.finditer(text):
        value = number(match.group(1))
        if value is None:
            continue
        if "and a half" in match.group(0):
            value += 0.5
        unit = match.group(2)[0]
        total += value * (3600 if unit == "h" else 60 if unit == "m" else 1)
        found = True
    return int(round(total)) if found and total > 0 else None


def say_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs and not hours:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " and ".join(parts) if parts else "no time"


def _clock(now: datetime) -> str:
    return now.strftime("%I:%M").lstrip("0")


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def timer_label(message: str) -> str:
    """'' for an unnamed timer (the tool's default "Timer up")."""
    message = (message or "").strip()
    return "" if message.lower() in ("", "timer", "timer up", "timer's up") else message


# --- Patterns --------------------------------------------------------------------

_STOP = re.compile(r"^(stop|stop it|stop that|cancel|never ?mind|shut up|be quiet|quiet|enough|that's enough|"
                   r"thats enough|silence|forget it|nothing|dismiss|alarm off|stop the alarm|"
                   r"okay stop|ok stop)$")
# Whole questions only: "what time is it in Tokyo" and "the date of the Super
# Bowl" are for the conversation.
_TIME = re.compile(r"^(?:so |um |uh )?(what time is it|what's the time|whats the time|what is the time|time|"
                   r"tell me the time|got the time|what time it is|do you know what time it is)"
                   r"(?: now| right now| please)?$")
_DATE = re.compile(r"^(what's the date|whats the date|what is the date|what's today's date|today's date|"
                   r"what day is it|what day is today|what's today|what is today|what's the date today)"
                   r"(?: today)?$")
_WEATHER = re.compile(r"\b(weather|temperature|how (?:hot|cold|warm|chilly) is it|is it (?:cold|hot|warm|chilly)|"
                      r"what's it like outside|how is it outside|how's it outside)\b")
_LATER = re.compile(r"\b(tomorrow|tonight|week|weekend|forecast|later|monday|tuesday|wednesday|thursday|friday|"
                    r"saturday|sunday|next|hourly|rain|snow|in [a-z]+ (?:city|utah|colorado|california))\b")
_TIMER_SET = re.compile(r"\btimer\b")
_TIMER_LEFT = re.compile(r"\b(how much time|how long|time left|what's left|how's my timer|hows my timer|"
                         r"check (?:my |the )?timers?|timer status|how long is left|how much longer)\b")
_TIMER_CANCEL = re.compile(r"^(?:cancel|stop|delete|remove|kill|clear|end|turn off) (?:the |my |all |all the |all my )?"
                           r"(?:(?P<label>[a-z' ]+?) )?timers?$")
_VOLUME_SET = re.compile(r"\b(?:volume|loudness)(?: (?:to|at|level))? (?P<n>\d{1,3}|"
                         + "|".join(list(_UNITS)[1:13]) + r"|ten)(?: ?%| percent)?$|"
                         r"\bset (?:the )?volume to (?P<m>\d{1,3})")
_VOLUME_UP = re.compile(r"^(?:volume up|louder|turn it up|turn (?:the )?volume up|turn up (?:the )?volume|"
                        r"speak up|a bit louder|little louder|raise (?:the )?volume|increase (?:the )?volume)$")
_VOLUME_DOWN = re.compile(r"^(?:volume down|quieter|softer|turn it down|turn (?:the )?volume down|"
                          r"turn down (?:the )?volume|a bit quieter|little quieter|lower (?:the )?volume|"
                          r"decrease (?:the )?volume|too loud)$")
_VOLUME_MAX = re.compile(r"^(?:max(?:imum)? volume|full volume|volume max|volume all the way up)$")
_VOLUME_ASK = re.compile(r"^(?:what's the volume|whats the volume|what is the volume|what volume are you at|"
                         r"how loud are you)$")
_CALENDAR = re.compile(r"^(?:what's|whats|what is) (?:on )?my (?:calendar|schedule|agenda)(?: today| for today| look like)?$|"
                       r"^(?:what do i have|what have i got|what's happening|whats happening) today$|"
                       r"^what does my day look like$|^what's my day look like$|^anything on my calendar(?: today)?$")
_PAUSE = re.compile(r"^(?:pause|pause it|pause (?:the )?(?:music|song)|stop (?:the )?music|hold the music)$")
_RESUME = re.compile(r"^(?:resume|unpause|play|resume (?:the )?music|keep playing|continue|play music|"
                     r"play some music|start the music)$")
_NEXT = re.compile(r"^(?:next|skip|next song|next track|skip (?:this |the )?(?:song|track)|skip it)$")
_PREVIOUS = re.compile(r"^(?:previous|previous song|previous track|last song|go back a song|play the last song|"
                       r"play the previous song)$")
_NOW_PLAYING = re.compile(r"^(?:what's playing|whats playing|what is playing|what song is this|what's this song|"
                          r"whats this song|what is this song|who sings this|who is this|what am i listening to)$")
_PLAY = re.compile(r"^play (?P<q>.+?)(?: on spotify)?$")
_NOT_SPOTIFY = re.compile(r"\b(?:on|in) (?:the |my )?(?:tv|television|plex|netflix|youtube|disney|hulu|max)\b|"
                          r"\b(?:episode|movie|season|show)\b")
_ALL_LIGHTS = re.compile(r"^(?:turn|switch|shut|put) (?P<a>on|off) (?:all )?(?:of )?(?:the |my )?lights$|"
                         r"^(?:turn|switch|shut) (?:all )?(?:the |my )?lights (?P<b>on|off)$|"
                         r"^(?:all )?(?:the )?lights (?P<c>on|off)$|^(?P<out>lights out)$")
_REPEAT = re.compile(r"^(?:repeat that|repeat|say that again|say again|what did you say|come again|pardon|"
                     r"sorry what|what was that)$")


def answer(text: str, last_reply: str = "", now: datetime | None = None) -> Answer | None:
    said = normalize(text)
    if not said or len(said.split()) > 12:
        return None
    now = now or datetime.now()

    if _STOP.match(said):
        return Answer(stop=True, kind="stop")
    if _REPEAT.match(said):
        return Answer(say=last_reply or "I haven't said anything yet, sir.", kind="repeat")
    if _TIME.match(said):
        return Answer(say=f"It's {_clock(now)}, sir.", kind="time")
    if _DATE.match(said):
        return Answer(say=f"It's {now.strftime('%A, %B')} {_ordinal(now.day)}, sir.", kind="date")
    if _CALENDAR.match(said):
        return _calendar(now)
    volume = _volume(said)
    if volume is not None:
        return volume
    music = _music(said)
    if music is not None:
        return music
    lights = _ALL_LIGHTS.match(said)
    if lights:
        action = lights.group("a") or lights.group("b") or lights.group("c") or "off"
        return _all_lights(action)
    timer = _timer(said)
    if timer is not None:
        return timer
    if _WEATHER.search(said) and not _LATER.search(said):
        return _weather()
    return None


def _volume(said: str) -> Answer | None:
    match = _VOLUME_SET.search(said)
    if match:
        raw = match.group("n") or match.group("m")
        value = number(raw) or 0
        value = value / 10 if value > 10 else value  # "volume 40" / "40 percent" means 4
        return Answer(sound="done", kind=f"volume {room_volume.set_level(round(value))}")
    if _VOLUME_MAX.match(said):
        return Answer(sound="done", kind=f"volume {room_volume.set_level(10)}")
    if _VOLUME_UP.match(said):
        return Answer(sound="done", kind=f"volume {room_volume.step(+1)}")
    if _VOLUME_DOWN.match(said):
        return Answer(sound="done", kind=f"volume {room_volume.step(-1)}")
    if _VOLUME_ASK.match(said):
        level = room_volume.level()
        return Answer(say=f"Volume {level}, sir." if level is not None else "I can't read the volume, sir.",
                      kind="volume")
    return None


def _timer(said: str) -> Answer | None:
    active = [t for t in timers.active_timers() if not t.alarming]
    cancel = _TIMER_CANCEL.match(said)
    if cancel:
        label = (cancel.group("label") or "").strip()
        label = "" if label in ("the", "my", "all", "this", "that") else label
        chosen = [t for t in active if label and label in timer_label(t.message).lower()] if label else active
        if not chosen:
            return Answer(say="There's no timer like that, sir." if label else "You don't have a timer running, sir.",
                          kind="timer")
        for t in chosen:
            timers.cancel_timer(t.id)
        what = f"the {label} timer" if label else ("your timer" if len(chosen) == 1 else f"all {len(chosen)} timers")
        return Answer(say=f"Cancelled {what}, sir.", kind="timer cancel")
    if _TIMER_LEFT.search(said) and "timer" in said or said in ("how long left", "how much longer", "time left"):
        if not active:
            return Answer(say="You don't have a timer running, sir.", kind="timer")
        parts = []
        for t in sorted(active, key=lambda t: t.remaining_seconds()):
            name = timer_label(t.message)
            state = " (paused)" if t.paused else ""
            parts.append(f"{say_duration(t.remaining_seconds())} left on {'the ' + name if name else 'your'} timer{state}")
        return Answer(say=("; ".join(parts) + ", sir.").capitalize(), kind="timer left")
    if _TIMER_SET.search(said) and not said.startswith(("cancel", "stop", "delete")):
        seconds = parse_duration(said)
        if seconds is None:
            return None  # "set a timer" with no length: the conversation asks how long
        label = _timer_name(said)
        timers.set_timer(seconds, label or "Timer up")
        what = say_duration(seconds).capitalize() + (f" for the {label}" if label else "")
        return Answer(say=f"{what}, starting now.", kind="timer set")
    return None


_NOT_NAMES = {"set", "a", "an", "start", "make", "put", "on", "the", "my", "new", "another", "for", "me", "up",
              "and", "half", "minute", "minutes", "hour", "hours", "second", "seconds", "min", "mins", "of"}


def _timer_name(said: str) -> str:
    """'pasta' from "set a pasta timer for 10 minutes" / "10 minute pasta timer"."""
    before = said.split("timer", 1)[0]
    before = _PART.sub(" ", before)
    words = [w for w in re.findall(r"[a-z']+", before) if w not in _NOT_NAMES and number(w) is None]
    after = re.search(r"timer (?:for|called|named) (?:the )?([a-z' ]+?)(?: for| in|$)", said)
    if not words and after and parse_duration(after.group(1)) is None:
        words = after.group(1).split()
    return " ".join(words[-3:])


def _weather() -> Answer | None:
    from app import status_hub
    try:
        weather = status_hub.weather_status()
    except Exception:  # noqa: BLE001 - the conversation can try instead
        return None
    temp = weather.get("temperature_f")
    if weather.get("error") or weather.get("online") is False or temp is None:
        return None
    place = (weather.get("location") or "").split(",")[0].strip()
    line = f"It's {round(temp)} and {weather.get('description') or 'fair'}"
    line += f" in {place}" if place else ""
    high, low = weather.get("high_f"), weather.get("low_f")
    if high is not None and low is not None:
        line += f", sir, with a high of {round(high)} and a low of {round(low)}."
    else:
        line += ", sir."
    _put_weather_on_screen()
    return Answer(say=line, kind="weather")


def _put_weather_on_screen() -> None:
    """The quick answer speaks; the full readout goes up on the iPad, phone
    and TV at the same time (in the background, so the reply isn't held)."""
    import threading

    def run() -> None:
        try:
            from brain import answer_cards
            answer_cards.show("weather_today", source="room voice")
        except Exception:  # noqa: BLE001 - the spoken answer already went out
            import logging
            logging.getLogger(__name__).exception("Weather card failed")

    threading.Thread(target=run, name="weather-card", daemon=True).start()


def _calendar(now: datetime) -> Answer:
    from speech import good_morning
    events = good_morning._today(now)  # noqa: SLF001 - the same calendar view
    if not events:
        return Answer(say="Nothing on your calendar today, sir.", kind="calendar")
    items = []
    for event in events[:5]:
        if event.get("all_day"):
            items.append(event["title"])
        else:
            at = datetime.fromtimestamp(float(event["start_at"])).strftime("%I:%M").lstrip("0")
            items.append(f"{event['title']} at {at}")
    listed = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    count = len(events)
    return Answer(say=f"You have {count} thing{'s' if count != 1 else ''} today, sir: {listed}.", kind="calendar")


def _spotify_reply(result: str, quiet_ok: bool) -> Answer:
    failed = result.startswith(("Spotify error", "Spotify isn't", "Spotify is not", "I couldn't", "No ",
                                "I need", "Nothing"))
    if failed or not quiet_ok:
        return Answer(say=result, sound="" if not failed else "error", kind="music")
    return Answer(sound="done", kind="music")


def _music(said: str) -> Answer | None:
    from integrations import spotify
    if not spotify._is_configured():  # noqa: SLF001 - no Spotify: "play"/"pause" are for the model (the TV, Plex)
        return None
    if _PAUSE.match(said):
        return _spotify_reply(spotify.pause_music(), quiet_ok=True)
    if _RESUME.match(said):
        return _spotify_reply(spotify.resume_music(), quiet_ok=True)
    if _NEXT.match(said):
        return _spotify_reply(spotify.skip_track(), quiet_ok=True)
    if _PREVIOUS.match(said):
        return _spotify_reply(spotify.previous_track(), quiet_ok=True)
    if _NOW_PLAYING.match(said):
        result = spotify.current_track()
        return Answer(say=result if result.startswith(("Nothing", "Spotify")) else f"It's {result}", kind="music")
    play = _PLAY.match(said)
    if play and not _NOT_SPOTIFY.search(said):
        query = play.group("q").strip()
        if query in ("music", "something", "some music", "my music"):
            return _spotify_reply(spotify.resume_music(), quiet_ok=True)
        return _spotify_reply(spotify.play_music(query), quiet_ok=False)  # say what's on, like a speaker
    return None


def _all_lights(action: str) -> Answer:
    """Every dashboard light (and lamp) on or off. Never the fan light on."""
    from app import status_hub
    entities = status_hub.home_entities().get("entities", [])
    targets = [e for e in entities if e.get("domain") == "light" or "lamp" in str(e.get("friendly_name", "")).lower()]
    if action == "on":
        targets = [e for e in targets if e["entity_id"] != "light.nicks_fan"]
    if not targets:
        return Answer(say="I can't see any lights, sir.", sound="error", kind="lights")
    failed = [e for e in targets if status_hub.home_control(e["entity_id"], action, settle=False).get("error")]
    if len(failed) == len(targets):
        return Answer(say="That didn't work, sir.", sound="error", kind=f"lights {action}")
    return Answer(sound="done", kind=f"lights {action} ({len(targets) - len(failed)}/{len(targets)})")
