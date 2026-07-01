"""HUD chrome widgets for the JARVIS cockpit.

A small reusable QFrame that paints glowing cyan corner brackets over its
border, the signature framing motif of the Iron Man / JARVIS holographic
interface. Everything else (background, border, radius) still comes from the
frame's stylesheet; this only overlays the four corner ticks.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QWidget
from PyQt6.QtCore import Qt


class HUDFrame(QFrame):
    """A framed panel with movie-style cyan corner brackets."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        bracket_color: QColor | None = None,
        bracket_len: int = 16,
        inset: int = 3,
    ) -> None:
        super().__init__(parent)
        self._bracket_color = bracket_color or QColor(0, 217, 255)
        self._bracket_len = bracket_len
        self._inset = inset

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._bracket_color)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)

        w, h = self.width(), self.height()
        m = self._inset
        L = self._bracket_len

        # Top-left
        p.drawLine(m, m, m + L, m)
        p.drawLine(m, m, m, m + L)
        # Top-right
        p.drawLine(w - m, m, w - m - L, m)
        p.drawLine(w - m, m, w - m, m + L)
        # Bottom-left
        p.drawLine(m, h - m, m + L, h - m)
        p.drawLine(m, h - m, m, h - m - L)
        # Bottom-right
        p.drawLine(w - m, h - m, w - m - L, h - m)
        p.drawLine(w - m, h - m, w - m, h - m - L)
        p.end()
