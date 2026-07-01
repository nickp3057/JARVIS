"""Side-panel widgets for the JARVIS UI.

Panels:
- SessionsPanel   - scrollable clickable list of past sessions
- MemoryPanel     - scrollable view of all memory facts
- TimersPanel     - interactive timer cards with pause/cancel/dismiss + alarm
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from brain.history import list_sessions
from brain.runtime import JarvisRuntime
from brain.timers import (
    Timer,
    cancel_timer,
    dismiss_timer,
    pause_timer,
    resume_timer,
)
from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_FILE = _PROJECT_ROOT / "memory" / "profile.json"


# ---------------------------------------------------------------------------
# Shared styling
# ---------------------------------------------------------------------------

_CARD_QSS = (
    "QFrame#card {"
    "  background-color: rgba(4, 14, 20, 220);"
    "  border: 1px solid rgba(0, 160, 200, 70);"
    "  border-radius: 8px;"
    "}"
    "QLabel#title {"
    "  color: rgba(0, 212, 245, 230);"
    "  font-weight: bold;"
    "  letter-spacing: 2px;"
    "  font-size: 9pt;"
    "}"
)

_SESSION_BTN_QSS = (
    "QPushButton {"
    "  background-color: rgba(8, 22, 30, 200);"
    "  color: #BFE6F2;"
    "  border: 1px solid rgba(0, 140, 180, 60);"
    "  border-radius: 6px;"
    "  padding: 7px 10px;"
    "  text-align: left;"
    "  font-size: 9pt;"
    "}"
    "QPushButton:hover {"
    "  background-color: rgba(10, 50, 64, 200);"
    "  border-color: rgba(0, 190, 230, 160);"
    "}"
)

_SMALL_BTN_BASE = (
    "QPushButton {"
    "  border-radius: 4px;"
    "  padding: 3px 8px;"
    "  font-size: 8pt;"
    "  font-weight: bold;"
    "  letter-spacing: 1px;"
    "}"
)

_CANCEL_BTN_QSS = _SMALL_BTN_BASE + (
    "QPushButton { background: rgba(10, 70, 90, 180); color: #DFF7FF;"
    "  border: 1px solid rgba(0, 170, 210, 100); }"
    "QPushButton:hover { background: rgba(14, 92, 116, 220); }"
)

_PAUSE_BTN_QSS = _SMALL_BTN_BASE + (
    "QPushButton { background: rgba(8, 26, 34, 180); color: #BFE6F2;"
    "  border: 1px solid rgba(0, 140, 180, 80); }"
    "QPushButton:hover { background: rgba(10, 52, 66, 220); }"
)

_SCROLLBAR_QSS = (
    "QScrollBar:vertical {"
    "  background: rgba(6, 16, 22, 180);"
    "  width: 6px; border-radius: 3px;"
    "}"
    "QScrollBar::handle:vertical {"
    "  background: rgba(0, 170, 210, 120);"
    "  border-radius: 3px;"
    "}"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
)

_DISMISS_BTN_QSS = _SMALL_BTN_BASE + (
    "QPushButton { background: rgba(200, 40, 20, 200); color: #EAFCFF;"
    "  border: 1px solid rgba(255, 90, 60, 180); }"
    "QPushButton:hover { background: rgba(240, 60, 30, 240); }"
)

_ALARM_CARD_QSS = (
    "QFrame#alarm_card {"
    "  background-color: rgba(10, 48, 60, 200);"
    "  border: 1px solid rgba(255, 70, 40, 180);"
    "  border-radius: 6px;"
    "}"
)


# ---------------------------------------------------------------------------
# Runtime panel
# ---------------------------------------------------------------------------

class RuntimePanel(QFrame):
    """Live high-level system status."""

    def __init__(self, runtime: JarvisRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self.setObjectName("card")
        self.setStyleSheet(_CARD_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        title_lbl = QLabel("SYSTEM")
        title_lbl.setObjectName("title")
        outer.addWidget(title_lbl)

        self._brain_lbl = QLabel()
        self._memory_lbl = QLabel()
        self._timers_lbl = QLabel()
        self._tools_lbl = QLabel()
        self._events_lbl = QLabel()
        for lbl in (
            self._brain_lbl,
            self._memory_lbl,
            self._timers_lbl,
            self._tools_lbl,
            self._events_lbl,
        ):
            lbl.setStyleSheet("color: #C8ECF7; font-size: 9pt;")
            outer.addWidget(lbl)

        self._timer = QTimer(self)
        self._timer.setInterval(2_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        status = self._runtime.status_snapshot()
        brain_color = "#B8F0C2" if status.brain_online else "#E8A85A"
        self._brain_lbl.setText(f"Brain: {status.brain_label}")
        self._brain_lbl.setStyleSheet(f"color: {brain_color}; font-size: 9pt; font-weight: bold;")
        self._memory_lbl.setText(f"Memory: {status.memory_count} fact{'s' if status.memory_count != 1 else ''}")
        self._timers_lbl.setText(
            f"Timers: {status.active_timer_count} active"
            + (f", {status.alarming_timer_count} alarming" if status.alarming_timer_count else "")
        )
        self._tools_lbl.setText("Last turn: tools used" if status.last_used_tools else "Last turn: conversation")
        self._events_lbl.setText(f"Events: {status.event_count} recorded")
        self._events_lbl.setToolTip(f"{status.active_task_count} active runtime task(s).")


# ---------------------------------------------------------------------------
# Tasks panel
# ---------------------------------------------------------------------------

class TasksPanel(QFrame):
    """Recent runtime work lane."""

    def __init__(self, runtime: JarvisRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self.setObjectName("card")
        self.setStyleSheet(_CARD_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        title_lbl = QLabel("ACTIVE TASKS")
        title_lbl.setObjectName("title")
        outer.addWidget(title_lbl)

        self._content_widget = QWidget()
        self._content_widget.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(5)
        self._content_layout.addStretch(1)
        outer.addWidget(self._content_widget, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(1_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        tasks = self._runtime.recent_tasks(limit=5)

        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not tasks:
            ph = QLabel("No active work.")
            ph.setStyleSheet("color: #466270; font-size: 9pt;")
            self._content_layout.insertWidget(0, ph)
            return

        for i, task in enumerate(tasks):
            label = f"{task.state.upper()}  {task.title}"
            if task.detail:
                label += f"\n{task.detail}"
            lbl = QLabel(label)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                "color: #DFF7FF; font-size: 9pt; font-weight: bold;"
                if task.state == "active"
                else "color: #7FA8B8; font-size: 9pt;"
            )
            self._content_layout.insertWidget(i, lbl)


# ---------------------------------------------------------------------------
# Home panel
# ---------------------------------------------------------------------------

class HomePanel(QFrame):
    """Live Home Assistant domain summary."""

    def __init__(self, runtime: JarvisRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self.setObjectName("card")
        self.setStyleSheet(_CARD_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        title_lbl = QLabel("HOME STATE")
        title_lbl.setObjectName("title")
        outer.addWidget(title_lbl)

        self._summary_lbl = QLabel()
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet("color: #C8ECF7; font-size: 9pt; font-weight: bold;")
        outer.addWidget(self._summary_lbl)

        self._domain_widget = QWidget()
        self._domain_widget.setStyleSheet("background: transparent;")
        self._domain_layout = QVBoxLayout(self._domain_widget)
        self._domain_layout.setContentsMargins(0, 0, 0, 0)
        self._domain_layout.setSpacing(4)
        self._domain_layout.addStretch(1)
        outer.addWidget(self._domain_widget, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(3_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        snapshot = self._runtime.home_snapshot()

        while self._domain_layout.count() > 1:
            item = self._domain_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not snapshot.online:
            self._summary_lbl.setText("Home Assistant offline")
            self._summary_lbl.setStyleSheet("color: #E8A85A; font-size: 9pt; font-weight: bold;")
            detail = snapshot.error or "Waiting for first monitor update."
            lbl = QLabel(detail)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #5A8090; font-size: 9pt;")
            self._domain_layout.insertWidget(0, lbl)
            return

        self._summary_lbl.setText(f"{snapshot.entity_count} watched entities")
        self._summary_lbl.setStyleSheet("color: #B8F0C2; font-size: 9pt; font-weight: bold;")

        preferred_order = ("light", "switch", "fan", "media_player")
        domains = [
            snapshot.domains[domain]
            for domain in preferred_order
            if domain in snapshot.domains
        ]
        if not domains:
            lbl = QLabel("No watched domains yet.")
            lbl.setStyleSheet("color: #466270; font-size: 9pt;")
            self._domain_layout.insertWidget(0, lbl)
            return

        for i, domain in enumerate(domains):
            name = domain.domain.replace("_", " ").upper()
            if domain.domain in ("light", "switch"):
                detail = f"{domain.on_count} on / {domain.total} total"
            elif domain.domain == "fan":
                detail = f"{domain.on_count} active / {domain.total} total"
            else:
                detail = f"{domain.active_count} active / {domain.total} total"
            if domain.unavailable_count:
                detail += f"  ({domain.unavailable_count} unavailable)"

            lbl = QLabel(f"{name}: {detail}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #C8ECF7; font-size: 9pt;")
            self._domain_layout.insertWidget(i, lbl)


# ---------------------------------------------------------------------------
# Weather panel
# ---------------------------------------------------------------------------

class WeatherPanel(QFrame):
    """Current weather snapshot."""

    def __init__(self, runtime: JarvisRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self.setObjectName("card")
        self.setStyleSheet(_CARD_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        title_lbl = QLabel("WEATHER")
        title_lbl.setObjectName("title")
        outer.addWidget(title_lbl)

        self._primary_lbl = QLabel()
        self._primary_lbl.setWordWrap(True)
        self._primary_lbl.setStyleSheet("color: #C8ECF7; font-size: 10pt; font-weight: bold;")
        outer.addWidget(self._primary_lbl)

        self._detail_lbl = QLabel()
        self._detail_lbl.setWordWrap(True)
        self._detail_lbl.setStyleSheet("color: #7FA8B8; font-size: 9pt;")
        outer.addWidget(self._detail_lbl)

        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        snapshot = self._runtime.weather_snapshot()
        if not snapshot.online:
            self._primary_lbl.setText("Weather unavailable")
            self._primary_lbl.setStyleSheet("color: #E8A85A; font-size: 10pt; font-weight: bold;")
            self._detail_lbl.setText(snapshot.error or "Waiting for weather monitor.")
            return

        temp = f"{snapshot.temperature_f}F" if snapshot.temperature_f is not None else "--"
        self._primary_lbl.setText(f"{temp}  {snapshot.description}")
        self._primary_lbl.setStyleSheet("color: #B8F0C2; font-size: 10pt; font-weight: bold;")

        details: list[str] = [snapshot.location]
        if snapshot.high_f is not None and snapshot.low_f is not None:
            details.append(f"High {snapshot.high_f} / Low {snapshot.low_f}")
        if snapshot.humidity_percent is not None:
            details.append(f"Humidity {snapshot.humidity_percent}%")
        if snapshot.wind_mph is not None:
            details.append(f"Wind {snapshot.wind_mph} mph")
        self._detail_lbl.setText("\n".join(details))


# ---------------------------------------------------------------------------
# Events panel
# ---------------------------------------------------------------------------

class EventsPanel(QFrame):
    """Scrollable runtime event inbox."""

    def __init__(self, runtime: JarvisRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self.setObjectName("card")
        self.setStyleSheet(_CARD_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        title_lbl = QLabel("EVENTS")
        title_lbl.setObjectName("title")
        outer.addWidget(title_lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + _SCROLLBAR_QSS
        )

        self._content_widget = QWidget()
        self._content_widget.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(5)
        self._content_layout.addStretch(1)

        self._scroll.setWidget(self._content_widget)
        outer.addWidget(self._scroll, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(2_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        self._runtime.observe_timers()
        events = self._runtime.recent_events(limit=20)

        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not events:
            ph = QLabel("No events yet.")
            ph.setStyleSheet("color: #466270; font-size: 9pt;")
            self._content_layout.insertWidget(0, ph)
            return

        for i, event in enumerate(events):
            when = datetime.fromtimestamp(event.created_at).strftime("%I:%M %p").lstrip("0")
            detail = f"\n{event.detail}" if event.detail else ""
            lbl = QLabel(f"{when}  {event.title}{detail}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                "color: #DFF7FF; font-size: 9pt; font-weight: bold;"
                if event.kind == "reminder"
                else "color: #C8ECF7; font-size: 9pt;"
            )
            self._content_layout.insertWidget(i, lbl)


# ---------------------------------------------------------------------------
# Sessions panel
# ---------------------------------------------------------------------------

class SessionsPanel(QFrame):
    """Scrollable list of past sessions; emits session_selected(session_id)."""

    session_selected = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(_CARD_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        title_lbl = QLabel("RECENT SESSIONS")
        title_lbl.setObjectName("title")
        outer.addWidget(title_lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + _SCROLLBAR_QSS
        )

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch(1)

        self._scroll.setWidget(self._list_widget)
        outer.addWidget(self._scroll, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        try:
            sessions = list_sessions(limit=30)
        except Exception:  # noqa: BLE001
            return

        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not sessions:
            ph = QLabel("No sessions yet.")
            ph.setStyleSheet("color: #466270; font-size: 9pt;")
            self._list_layout.insertWidget(0, ph)
            return

        for i, s in enumerate(sessions):
            sid = s.get("session_id", "")
            created = (s.get("created_at") or "")[:16]
            turns = s.get("turn_count") or 0
            preview = (s.get("preview") or "").strip()
            if len(preview) > 55:
                preview = preview[:52] + "..."

            label = f"{created}  -  {turns} turn{'s' if turns != 1 else ''}"
            if preview:
                label += f"\n{preview}"

            btn = QPushButton(label)
            btn.setStyleSheet(_SESSION_BTN_QSS)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, s=sid: self.session_selected.emit(s))
            self._list_layout.insertWidget(i, btn)

    def notify_new_session(self) -> None:
        self.refresh()


# ---------------------------------------------------------------------------
# Memory panel
# ---------------------------------------------------------------------------

class MemoryPanel(QFrame):
    """Scrollable list of all stored memory facts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(_CARD_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        title_lbl = QLabel("MEMORY")
        title_lbl.setObjectName("title")
        outer.addWidget(title_lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + _SCROLLBAR_QSS
        )

        self._content_widget = QWidget()
        self._content_widget.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(4)
        self._content_layout.addStretch(1)

        self._scroll.setWidget(self._content_widget)
        outer.addWidget(self._scroll, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(5_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        try:
            if not _MEMORY_FILE.exists():
                self._set_placeholder("No memory yet.")
                return
            with _MEMORY_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            self._set_placeholder("(memory unreadable)")
            return

        facts = data.get("facts", []) or []

        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not facts:
            self._set_placeholder("Memory is empty.")
            return

        for i, f in enumerate(facts):
            content = (f.get("content") or "").strip()
            if not content:
                continue
            lbl = QLabel(f"* {content}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #C8ECF7; font-size: 10pt;")
            self._content_layout.insertWidget(i, lbl)

    def _set_placeholder(self, text: str) -> None:
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #466270; font-size: 10pt;")
        self._content_layout.insertWidget(0, lbl)


# ---------------------------------------------------------------------------
# Timer card widget
# ---------------------------------------------------------------------------

class _TimerCard(QFrame):
    """One row in the timers panel: shows countdown + action buttons."""

    def __init__(self, timer: Timer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tid = timer.id

        if timer.alarming:
            self.setObjectName("alarm_card")
            self.setStyleSheet(_ALARM_CARD_QSS)
        else:
            self.setObjectName("card")
            self.setStyleSheet(
                "QFrame#card {"
                "  background-color: rgba(6, 18, 26, 210);"
                "  border: 1px solid rgba(0, 140, 180, 60);"
                "  border-radius: 6px;"
                "}"
            )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # --- Top row: message label + countdown ---------------------------
        top = QHBoxLayout()
        top.setSpacing(6)

        self._msg_lbl = QLabel(timer.message)
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setStyleSheet(
            "color: #DFF7FF; font-size: 9pt; font-weight: bold;"
            if timer.alarming
            else "color: #BFE6F2; font-size: 9pt;"
        )
        top.addWidget(self._msg_lbl, 1)

        self._time_lbl = QLabel()
        self._time_lbl.setStyleSheet(
            "color: #FF6040; font-size: 9pt; font-weight: bold; font-family: monospace;"
            if timer.alarming
            else "color: #4FB4D0; font-size: 9pt; font-family: monospace;"
        )
        self._time_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        top.addWidget(self._time_lbl)
        layout.addLayout(top)

        # --- Bottom row: action buttons -----------------------------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(5)
        btn_row.addStretch(1)

        if timer.alarming:
            dismiss_btn = QPushButton("DISMISS")
            dismiss_btn.setStyleSheet(_DISMISS_BTN_QSS)
            dismiss_btn.clicked.connect(self._on_dismiss)
            btn_row.addWidget(dismiss_btn)
        else:
            if timer.paused:
                self._pause_btn = QPushButton("RESUME")
            else:
                self._pause_btn = QPushButton("PAUSE")
            self._pause_btn.setStyleSheet(_PAUSE_BTN_QSS)
            self._pause_btn.clicked.connect(self._on_pause_resume)
            btn_row.addWidget(self._pause_btn)

            cancel_btn = QPushButton("CANCEL")
            cancel_btn.setStyleSheet(_CANCEL_BTN_QSS)
            cancel_btn.clicked.connect(self._on_cancel)
            btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)
        self._update_time(timer)

    def update_timer(self, timer: Timer) -> None:
        self._update_time(timer)

    def _update_time(self, timer: Timer) -> None:
        if timer.alarming:
            self._time_lbl.setText("NOW")
        elif timer.paused:
            rem = timer.paused_remaining or 0.0
            self._time_lbl.setText(f"PAUSED {_fmt(rem)}")
            if hasattr(self, "_pause_btn"):
                self._pause_btn.setText("RESUME")
        else:
            rem = timer.remaining_seconds()
            self._time_lbl.setText(_fmt(rem))
            if hasattr(self, "_pause_btn"):
                self._pause_btn.setText("PAUSE")

    def _on_pause_resume(self) -> None:
        # Read current button label to decide direction
        if self._pause_btn.text() == "PAUSE":
            pause_timer(self._tid)
        else:
            resume_timer(self._tid)

    def _on_cancel(self) -> None:
        cancel_timer(self._tid)

    def _on_dismiss(self) -> None:
        dismiss_timer(self._tid)


def _fmt(seconds: float) -> str:
    """Compact HH:MM:SS or MM:SS countdown string."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _play_alarm_beep() -> None:
    """Non-blocking alarm beep (Windows winsound; silent on other platforms)."""
    def _beep() -> None:
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(880, 200)
                __import__("time").sleep(0.12)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_beep, daemon=True).start()


# ---------------------------------------------------------------------------
# Timers panel
# ---------------------------------------------------------------------------

class TimersPanel(QFrame):
    """Interactive scrollable panel of active timers with alarm support."""

    def __init__(self, runtime: JarvisRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self.setObjectName("card")
        self.setStyleSheet(_CARD_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        title_lbl = QLabel("ACTIVE TIMERS")
        title_lbl.setObjectName("title")
        outer.addWidget(title_lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + _SCROLLBAR_QSS
        )

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(5)
        self._list_layout.addStretch(1)

        self._scroll.setWidget(self._list_widget)
        outer.addWidget(self._scroll, 1)

        # Track cards keyed by timer id
        self._cards: dict[str, _TimerCard] = {}
        self._placeholder: QLabel | None = None

        # 1-second tick for countdowns
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1_000)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

        # Alarm beep fires every 4s while any timer is alarming.
        self._alarm_timer = QTimer(self)
        self._alarm_timer.setInterval(4_000)
        self._alarm_timer.timeout.connect(self._maybe_beep)

        self._tick()

    def _tick(self) -> None:
        # Transition any just-expired timers to alarming state.
        # (In voice mode the runner also calls this, but calling twice is safe.)
        self._runtime.observe_timers()

        timers = self._runtime.active_timers()
        timer_ids = {t.id for t in timers}

        # Remove cards for timers that no longer exist
        for tid in list(self._cards.keys()):
            if tid not in timer_ids:
                card = self._cards.pop(tid)
                self._list_layout.removeWidget(card)
                card.deleteLater()

        has_alarming = False

        # Add / update cards
        for i, t in enumerate(timers):
            if t.alarming:
                has_alarming = True

            if t.id in self._cards:
                self._cards[t.id].update_timer(t)
            else:
                card = _TimerCard(t)
                self._cards[t.id] = card
                # Insert before the trailing stretch
                insert_pos = self._list_layout.count() - 1
                self._list_layout.insertWidget(insert_pos, card)

        # Show placeholder when empty
        if not timers:
            if not hasattr(self, "_placeholder") or self._placeholder is None:
                ph = QLabel("No active timers.")
                ph.setStyleSheet("color: #466270; font-size: 9pt;")
                self._list_layout.insertWidget(0, ph)
                self._placeholder = ph
        else:
            if hasattr(self, "_placeholder") and self._placeholder is not None:
                self._list_layout.removeWidget(self._placeholder)
                self._placeholder.deleteLater()
                self._placeholder = None

        # Manage alarm beeper
        if has_alarming and not self._alarm_timer.isActive():
            _play_alarm_beep()   # immediate first beep
            self._alarm_timer.start()
        elif not has_alarming and self._alarm_timer.isActive():
            self._alarm_timer.stop()

    def _maybe_beep(self) -> None:
        timers = self._runtime.active_timers()
        if any(t.alarming for t in timers):
            _play_alarm_beep()
        else:
            self._alarm_timer.stop()


# ---------------------------------------------------------------------------
# Now Playing panel
# ---------------------------------------------------------------------------

class NowPlayingPanel(QFrame):
    """Shows the current Spotify track; hides itself when nothing is playing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(_CARD_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        title_lbl = QLabel("NOW PLAYING")
        title_lbl.setObjectName("title")
        layout.addWidget(title_lbl)

        self._track = QLabel("—")
        self._track.setWordWrap(True)
        self._track.setStyleSheet("color: #C8ECF7; font-size: 10pt; font-weight: bold;")
        layout.addWidget(self._track)

        self._artist = QLabel("")
        self._artist.setWordWrap(True)
        self._artist.setStyleSheet("color: #80A8B8; font-size: 9pt;")
        layout.addWidget(self._artist)

        self._timer = QTimer(self)
        self._timer.setInterval(8_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        try:
            from integrations.spotify import current_track_structured
            info = current_track_structured()
        except Exception:  # noqa: BLE001
            self.hide()
            return

        if not info:
            self.hide()
            return

        self.show()
        self._track.setText(info.get("track", "Unknown"))
        artist = info.get("artist", "")
        album = info.get("album", "")
        sub = artist
        if album and album != info.get("track", ""):
            sub = f"{artist}  ·  {album}" if artist else album
        self._artist.setText(sub)


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------

class StatusColumn(QWidget):
    """Vertical stack of status panels."""

    session_selected = pyqtSignal(str)

    def __init__(self, runtime: JarvisRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.now_playing = NowPlayingPanel()
        self.runtime = RuntimePanel(runtime)
        self.tasks = TasksPanel(runtime)
        self.home = HomePanel(runtime)
        self.weather = WeatherPanel(runtime)
        self.events = EventsPanel(runtime)
        self.sessions = SessionsPanel()
        self.sessions.session_selected.connect(self.session_selected)

        self.memory = MemoryPanel()
        self.timers = TimersPanel(runtime)

        layout.addWidget(self.now_playing, 0)
        layout.addWidget(self.runtime, 1)
        layout.addWidget(self.tasks, 1)
        layout.addWidget(self.home, 1)
        layout.addWidget(self.weather, 1)
        layout.addWidget(self.events, 2)
        layout.addWidget(self.sessions, 3)
        layout.addWidget(self.memory, 3)
        layout.addWidget(self.timers, 2)
        layout.addStretch(0)
