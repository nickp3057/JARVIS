"""The JARVIS orb - an animated Iron Man HUD-style visualization.

A QWidget that paints a glowing cyan holographic core with concentric rotating rings,
tick marks, and state-driven needle protrusions. Drives a state machine
(IDLE / LISTENING / THINKING / SPEAKING) so the assistant's mood is visible
at a glance.

All drawing is custom QPainter. A QTimer at 60 FPS advances a phase clock
that every animated element reads from.
"""

from __future__ import annotations

import math
import random
from enum import Enum

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget


class OrbState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ALERT = "alert"


# State -> (primary color, secondary color, pulse Hz, ring speed, spike count, glow strength).
_STATE_PARAMS: dict[OrbState, dict] = {
    OrbState.IDLE: {
        "primary": QColor(0, 150, 190),
        "secondary": QColor(0, 70, 95),
        "pulse_hz": 0.25,
        "ring_speed": 5.0,    # degrees per second
        "spike_count": 0,
        "glow": 0.6,
    },
    OrbState.LISTENING: {
        "primary": QColor(0, 205, 245),
        "secondary": QColor(0, 110, 150),
        "pulse_hz": 1.4,
        "ring_speed": 15.0,
        "spike_count": 0,
        "glow": 0.95,
    },
    OrbState.THINKING: {
        # Amber "processing / targeting" mode, like the suit's analysis overlay.
        "primary": QColor(240, 172, 40),
        "secondary": QColor(150, 96, 10),
        "pulse_hz": 0.8,
        "ring_speed": 38.0,
        "spike_count": 4,
        "glow": 0.9,
    },
    OrbState.SPEAKING: {
        "primary": QColor(40, 220, 255),
        "secondary": QColor(0, 140, 185),
        "pulse_hz": 2.8,
        "ring_speed": 12.0,
        "spike_count": 12,
        "glow": 1.0,
    },
    OrbState.ALERT: {
        # Hot-red alert state for errors / warnings.
        "primary": QColor(255, 70, 50),
        "secondary": QColor(150, 25, 18),
        "pulse_hz": 2.2,
        "ring_speed": 30.0,
        "spike_count": 8,
        "glow": 1.0,
    },
}


class JarvisOrb(QWidget):
    """The animated centerpiece of the JARVIS UI."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = OrbState.IDLE
        self._phase = 0.0  # seconds since start
        self._spike_seed = random.Random(7)
        self._fps = 60

        # Audio-reactive level: 0.0 (silent) to 1.0 (peak).
        # Set externally via set_rms(); decays each tick so the visual
        # falls off naturally between envelope frames.
        self._rms_level: float = 0.0

        self.setMinimumSize(260, 260)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(1000 / self._fps))

    # --- Public API ---------------------------------------------------------

    def set_state(self, state: OrbState) -> None:
        if state == self._state:
            return
        self._state = state
        # Reseed spikes so the speaking pattern has a fresh look on each transition.
        self._spike_seed = random.Random()
        if state != OrbState.SPEAKING:
            self._rms_level = 0.0
        self.update()

    def state(self) -> OrbState:
        return self._state

    def set_rms(self, level: float) -> None:
        """Feed a normalized audio RMS level (0.0-1.0).

        The orb rises instantly toward ``level`` and decays naturally each
        tick so there's no jarring snap between envelope frames.
        """
        level = max(0.0, min(1.0, level))
        # Rise fast, decay handled in _tick
        self._rms_level = max(self._rms_level, level)

    # --- Animation ----------------------------------------------------------

    def _tick(self) -> None:
        self._phase += 1.0 / self._fps
        # Decay RMS toward zero each frame so the glow fades between envelope
        # updates.  At 60 fps a factor of 0.93 gives ~1s to fully decay,
        # keeping peaks visible long enough to read.
        self._rms_level *= 0.93
        if self._rms_level < 0.005:
            self._rms_level = 0.0
        self.update()

    # --- Painting -----------------------------------------------------------

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        params = _STATE_PARAMS[self._state]
        primary: QColor = params["primary"]
        secondary: QColor = params["secondary"]
        pulse_hz: float = params["pulse_hz"]
        ring_speed: float = params["ring_speed"]
        spike_count: int = params["spike_count"]
        glow: float = params["glow"]

        w = self.width()
        h = self.height()
        size = min(w, h)
        cx, cy = w / 2.0, h / 2.0

        # Pulse factor (smoothly oscillates between 0.92 and 1.08).
        # In SPEAKING state the RMS level heavily boosts the pulse so the
        # whole orb visibly breathes with the voice.
        rms = self._rms_level
        rms_pulse_boost = 0.22 * rms if self._state == OrbState.SPEAKING else 0.0
        pulse = 1.0 + (0.08 + rms_pulse_boost) * math.sin(self._phase * pulse_hz * 2 * math.pi)

        # Base radius scaled to widget size.
        base_r = (size * 0.42) * pulse

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )

        # --- Background dark gradient (deep red-black) --------------------
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0, QColor(8, 16, 24))
        bg.setColorAt(1.0, QColor(2, 5, 10))
        painter.fillRect(0, 0, w, h, bg)

        # --- Faint scanlines (HUD overlay) --------------------------------
        scan = QColor(primary)
        scan.setAlphaF(0.05)
        painter.setPen(QPen(scan, 1))
        y = 0
        while y < h:
            painter.drawLine(0, int(y), w, int(y))
            y += 3

        # --- Outer halo ---------------------------------------------------
        # RMS dramatically expands and brightens the halo when speaking.
        halo_r = base_r * (1.9 + 0.9 * rms)
        halo = QRadialGradient(cx, cy, halo_r)
        halo_color = QColor(primary)
        halo_color.setAlphaF(min(1.0, (0.35 + 0.55 * rms) * glow))
        halo.setColorAt(0.0, halo_color)
        edge = QColor(primary)
        edge.setAlphaF(0.0)
        halo.setColorAt(1.0, edge)
        painter.setBrush(halo)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

        # --- Outer ring with tick marks -----------------------------------
        ring_phase = (self._phase * ring_speed) % 360.0
        outer_r = base_r * 1.45
        self._draw_ticked_ring(
            painter,
            cx,
            cy,
            outer_r,
            color=primary,
            tick_count=72,
            major_every=6,
            rotation_deg=ring_phase,
            tick_len=size * 0.025,
        )

        # --- Middle ring with arc segments (counter-rotation) ------------
        mid_r = base_r * 1.18
        self._draw_arc_ring(
            painter,
            cx,
            cy,
            mid_r,
            color=secondary,
            rotation_deg=-ring_phase * 1.3,
            segments=4,
            gap_deg=22,
        )

        # --- Inner ring (faster) -----------------------------------------
        inner_r = base_r * 0.95
        self._draw_arc_ring(
            painter,
            cx,
            cy,
            inner_r,
            color=primary,
            rotation_deg=ring_phase * 2.1,
            segments=3,
            gap_deg=30,
            width=2,
        )

        # --- Core sphere --------------------------------------------------
        # Core flares significantly brighter at high RMS (voice peaks).
        core_r = base_r * (0.55 + 0.12 * rms)
        core_grad = QRadialGradient(cx, cy - core_r * 0.2, core_r)
        bright = QColor(primary).lighter(int(150 + 120 * rms))
        bright.setAlphaF(min(1.0, 0.85 * glow + 0.1 + 0.25 * rms))
        dim = QColor(secondary)
        dim.setAlphaF(0.4)
        edge = QColor(0, 0, 0, 0)
        core_grad.setColorAt(0.0, bright)
        core_grad.setColorAt(0.6, dim)
        core_grad.setColorAt(1.0, edge)
        painter.setBrush(core_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), core_r, core_r)

        # --- Spikes / needles (state-driven) -----------------------------
        if spike_count > 0:
            # In SPEAKING state the spike zone dramatically extends with RMS
            # so the orb visibly "shouts" at loud moments.
            spike_outer = outer_r * (1.18 + 0.65 * rms)
            self._draw_spikes(
                painter,
                cx,
                cy,
                inner_r=outer_r * 1.02,
                outer_r=spike_outer,
                color=primary,
                count=spike_count,
                phase=self._phase,
                rms=rms,
            )

        # --- State label below orb ---------------------------------------
        self._draw_state_label(painter, w, h, primary)

        painter.end()

    # --- Drawing helpers ----------------------------------------------------

    def _draw_ticked_ring(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        r: float,
        color: QColor,
        tick_count: int,
        major_every: int,
        rotation_deg: float,
        tick_len: float,
    ) -> None:
        pen = QPen(color)
        pen.setWidthF(1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)

        for i in range(tick_count):
            angle_deg = rotation_deg + (i * 360.0 / tick_count)
            angle_rad = math.radians(angle_deg)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)

            inner = r
            outer = r + tick_len * (1.7 if i % major_every == 0 else 1.0)
            x1 = cx + cos_a * inner
            y1 = cy + sin_a * inner
            x2 = cx + cos_a * outer
            y2 = cy + sin_a * outer

            faded = QColor(color)
            faded.setAlphaF(0.85 if i % major_every == 0 else 0.5)
            pen.setColor(faded)
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _draw_arc_ring(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        r: float,
        color: QColor,
        rotation_deg: float,
        segments: int,
        gap_deg: float,
        width: float = 3.0,
    ) -> None:
        pen = QPen(color)
        pen.setWidthF(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        seg_total = 360.0 / segments
        seg_arc = seg_total - gap_deg

        for i in range(segments):
            start_deg = rotation_deg + i * seg_total
            # Qt's drawArc uses 1/16 of a degree.
            start_qt = int(start_deg * 16)
            span_qt = int(seg_arc * 16)
            p.drawArc(rect, start_qt, span_qt)

    def _draw_spikes(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        inner_r: float,
        outer_r: float,
        color: QColor,
        count: int,
        phase: float,
        rms: float = 0.0,
    ) -> None:
        pen = QPen(color)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)

        for i in range(count):
            # Deterministic per-spike base angle + animated wobble.
            base_angle = (i / count) * 360.0
            wobble = 8.0 * math.sin(phase * 2.2 + i)
            angle_deg = base_angle + wobble
            angle_rad = math.radians(angle_deg)

            # Sine animation blended with RMS so spikes shoot out when loud.
            sine_factor = 0.5 + 0.5 * abs(math.sin(phase * 3.2 + i * 0.7))
            # Each spike gets a slightly different RMS boost so they don't
            # all move in lockstep (more organic look).
            rms_factor = rms * (0.7 + 0.3 * abs(math.sin(i * 1.3)))
            length_factor = max(sine_factor, rms_factor)
            length = (outer_r - inner_r) * length_factor

            x1 = cx + math.cos(angle_rad) * inner_r
            y1 = cy + math.sin(angle_rad) * inner_r
            x2 = cx + math.cos(angle_rad) * (inner_r + length)
            y2 = cy + math.sin(angle_rad) * (inner_r + length)

            faded = QColor(color)
            faded.setAlphaF(min(1.0, 0.5 + 0.5 * length_factor))
            pen.setColor(faded)
            pen.setWidthF(1.0 + length_factor * 2.2)
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _draw_state_label(self, p: QPainter, w: int, h: int, color: QColor) -> None:
        label_map = {
            OrbState.IDLE: "STANDBY",
            OrbState.LISTENING: "LISTENING",
            OrbState.THINKING: "THINKING",
            OrbState.SPEAKING: "SPEAKING",
            OrbState.ALERT: "ALERT",
        }
        label = label_map[self._state]

        font = p.font()
        font.setPointSize(max(10, int(min(w, h) * 0.04)))
        font.setBold(True)
        font.setLetterSpacing(p.font().SpacingType.AbsoluteSpacing, 3.0)
        p.setFont(font)

        faded = QColor(color)
        faded.setAlphaF(0.85)
        p.setPen(QPen(faded))

        rect = QRectF(0, h * 0.84, w, h * 0.1)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), label)
