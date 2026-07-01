"""Chat transcript + input widget for the JARVIS UI.

User types into the input box -> message goes to the agent on a worker
thread -> reply comes back via a signal and gets appended to the transcript.
A SECOND worker thread runs the TTS playback so the UI thread (and the
orb animation) keep running smoothly while JARVIS is talking.

The widget emits ``user_sent``, ``jarvis_replied``, and ``speak_started`` /
``speak_finished`` signals so the main window can swap orb states.
"""

from __future__ import annotations

from html import escape

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from brain.runtime import JarvisRuntime
from speech.tts import speak


# --- Workers ----------------------------------------------------------------

class _AgentWorker(QObject):
    """Runs ``runtime.respond(text)`` off the UI thread."""

    finished = pyqtSignal(str, str)  # (user_text, reply)
    failed = pyqtSignal(str, str)    # (user_text, error)

    def __init__(self, runtime: JarvisRuntime, text: str) -> None:
        super().__init__()
        self._runtime = runtime
        self._text = text

    def run(self) -> None:
        try:
            reply = self._runtime.respond(self._text)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self._text, str(exc))
            return
        self.finished.emit(self._text, reply)


class _SpeakWorker(QObject):
    """Plays TTS off the UI thread."""

    finished = pyqtSignal()

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def run(self) -> None:
        try:
            speak(self._text)
        except Exception:  # noqa: BLE001
            pass
        self.finished.emit()


# --- Widget -----------------------------------------------------------------

class ChatWidget(QWidget):
    """Chat transcript + input field. Sends to the agent and shows replies."""

    user_sent = pyqtSignal(str)
    jarvis_replied = pyqtSignal(str)
    speak_started = pyqtSignal()
    speak_finished = pyqtSignal()

    def __init__(
        self,
        runtime: JarvisRuntime,
        speak_replies: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._speak_replies = speak_replies
        self._muted = False
        self._agent_thread: QThread | None = None
        self._agent_worker: _AgentWorker | None = None
        self._speak_thread: QThread | None = None
        self._speak_worker: _SpeakWorker | None = None

        self._build()

    # --- Public --------------------------------------------------------

    def append_user(self, text: str) -> None:
        self._append_bubble(text, side="right")

    def append_jarvis(self, text: str) -> None:
        self._append_bubble(text, side="left")

    def speak_line(self, text: str) -> None:
        """Append a JARVIS line and speak it (respecting mute), driving the orb."""
        text = (text or "").strip()
        if not text:
            return
        self.append_jarvis(text)
        if self._speak_replies and not self._muted:
            self._dispatch_to_speak(text)

    def append_system(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        cursor = self._transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(
            f'<p align="center" style="color:#466270; font-size:9pt; margin:6px 0;">'
            f'{self._escape(text)}</p>'
        )
        self._transcript.setTextCursor(cursor)
        self._transcript.ensureCursorVisible()

    def set_input_enabled(self, enabled: bool) -> None:
        self._input.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)
        if enabled:
            self._input.setFocus()

    def set_muted(self, muted: bool) -> None:
        """Enable or disable TTS playback."""
        self._muted = muted

    def load_session(self, turns: list[dict]) -> None:
        """Clear the transcript and repopulate from a list of {role, content} dicts."""
        self._transcript.clear()
        for msg in turns:
            role = msg.get("role", "")
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                self._append_bubble(content, side="right")
            elif role == "assistant":
                self._append_bubble(content, side="left")

    # --- Building ------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._transcript = QTextEdit(self)
        self._transcript.setReadOnly(True)
        self._transcript.setStyleSheet(
            "QTextEdit {"
            "  background-color: rgba(4, 12, 18, 240);"
            "  color: #CDEEF8;"
            "  border: 1px solid rgba(0, 170, 210, 50);"
            "  border-radius: 8px;"
            "  padding: 6px;"
            "  font-family: 'Segoe UI', Arial, sans-serif;"
            "  font-size: 11pt;"
            "}"
        )
        layout.addWidget(self._transcript, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self._input = QLineEdit(self)
        self._input.setPlaceholderText("Type a message to JARVIS...")
        self._input.setStyleSheet(
            "QLineEdit {"
            "  background-color: rgba(6, 18, 24, 220);"
            "  color: #CDEEF8;"
            "  border: 1px solid rgba(0, 170, 210, 90);"
            "  border-radius: 6px;"
            "  padding: 8px;"
            "  font-size: 11pt;"
            "}"
            "QLineEdit:focus { border-color: rgba(0, 212, 245, 200); }"
        )
        self._input.returnPressed.connect(self._on_send)
        input_row.addWidget(self._input, 1)

        self._send_btn = QPushButton("SEND", self)
        self._send_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(0, 150, 190, 60);"
            "  color: #DFF7FF;"
            "  border: 1px solid rgba(0, 190, 230, 160);"
            "  border-radius: 6px;"
            "  padding: 8px 18px;"
            "  font-weight: bold;"
            "  letter-spacing: 2px;"
            "}"
            "QPushButton:hover { background-color: rgba(0, 180, 220, 110); }"
            "QPushButton:disabled { color: #5A8090; border-color: rgba(40, 120, 140, 80); }"
        )
        self._send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self._send_btn)

        layout.addLayout(input_row)

    # --- Behavior ------------------------------------------------------

    def _append_bubble(self, text: str, *, side: str) -> None:
        """Append a chat bubble: user on the right, JARVIS on the left."""
        text = text.strip()
        if not text:
            return

        escaped = self._escape(text)
        cursor = self._transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if side == "right":
            # User bubble: right-aligned, dark red
            html = (
                '<table width="100%" cellpadding="0" cellspacing="0">'
                '<tr>'
                '<td width="18%"></td>'
                '<td align="right" style="padding: 3px 4px;">'
                '<table cellpadding="0" cellspacing="0"><tr>'
                '<td bgcolor="#0c4a5e" style="color:#FFFFFF; padding:9px 13px;'
                ' border-radius:14px 14px 3px 14px; font-size:11pt;">'
                f'{escaped}'
                '</td></tr></table>'
                '</td>'
                '</tr></table>'
            )
        else:
            # JARVIS bubble: left-aligned, near-black with warm tint
            html = (
                '<table width="100%" cellpadding="0" cellspacing="0">'
                '<tr>'
                '<td align="left" style="padding: 3px 4px;">'
                '<table cellpadding="0" cellspacing="0"><tr>'
                '<td bgcolor="#03141c" style="color:#BFE6F2; padding:9px 13px;'
                ' border-radius:14px 14px 14px 3px; font-size:11pt;">'
                f'{escaped}'
                '</td></tr></table>'
                '</td>'
                '<td width="18%"></td>'
                '</tr></table>'
            )

        cursor.insertHtml(html)
        # Spacer between bubbles
        cursor.insertHtml('<p style="margin:2px 0;"> </p>')
        self._transcript.setTextCursor(cursor)
        self._transcript.ensureCursorVisible()

    @staticmethod
    def _escape(text: str) -> str:
        return escape(text, quote=True).replace("\n", "<br>")

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.append_user(text)
        self.user_sent.emit(text)
        self.set_input_enabled(False)
        self._dispatch_to_agent(text)

    def _dispatch_to_agent(self, text: str) -> None:
        self._agent_thread = QThread(self)
        self._agent_worker = _AgentWorker(self._runtime, text)
        self._agent_worker.moveToThread(self._agent_thread)
        self._agent_thread.started.connect(self._agent_worker.run)
        self._agent_worker.finished.connect(self._on_agent_finished)
        self._agent_worker.failed.connect(self._on_agent_failed)
        self._agent_worker.finished.connect(self._agent_thread.quit)
        self._agent_worker.failed.connect(self._agent_thread.quit)
        self._agent_thread.finished.connect(self._cleanup_agent_thread)
        self._agent_thread.start()

    def _on_agent_finished(self, _user_text: str, reply: str) -> None:
        self.append_jarvis(reply)
        self.jarvis_replied.emit(reply)
        # Re-enable input immediately; don't make the user wait for TTS.
        self.set_input_enabled(True)
        if self._speak_replies and not self._muted and reply.strip():
            self._dispatch_to_speak(reply)
        else:
            # No TTS will fire; tell the main window so it can reset the orb.
            self.speak_finished.emit()

    def _on_agent_failed(self, _user_text: str, error: str) -> None:
        self.append_system(f"Agent error: {error}")
        self.set_input_enabled(True)
        self.speak_finished.emit()

    def _cleanup_agent_thread(self) -> None:
        if self._agent_worker is not None:
            self._agent_worker.deleteLater()
            self._agent_worker = None
        if self._agent_thread is not None:
            self._agent_thread.deleteLater()
            self._agent_thread = None

    # --- Speak (off-thread, doesn't block the UI / orb) ----------------

    def _dispatch_to_speak(self, text: str) -> None:
        # If something else was already speaking (rare), just let it finish
        # and queue up by serial replacement: cancel old worker first.
        if self._speak_thread is not None:
            try:
                self._speak_thread.quit()
                self._speak_thread.wait(200)
            except Exception:  # noqa: BLE001
                pass
            self._cleanup_speak_thread()

        self._speak_thread = QThread(self)
        self._speak_worker = _SpeakWorker(text)
        self._speak_worker.moveToThread(self._speak_thread)
        self._speak_thread.started.connect(self._speak_worker.run)
        self._speak_worker.finished.connect(self._on_speak_finished)
        self._speak_worker.finished.connect(self._speak_thread.quit)
        self._speak_thread.finished.connect(self._cleanup_speak_thread)
        self.speak_started.emit()
        self._speak_thread.start()

    def _on_speak_finished(self) -> None:
        self.speak_finished.emit()
        # Input was already re-enabled when the agent replied; nothing to do.

    def _cleanup_speak_thread(self) -> None:
        if self._speak_worker is not None:
            self._speak_worker.deleteLater()
            self._speak_worker = None
        if self._speak_thread is not None:
            self._speak_thread.deleteLater()
            self._speak_thread = None
