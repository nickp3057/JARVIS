"""JARVIS entry point.

Three launch modes (default = GUI):

* **GUI mode** (default) - the JARVIS-themed PyQt6 window with the
  animated orb, chat transcript, status panels, and a CHAT/VOICE toggle.
  Best daily-driver experience.

* **Voice mode** (``python -m app.main --voice``) - headless always-listening
  voice loop. Same wake word + STT + TTS pipeline, no UI.

* **Text mode** (``python -m app.main --text``) - the original keyboard
  REPL. Useful for development, debugging, or when the GUI isn't available.

If voice deps are missing or the agent itself can't initialize, we
degrade gracefully so JARVIS is never fully dark.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make sure top-level modules are importable regardless of cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from brain.runtime import JarvisRuntime  # noqa: E402
from jarvis_logger import get_logger  # noqa: E402
from speech.tts import speak  # noqa: E402

log = get_logger(__name__)

GREETING = "Pearson Stark JARVIS online. Standing by, sir."
EXIT_LINE = "Shutting down. See you, sir."
_EXIT_WORDS = {"exit", "quit", "bye", "goodbye"}


def _say(message: str) -> None:
    print(f"Jarvis: {message}")
    speak(message)


def _is_exit(text: str) -> bool:
    return text.strip().lower() in _EXIT_WORDS


def _streaming_enabled() -> bool:
    return os.getenv("STREAMING_TTS", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


# --- Text mode (REPL) -------------------------------------------------------

def _read_input() -> str | None:
    try:
        return input("You: ")
    except EOFError:
        print()
        return None
    except KeyboardInterrupt:
        print("\nJarvis: Caught interrupt. Say 'exit' when you're ready.")
        return ""


def run_text_mode(runtime: JarvisRuntime) -> int:
    print("=" * 60)
    print("  Pearson Stark JARVIS LAB Assistant - Text Mode")
    print(f"  Brain: {runtime.brain_label}")
    print("=" * 60)
    _say(GREETING)

    briefing = runtime.launch_briefing()
    if briefing:
        _say(briefing)

    if not runtime.brain_online:
        log.warning("Agent disabled; running in keyword-router fallback mode.")

    running = True
    while running:
        user_input = _read_input()
        if user_input is None:
            break
        if not user_input.strip():
            continue

        if _is_exit(user_input):
            _say(EXIT_LINE)
            break

        try:
            if _streaming_enabled() and runtime.brain_online:
                from speech.tts import speak_stream

                def _stream():
                    print("Jarvis: ", end="", flush=True)
                    for chunk in runtime.respond_streaming(user_input):
                        print(chunk, end="", flush=True)
                        yield chunk
                    print()

                speak_stream(_stream())
            else:
                reply = runtime.respond(user_input)
                _say(reply)
        except KeyboardInterrupt:
            print("\nJarvis: Interrupted. Say 'exit' when you're ready.")
        except Exception:  # noqa: BLE001
            log.exception("Unhandled error in main loop (input=%r)", user_input)
            _say("Something went wrong, sir. I logged it and I'm still here.")

    return 0


# --- Voice mode (headless) --------------------------------------------------

def run_voice_mode(runtime: JarvisRuntime) -> int:
    if not runtime.brain_online:
        print("Jarvis: Brain offline. Voice mode requires Claude. Run with --text for keyword fallback.")
        log.warning("Voice mode requested but agent is unavailable; refusing to start.")
        return 1

    try:
        from speech.voice_loop import run_voice_loop
    except ImportError as exc:
        print(
            "Jarvis: Voice dependencies missing. Run "
            "'pip install -r requirements.txt' or launch with '--text' to use the REPL."
        )
        log.error("Voice deps unavailable: %s", exc)
        return 1

    print("=" * 60)
    print("  Pearson Stark JARVIS LAB Assistant - Voice Mode (headless)")
    print("  Brain: Claude (online)")
    print("=" * 60)

    try:
        return run_voice_loop(runtime)
    except KeyboardInterrupt:
        _say(EXIT_LINE)
        return 0
    except Exception:  # noqa: BLE001
        log.exception("Voice loop crashed")
        print("Jarvis: The voice loop crashed. Check jarvis.log for details.")
        return 1


# --- GUI mode (default) -----------------------------------------------------

def run_gui_mode(runtime: JarvisRuntime) -> int:
    try:
        from ui.main_window import run_gui
    except ImportError as exc:
        print(
            "Jarvis: GUI dependencies missing. Install PyQt6 "
            "(in requirements.txt) or launch with '--text' / '--voice'."
        )
        log.error("GUI deps unavailable: %s", exc)
        return 1

    print("=" * 60)
    print("  Pearson Stark JARVIS LAB Assistant - GUI")
    print(f"  Brain: {runtime.brain_label}")
    print("=" * 60)

    try:
        return run_gui(runtime)
    except Exception:  # noqa: BLE001
        log.exception("GUI crashed")
        print("Jarvis: The GUI crashed. Check jarvis.log for details.")
        return 1


# --- Entry ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    log.info("JARVIS starting up. argv=%s", args)
    runtime = JarvisRuntime()

    if "--text" in args:
        return run_text_mode(runtime)
    if "--voice" in args:
        return run_voice_mode(runtime)
    return run_gui_mode(runtime)


if __name__ == "__main__":
    sys.exit(main())
