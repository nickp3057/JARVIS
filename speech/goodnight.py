"""The bedtime routine: "Jarvis, goodnight."

Built to be as quick as "Hey Google, goodnight", so no model is involved:

    Nick:   Jarvis, goodnight.
    JARVIS: Goodnight, sir. Your alarm's set for 7:00. Is that fine?
    Nick:   Yes.                      (or "make it 6:30" / "no alarm" / silence)
    JARVIS: Sleep well, sir.          (the Goodnight scene runs as he says it)

Answers it understands: yes (and silence: he's going to sleep), a new time
("6:30", "make it eight", "seven thirty"), no alarm ("skip it", "sleep in"),
or a bare "no", which asks "What time, sir?" once. A changed time or a
skipped morning is a one-day change; the weekly schedule stays as it was.

The voice front end passes ``say`` / ``listen`` (``speech/room_voice.py``);
lines are fixed templates, so their audio caches after the first night.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import os
import re
from typing import Callable

from brain import wake_alarm

_TRIGGERS = {"goodnight", "good night", "night night", "nighty night", "going to bed",
             "i'm going to bed", "im going to bed", "bedtime", "time for bed"}
_YES = {"yes", "yeah", "yep", "yup", "sure", "fine", "perfect", "good", "ok", "okay", "correct",
        "please", "do it", "sounds good", "that's fine", "thats fine", "that's good", "that works",
        "yes please", "it's fine", "its fine", "great", "right", "mhm", "uh huh"}
_NO = {"no", "nope", "nah", "not really", "no thanks"}
_SKIP = re.compile(r"\b(no alarm|skip|turn it off|cancel|don't wake|dont wake|sleep in|no wake|off tomorrow)\b")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_MINUTES = {"o'clock": 0, "oclock": 0, "fifteen": 15, "thirty": 30, "forty five": 45, "forty-five": 45,
            "half": 30}
_DIGITS = re.compile(r"\b(\d{1,2})(?:[:.](\d{2})|\s+(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?![\d:])")
_SPOKEN = re.compile(r"\b(" + "|".join(_WORDS) + r")(?:\s+(o'?clock|fifteen|thirty|forty[ -]five))?\b")
_HALF_PAST = re.compile(r"\bhalf (?:past )?(" + "|".join(_WORDS) + r"|\d{1,2})\b")


def _address() -> str:
    return (os.getenv("ADDRESS") or "sir").strip() or "sir"


def _clean(text: str | None) -> str:
    text = re.sub(r"[^\w\s:'.-]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text.replace("jarvis", " ")).strip(" .")


# Words that can sit around "good night" without changing what it means:
# politeness, "I said", and the ways the mic mishears "Jarvis".
_FILLER = {"hey", "hi", "hay", "oh", "ok", "okay", "alright", "all", "right", "well", "so", "and",
           "thanks", "thank", "you", "i", "said", "say", "sir", "buddy", "then", "anyway", "now",
           "jervis", "jarvis's", "two", "to", "too", "drivers", "driver", "travis", "jarvas", "service", "jervais", "j"}


def is_goodnight(text: str) -> bool:
    """"Good night", and also "Hey Jarvis, good night", "Thank you, Jarvis.
    Good night", "I said good night" or "Good night. Hey drivers, good night".
    Anything else in the sentence ("turn off the lights, good night") goes to
    the conversation instead."""
    cleaned = _clean(text)
    if cleaned in _TRIGGERS:
        return True
    rest = re.sub(r"[.:'-]", " ", cleaned)
    found = False
    for trigger in sorted(_TRIGGERS, key=len, reverse=True):
        pattern = r"\b" + re.escape(trigger.replace("'", " ")) + r"\b"
        rest, hits = re.subn(pattern, " ", rest)
        found = found or hits > 0
    return found and all(word in _FILLER for word in rest.split())


def parse_spoken_time(text: str) -> str | None:
    """"6:30", "6.30", "six thirty", "make it 8", "half past six", "7 pm" -> "HH:MM".
    No am/pm means morning (it's an alarm), except 12, which is noon."""
    text = _clean(text)
    hour = minute = None
    pm = am = False
    m = _DIGITS.search(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or m.group(3) or 0)
        suffix = (m.group(4) or "").replace(".", "")
        pm, am = suffix == "pm", suffix == "am"
    else:
        half = _HALF_PAST.search(text)
        spoken = _SPOKEN.search(text)
        if half:
            word = half.group(1)
            hour, minute = (int(word) if word.isdigit() else _WORDS[word]), 30
        elif spoken:
            hour = _WORDS[spoken.group(1)]
            minute = _MINUTES.get((spoken.group(2) or "o'clock").replace("-", " "), 0)
        pm = bool(re.search(r"\b(pm|p m|in the evening|tonight)\b", text))
    if hour is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if pm and hour < 12:
        hour += 12
    elif am and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def understand(reply: str | None) -> tuple[str, str | None]:
    """("yes" | "change" | "skip" | "no" | "silent" | "unclear", new time)."""
    text = _clean(reply)
    if not text:
        return "silent", None
    if _SKIP.search(text):
        return "skip", None
    hhmm = parse_spoken_time(text)
    if hhmm:
        return "change", hhmm
    words = text.replace(",", " ")
    if words in _YES or any(words.startswith(y + " ") for y in ("yes", "yeah", "yep", "sure")):
        return "yes", None
    if words in _NO or words.startswith("no "):
        return "no", None
    return "unclear", None


def _say_time(at: datetime) -> str:
    return at.strftime("%-I:%M")


def _morning(now: datetime) -> tuple[str, datetime | None]:
    """The date of the coming morning and its alarm (None: no alarm set)."""
    at = wake_alarm.next_time(wake_alarm.load(), now)
    if at is not None and at - now <= timedelta(hours=20):
        return at.date().isoformat(), at
    morning = now if now.hour < 5 else now + timedelta(days=1)  # after midnight, "tomorrow" is today
    return morning.date().isoformat(), None


def run(say: Callable[[str], None], listen: Callable[[], str | None],
        lights_out: Callable[[], None], now: datetime | None = None) -> str:
    """The whole routine; returns what happened to the alarm, for the log."""
    now = now or datetime.now()
    sir = _address()
    day, at = _morning(now)
    if at is not None:
        say(f"Goodnight, {sir}. Your alarm's set for {_say_time(at)}. Is that fine?")
    else:
        say(f"Goodnight, {sir}. There's no alarm for the morning. Want one?")
    kind, hhmm = understand(listen())

    if kind == "no" or (kind == "yes" and at is None):
        if kind == "no" and at is None:
            kind = "silent"  # no alarm, and he doesn't want one
        else:
            say(f"What time, {sir}?")
            kind, hhmm = understand(listen())
            if kind != "change" and kind != "skip":
                kind = "silent"

    if kind == "change" and hhmm:
        wake_alarm.set_alarm(hhmm, day, now=now)  # this morning only; the week stays as it was
        closing, outcome = f"{_say_time(datetime.strptime(hhmm, '%H:%M'))}, then. Sleep well, {sir}.", f"moved to {hhmm}"
    elif kind == "skip" and at is not None:
        wake_alarm.skip(day, now=now)
        closing, outcome = f"No alarm, then. Sleep well, {sir}.", "skipped"
    else:  # yes, silence, or something unclear: keep it as it is
        closing, outcome = f"Sleep well, {sir}.", "kept" if at is not None else "none"
    lights_out()
    say(closing)
    return outcome
