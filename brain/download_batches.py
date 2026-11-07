"""One "finished downloading" line per show, not one per episode.

The download monitor (``JarvisRuntime._download_monitor``) used to announce
every torrent that hit 100%, so adding a season meant "Silo, season 1,
episode 3 has finished downloading, sir" over and over. Episodes now wait
here and are announced together once none of that show's other torrents is
still downloading ("4 episodes of Silo have finished downloading, sir."), or
after ``DOWNLOAD_BATCH_MAX_WAIT_S`` (30 min) so one slow or stuck episode
can't hold the rest back forever. Movies and whole-season packs are a single
torrent already and are announced at once, as before.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from integrations.qbittorrent import friendly_name


def _episode_show(release: str) -> str | None:
    """"Silo" for a single-episode release, None for a movie or season pack."""
    spoken = friendly_name(release)
    if ", episode " not in spoken:
        return None
    return spoken.split(", season ")[0].split(", episode ")[0].strip() or None


def _max_wait() -> float:
    try:
        return float(os.getenv("DOWNLOAD_BATCH_MAX_WAIT_S", "1800"))
    except ValueError:
        return 1800.0


@dataclass
class _Batch:
    show: str
    episodes: list[str] = field(default_factory=list)
    since: float = 0.0


class EpisodeBatcher:
    def __init__(self) -> None:
        self._batches: dict[str, _Batch] = {}

    def completed(self, release: str, now: float | None = None) -> str | None:
        """A torrent finished. The line to say now, or None if it's an
        episode held for its show's batch."""
        show = _episode_show(release)
        if show is None:
            return f"{friendly_name(release)} has finished downloading, sir."
        batch = self._batches.setdefault(show.lower(), _Batch(show, since=time.time() if now is None else now))
        batch.episodes.append(friendly_name(release))
        return None

    def flush(self, torrents: Iterable[dict[str, Any]], now: float | None = None) -> list[str]:
        """Lines for shows with nothing left downloading (or waited long enough)."""
        now = time.time() if now is None else now
        busy = {
            show.lower()
            for item in torrents
            if float(item.get("progress", 0) or 0) < 1
            and (show := _episode_show(str(item.get("name", "")))) is not None
        }
        lines = []
        for key in list(self._batches):
            batch = self._batches[key]
            if key in busy and now - batch.since < _max_wait():
                continue
            del self._batches[key]
            if len(batch.episodes) == 1:
                lines.append(f"{batch.episodes[0]} has finished downloading, sir.")
            else:
                lines.append(f"{len(batch.episodes)} episodes of {batch.show} have finished downloading, sir.")
        return lines
