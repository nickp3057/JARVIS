"""Wake word detection via openWakeWord.

Uses the pre-trained ``hey_jarvis`` model that ships with openwakeword.
That single model fires reliably on both "hey jarvis" and "hi jarvis"
(the acoustic difference is small enough that the network treats them
as variants).

On first run, openWakeWord auto-downloads its model files (a few MB each)
to its package directory; we trigger that here so the first launch isn't
mysteriously silent.
"""

from __future__ import annotations

import os
from typing import Any

from jarvis_logger import get_logger
from speech.audio import MicStream

log = get_logger(__name__)

WAKE_THRESHOLD = float(os.getenv("WAKE_THRESHOLD", "0.5"))
WAKE_MODEL_NAME = os.getenv("WAKE_MODEL", "hey_jarvis")


class WakeDetector:
    """Wraps an openWakeWord model and blocks until the wake word is heard."""

    def __init__(self) -> None:
        self._model: Any | None = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return

        # Trigger one-time model download if the user hasn't done it before.
        try:
            from openwakeword.utils import download_models

            download_models()
        except Exception:  # noqa: BLE001
            # download_models is a no-op when files already exist; only warn
            # if the import itself fails.
            log.debug("download_models() raised; assuming models are present.")

        from openwakeword.model import Model

        log.info("Loading openWakeWord model: %s", WAKE_MODEL_NAME)
        self._model = Model(
            wakeword_models=[WAKE_MODEL_NAME],
            inference_framework="onnx",
        )

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset()

    def wait_for_wake(self, stream: MicStream) -> str:
        """Block until the wake word is heard. Returns the firing model name."""
        self._ensure_loaded()
        assert self._model is not None
        self._model.reset()

        log.info("Listening for wake word...")
        while True:
            frame = stream.read(timeout=1.0)
            if frame is None:
                continue

            try:
                scores = self._model.predict(frame)
            except Exception:  # noqa: BLE001
                log.exception("openWakeWord predict() raised; skipping frame")
                continue

            for name, score in scores.items():
                if score >= WAKE_THRESHOLD:
                    log.info("Wake word fired: %s (score=%.2f)", name, score)
                    return name
