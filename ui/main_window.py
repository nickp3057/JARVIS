"""Main window for the JARVIS UI.

Layout (roughly)::

    +------------------------------------------------------------------+
    |  PEARSON STARK JARVIS                       [ CHAT | VOICE ]     |
    +-----------------------------+------------------------------------+
    |          ORB                |        Now Playing                 |
    |       (animated)            |        Weather                     |
    |                             |        Memory                      |
    |  STATE: LISTENING           |        Active Timers               |
    |                             |                                    |
    |  Chat transcript            |                                    |
    |  > You: ...                 |                                    |
    |  > Jarvis: ...              |                                    |
    |                             |                                    |
    |  [ Type a message ] [SEND]  |                                    |
    +-----------------------------+------------------------------------+

The mode toggle starts/stops the background voice runner. Either way,
the chat input always stays usable for typed commands.

The orb is driven by:
- ChatWidget.speak_started / speak_finished in chat mode
- VoiceRunner state signals in voice mode
"""

from __future__ import annotations

import os
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from brain.runtime import JarvisRuntime
from jarvis_logger import get_logger
from ui.chat_widget import ChatWidget
from ui.hud import HUDFrame
from ui.orb import JarvisOrb, OrbState
from ui.status_panels import StatusColumn
from ui.voice_runner import VoiceRunner

log = get_logger(__name__)


_GLOBAL_QSS = """
QMainWindow, QWidget#central {
    background-color: #03070d;
}
QLabel#header {
    color: rgba(0, 217, 255, 235);
    font-family: "Consolas", "Courier New", monospace;
    font-size: 17pt;
    font-weight: bold;
    letter-spacing: 6px;
}
QLabel#subheader {
    font-family: "Consolas", "Courier New", monospace;
    color: rgba(120, 185, 205, 160);
    font-size: 8pt;
    letter-spacing: 2px;
}
QLabel#sectionTitle {
    font-family: "Consolas", "Courier New", monospace;
    color: rgba(0, 217, 255, 225);
    font-size: 9pt;
    font-weight: bold;
    letter-spacing: 2px;
}
QLabel#telemetry {
    font-family: "Consolas", "Courier New", monospace;
    color: rgba(180, 230, 245, 190);
    font-size: 9pt;
}
QFrame#topBar, QFrame#cockpitPanel, QFrame#orbDeck, QFrame#chatDeck {
    background-color: rgba(6, 18, 26, 225);
    border: 1px solid rgba(0, 195, 235, 75);
    border-radius: 8px;
}
QFrame#modePill {
    background-color: rgba(8, 24, 32, 230);
    border: 1px solid rgba(0, 195, 235, 110);
    border-radius: 6px;
}
QPushButton#mode {
    background-color: rgba(0, 150, 190, 45);
    color: #C8ECF7;
    border: 1px solid rgba(0, 190, 230, 115);
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: bold;
    letter-spacing: 1px;
}
QPushButton#mode:checked {
    background-color: rgba(0, 205, 240, 185);
    color: #03141c;
    border-color: rgba(120, 230, 255, 220);
}
QPushButton#mode:hover { border-color: rgba(120, 230, 255, 200); }
"""


class JarvisMainWindow(QMainWindow):
    """The full JARVIS UI window."""

    launch_briefing_ready = pyqtSignal(str)

    def __init__(self, runtime: JarvisRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._voice_runner: VoiceRunner | None = None
        self._voice_active = False
        self._mode_lbl: QLabel | None = None
        self._telemetry_values: dict[str, QLabel] = {}
        self.launch_briefing_ready.connect(self._on_launch_briefing_ready)

        self.setWindowTitle("Pearson Stark JARVIS")
        self.resize(1440, 860)
        self.setMinimumSize(1160, 720)
        self.setStyleSheet(_GLOBAL_QSS)

        self._build()

    # --- Building ----------------------------------------------------------

    def _build(self) -> None:
        central = QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # --- Top command bar ----------------------------------------------
        top_bar = HUDFrame()
        top_bar.setObjectName("topBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 12, 16, 12)
        top_layout.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        title = QLabel("PEARSON STARK // JARVIS")
        title.setObjectName("header")
        title_col.addWidget(title)

        subtitle = QLabel("AMBIENT RUNTIME / VOICE / HOME / MEMORY")
        subtitle.setObjectName("subheader")
        title_col.addWidget(subtitle)
        top_layout.addLayout(title_col, 1)

        self._mode_lbl = QLabel("CHAT MODE")
        self._mode_lbl.setObjectName("telemetry")
        self._mode_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_pill = QFrame()
        mode_pill.setObjectName("modePill")
        mode_pill_layout = QHBoxLayout(mode_pill)
        mode_pill_layout.setContentsMargins(12, 6, 12, 6)
        mode_pill_layout.addWidget(self._mode_lbl)
        top_layout.addWidget(mode_pill)

        self._new_chat_btn = QPushButton("+ NEW CHAT")
        self._new_chat_btn.setObjectName("mode")
        self._new_chat_btn.clicked.connect(self._on_new_chat)
        top_layout.addWidget(self._new_chat_btn)

        self._chat_btn = QPushButton("CHAT")
        self._chat_btn.setObjectName("mode")
        self._chat_btn.setCheckable(True)
        self._chat_btn.setChecked(True)
        self._chat_btn.clicked.connect(self._activate_chat_mode)
        top_layout.addWidget(self._chat_btn)

        self._voice_btn = QPushButton("VOICE")
        self._voice_btn.setObjectName("mode")
        self._voice_btn.setCheckable(True)
        self._voice_btn.clicked.connect(self._activate_voice_mode)
        top_layout.addWidget(self._voice_btn)

        self._mute_btn = QPushButton("MUTE")
        self._mute_btn.setObjectName("mode")
        self._mute_btn.setCheckable(True)
        self._mute_btn.setChecked(False)
        self._mute_btn.clicked.connect(self._toggle_mute)
        top_layout.addWidget(self._mute_btn)
        root.addWidget(top_bar)

        # --- Cockpit body --------------------------------------------------
        body = QGridLayout()
        body.setHorizontalSpacing(12)
        body.setVerticalSpacing(12)
        body.setColumnStretch(0, 0)
        body.setColumnStretch(1, 1)
        body.setColumnStretch(2, 1)

        # Left intelligence rail.
        self._status = StatusColumn(self._runtime)
        self._status.setFixedWidth(360)
        self._status.session_selected.connect(self._on_session_selected)
        body.addWidget(self._status, 0, 0, 2, 1)

        # Center orb deck.
        orb_deck = HUDFrame()
        orb_deck.setObjectName("orbDeck")
        orb_layout = QVBoxLayout(orb_deck)
        orb_layout.setContentsMargins(18, 14, 18, 16)
        orb_layout.setSpacing(10)

        orb_title = QLabel("CORE PRESENCE")
        orb_title.setObjectName("sectionTitle")
        orb_layout.addWidget(orb_title)

        self._orb = JarvisOrb()
        self._orb.setMinimumHeight(420)
        orb_layout.addWidget(self._orb, 1)

        telemetry = QGridLayout()
        telemetry.setHorizontalSpacing(8)
        telemetry.setVerticalSpacing(8)
        telemetry.addWidget(self._telemetry_cell("WAKE", "STANDBY", key="wake"), 0, 0)
        telemetry.addWidget(self._telemetry_cell("BRAIN", self._runtime.brain_label, key="brain"), 0, 1)
        telemetry.addWidget(self._telemetry_cell("MODE", "CHAT", key="mode"), 1, 0)
        telemetry.addWidget(self._telemetry_cell("AUDIO", "READY", key="audio"), 1, 1)
        telemetry.addWidget(self._telemetry_cell("TASKS", "CLEAR", key="tasks"), 2, 0)
        telemetry.addWidget(self._telemetry_cell("EVENTS", "0", key="events"), 2, 1)
        telemetry.addWidget(self._telemetry_cell("HOME", "WAITING", key="home"), 3, 0)
        telemetry.addWidget(self._telemetry_cell("WEATHER", "WAITING", key="weather"), 3, 1)
        orb_layout.addLayout(telemetry)
        body.addWidget(orb_deck, 0, 1, 2, 1)

        # Right conversation deck.
        chat_deck = HUDFrame()
        chat_deck.setObjectName("chatDeck")
        chat_layout = QVBoxLayout(chat_deck)
        chat_layout.setContentsMargins(14, 12, 14, 14)
        chat_layout.setSpacing(8)

        chat_title = QLabel("CONVERSATION")
        chat_title.setObjectName("sectionTitle")
        chat_layout.addWidget(chat_title)

        self._chat = ChatWidget(self._runtime)
        self._chat.user_sent.connect(self._on_user_sent)
        self._chat.jarvis_replied.connect(self._on_jarvis_replied)
        self._chat.speak_started.connect(self._on_speak_started)
        self._chat.speak_finished.connect(self._on_speak_finished)
        chat_layout.addWidget(self._chat, 1)
        body.addWidget(chat_deck, 0, 2, 2, 1)

        root.addLayout(body, 1)

        # HUD refresh timer.
        self._hud_timer = QTimer(self)
        self._hud_timer.setInterval(1_000)
        self._hud_timer.timeout.connect(self._refresh_hud)
        self._hud_timer.start()
        self._refresh_hud()

        # Movie-style boot sequence, or a static greeting if disabled.
        self._booted = False
        boot_enabled = os.getenv("BOOT_SEQUENCE", "true").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if boot_enabled:
            QTimer.singleShot(60, self._run_boot_sequence)
        else:
            self._chat.append_system("JARVIS UI online. Click VOICE to enable always-listening.")
            if not self._runtime.brain_online:
                self._chat.append_system("Brain offline - check ANTHROPIC_API_KEY in .env.")
                self._orb.set_state(OrbState.ALERT)
            else:
                self._chat.speak_line("Pearson Stark JARVIS online. Standing by, sir.")
                self._start_launch_briefing()
            self._booted = True

    def _telemetry_cell(self, label: str, value: str, *, key: str) -> QFrame:
        cell = QFrame()
        cell.setObjectName("cockpitPanel")
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        label_widget = QLabel(label)
        label_widget.setObjectName("subheader")
        layout.addWidget(label_widget)

        value_widget = QLabel(value)
        value_widget.setObjectName("telemetry")
        value_widget.setWordWrap(True)
        layout.addWidget(value_widget)
        self._telemetry_values[key] = value_widget
        return cell

    def _set_telemetry(self, key: str, value: str) -> None:
        widget = self._telemetry_values.get(key)
        if widget is not None:
            widget.setText(value)

    def _refresh_hud(self) -> None:
        status = self._runtime.status_snapshot()
        orb_state = self._orb.state().value.upper() if hasattr(self, "_orb") else "STANDBY"
        self._set_telemetry("wake", orb_state)
        self._set_telemetry("brain", status.brain_label)
        self._set_telemetry("mode", "VOICE" if self._voice_active else "CHAT")
        self._set_telemetry("audio", "MUTED" if self._mute_btn.isChecked() else "READY")
        self._set_telemetry("tasks", f"{status.active_task_count} ACTIVE" if status.active_task_count else "CLEAR")
        self._set_telemetry("events", str(status.event_count))
        home = self._runtime.home_snapshot()
        if home.online:
            lights = home.domains.get("light")
            home_text = f"{lights.on_count} LIGHTS ON" if lights is not None else f"{home.entity_count} ENTITIES"
        else:
            home_text = "OFFLINE"
        self._set_telemetry("home", home_text)
        weather = self._runtime.weather_snapshot()
        if weather.online and weather.temperature_f is not None:
            weather_text = f"{weather.temperature_f}F {weather.description.upper()}"
        else:
            weather_text = "UNAVAILABLE"
        self._set_telemetry("weather", weather_text)

        # In chat mode, speak any due reminders / proactive alerts (calendar,
        # timers). In voice mode the voice runner already handles these.
        if getattr(self, "_booted", False) and not self._voice_active:
            for item in self._runtime.pop_due_reminders():
                self._chat.speak_line(f"Reminder, sir. {item.message}.")

    # --- Boot sequence -----------------------------------------------------

    def _run_boot_sequence(self) -> None:
        if self._booted:
            return
        self._booted = True
        try:
            from speech.sounds import play_boot
            play_boot()
        except Exception:  # noqa: BLE001
            log.debug("Boot sound unavailable")

        self._orb.set_state(OrbState.LISTENING)
        self._chat.append_system("Initializing JARVIS core...")

        steps = [
            ("brain", self._runtime.brain_label),
            ("audio", "READY"),
            ("home", "LINKED"),
            ("weather", "SYNCED"),
            ("events", "MONITORING"),
            ("tasks", "CLEAR"),
        ]
        for i, (key, value) in enumerate(steps):
            QTimer.singleShot(140 + i * 170, lambda k=key: self._set_telemetry(k, "INIT..."))
            QTimer.singleShot(240 + i * 170, lambda k=key, v=value: self._boot_step(k, v))
        QTimer.singleShot(240 + len(steps) * 170 + 240, self._finish_boot)

    def _boot_step(self, key: str, value: str) -> None:
        self._set_telemetry(key, value)
        try:
            from speech.sounds import play_blip
            play_blip()
        except Exception:  # noqa: BLE001
            pass

    def _finish_boot(self) -> None:
        online = self._runtime.brain_online
        self._orb.set_state(OrbState.IDLE if online else OrbState.ALERT)
        self._chat.append_system(
            "All systems nominal." if online
            else "Brain offline - check ANTHROPIC_API_KEY in .env."
        )
        self._refresh_hud()
        if not online:
            return
        # Speak via the chat widget so orb SPEAKING/IDLE stays in sync.
        self._chat.speak_line("Pearson Stark JARVIS online. All systems nominal, sir.")
        self._start_launch_briefing()

    def _start_launch_briefing(self) -> None:
        """Compose optional network-backed briefing details off the UI thread."""
        def _compose() -> None:
            briefing = self._runtime.launch_briefing()
            if briefing:
                self.launch_briefing_ready.emit(briefing)

        threading.Thread(target=_compose, name="jarvis-launch-briefing", daemon=True).start()

    def _on_launch_briefing_ready(self, briefing: str) -> None:
        self._chat.speak_line(briefing)

    # --- Mode handling -----------------------------------------------------

    def _activate_chat_mode(self) -> None:
        self._chat_btn.setChecked(True)
        self._voice_btn.setChecked(False)
        if self._mode_lbl is not None:
            self._mode_lbl.setText("CHAT MODE")
        self._runtime.record_event(kind="mode", source="ui", title="Chat mode", detail="Voice runner stopping")
        self._refresh_hud()
        if self._voice_active:
            self._stop_voice_runner()

    def _activate_voice_mode(self) -> None:
        self._chat_btn.setChecked(False)
        self._voice_btn.setChecked(True)
        if self._mode_lbl is not None:
            self._mode_lbl.setText("VOICE MODE")
        self._runtime.record_event(kind="mode", source="ui", title="Voice mode", detail="Always-listening enabled")
        self._refresh_hud()
        if not self._voice_active:
            self._start_voice_runner()

    def _start_voice_runner(self) -> None:
        if self._voice_runner is not None:
            return
        runner = VoiceRunner(self._runtime, parent=self)
        runner.state_idle.connect(lambda: self._orb.set_state(OrbState.IDLE))
        runner.state_listening.connect(lambda: self._orb.set_state(OrbState.LISTENING))
        runner.state_thinking.connect(lambda: self._orb.set_state(OrbState.THINKING))
        runner.state_speaking.connect(lambda: self._orb.set_state(OrbState.SPEAKING))
        runner.rms_level.connect(self._orb.set_rms)
        runner.user_heard.connect(self._chat.append_user)
        runner.jarvis_replied.connect(self._chat.append_jarvis)
        runner.jarvis_replied.connect(lambda _: self._status.sessions.notify_new_session())
        runner.notice.connect(self._chat.append_system)
        runner.stopped.connect(self._on_voice_stopped)
        self._voice_runner = runner
        self._voice_active = True
        self._chat.append_system("Voice mode ON. Say 'hey jarvis' to begin.")
        self._refresh_hud()
        runner.start()

    def _stop_voice_runner(self) -> None:
        if self._voice_runner is None:
            return
        self._voice_runner.request_stop()
        self._chat.append_system("Voice mode stopping...")

    def _on_voice_stopped(self) -> None:
        if self._voice_runner is not None:
            self._voice_runner.deleteLater()
            self._voice_runner = None
        self._voice_active = False
        self._chat.append_system("Voice mode OFF.")
        self._orb.set_state(OrbState.IDLE)
        if self._mode_lbl is not None:
            self._mode_lbl.setText("CHAT MODE")
        if not self._voice_btn.isChecked():
            self._chat_btn.setChecked(True)

    def _on_new_chat(self) -> None:
        """Start a fresh session, clear transcript, and refresh the sidebar."""
        self._runtime.start_session()
        self._chat.load_session([])
        self._chat.append_system("New session started.")
        self._status.sessions.notify_new_session()

    def _toggle_mute(self) -> None:
        muted = self._mute_btn.isChecked()
        self._chat.set_muted(muted)
        if self._voice_runner is not None:
            self._voice_runner.set_muted(muted)
        label = "MUTED" if muted else "MUTE"
        self._mute_btn.setText(label)
        self._runtime.record_event(
            kind="audio",
            source="ui",
            title="Audio muted" if muted else "Audio unmuted",
        )
        self._refresh_hud()

    def _on_session_selected(self, session_id: str) -> None:
        """Switch agent context to a past session and repopulate the transcript."""
        turns = self._runtime.switch_session(session_id)
        self._chat.load_session(turns)
        self._chat.append_system(f"Resumed session {session_id[:8]}...")

    # --- Chat-mode orb hooks (driven by speak start/finish) --------------

    def _on_user_sent(self, _text: str) -> None:
        if not self._voice_active:
            self._orb.set_state(OrbState.THINKING)
        self._refresh_hud()

    def _on_jarvis_replied(self, _text: str) -> None:
        # Orb state shifts to SPEAKING when TTS actually starts.
        self._status.sessions.notify_new_session()

    def _on_speak_started(self) -> None:
        if not self._voice_active:
            self._orb.set_state(OrbState.SPEAKING)
        self._refresh_hud()

    def _on_speak_finished(self) -> None:
        if not self._voice_active:
            self._orb.set_state(OrbState.IDLE)
        self._refresh_hud()

    # --- Lifecycle ---------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._voice_runner is not None:
            self._voice_runner.request_stop()
            self._voice_runner.wait(2000)
        self._runtime.stop_monitors()
        super().closeEvent(event)


def run_gui(runtime: JarvisRuntime) -> int:
    """Launch the GUI. Returns the QApplication exit code."""
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    win = JarvisMainWindow(runtime)
    win.show()
    return app.exec()
