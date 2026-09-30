"""Timeline: signal score over time, kept fragments and a draggable threshold."""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..analysis import AnalysisResult
from ..segments import Segment
from ..timefmt import format_time
from . import theme

_TICKS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]
_AXIS_H = 20
_BLANK_H = 5
_GRAB = 6


class TimelineWidget(QWidget):
    thresholdChanged = Signal(float)
    hoverChanged = Signal(float)      # seconds, or -1 when the mouse leaves
    positionClicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._result: Optional[AnalysisResult] = None
        self._segments: List[Segment] = []
        self._threshold = 0.45
        self._hover: Optional[float] = None
        self._playhead: Optional[float] = None
        self._view: Tuple[float, float] = (0.0, 1.0)
        self._drag_threshold = False
        self._envelope_key = None
        self._envelope = None
        self._blank_runs: List[Tuple[float, float]] = []

    # ----------------------------------------------------------------- data
    def set_result(self, result: Optional[AnalysisResult]) -> None:
        if result is not self._result:
            self._result = result
            self._envelope_key = None
            self._blank_runs = []
            if result is not None:
                self._view = (0.0, max(result.duration, 1e-3))
                self._blank_runs = self._runs(result.blank, result.rate)
            self._hover = None
        self.update()

    def set_segments(self, segments: List[Segment]) -> None:
        self._segments = list(segments)
        self.update()

    def set_threshold(self, value: float) -> None:
        self._threshold = float(value)
        self.update()

    def set_playhead(self, t: Optional[float]) -> None:
        self._playhead = t
        self.update()

    def duration(self) -> float:
        return self._result.duration if self._result is not None else 0.0

    @staticmethod
    def _runs(mask: np.ndarray, rate: float) -> List[Tuple[float, float]]:
        if len(mask) == 0 or not mask.any():
            return []
        padded = np.concatenate([[0], mask.astype(np.int8), [0]])
        d = np.diff(padded)
        return [(a / rate, b / rate) for a, b in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0])]

    # ------------------------------------------------------------- geometry
    def _plot(self) -> QRectF:
        return QRectF(8, 8, max(10, self.width() - 16), max(10, self.height() - 8 - _AXIS_H))

    def _x(self, t: float) -> float:
        r = self._plot()
        v0, v1 = self._view
        return r.left() + (t - v0) / max(v1 - v0, 1e-9) * r.width()

    def _t(self, x: float) -> float:
        r = self._plot()
        v0, v1 = self._view
        t = v0 + (x - r.left()) / max(r.width(), 1) * (v1 - v0)
        return min(max(t, 0.0), self.duration())

    def _y(self, v: float) -> float:
        r = self._plot()
        return r.bottom() - _BLANK_H - v * (r.height() - _BLANK_H)

    def _threshold_from_y(self, y: float) -> float:
        r = self._plot()
        v = (r.bottom() - _BLANK_H - y) / max(r.height() - _BLANK_H, 1)
        return round(min(max(v, 0.02), 0.98), 2)

    # ------------------------------------------------------------- painting
    def _compute_envelope(self, width: int):
        key = (width, self._view, id(self._result))
        if key == self._envelope_key:
            return self._envelope
        res = self._result
        n = res.frame_count
        v0, v1 = self._view
        # Frames [start, end) are visible; column c covers frames from edge c to edge c+1.
        start = int(np.clip(np.floor(v0 * res.rate), 0, n - 1))
        end = int(np.clip(np.ceil(v1 * res.rate) + 1, start + 1, n))
        visible = res.score[start:end]
        edges = np.linspace(v0 * res.rate, v1 * res.rate, width + 1)[:-1]
        cols = np.clip(np.floor(edges).astype(np.int64) - start, 0, len(visible) - 1)
        # reduceat: min/max over [cols[c], cols[c+1]), or the single frame if that is empty
        self._envelope = (np.minimum.reduceat(visible, cols), np.maximum.reduceat(visible, cols))
        self._envelope_key = key
        return self._envelope

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), theme.TL_BG)
        r = self._plot()
        if self._result is None or self._result.frame_count == 0:
            p.setPen(theme.MUTED)
            p.drawText(self.rect(), Qt.AlignCenter, "Здесь появится шкала сигнала после анализа")
            p.end()
            return

        # grid
        p.setPen(QPen(theme.TL_GRID, 1))
        for v in (0.25, 0.5, 0.75, 1.0):
            y = self._y(v)
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

        # kept fragments
        for seg in self._segments:
            x0, x1 = self._x(seg.start), self._x(seg.end)
            if x1 < r.left() or x0 > r.right():
                continue
            x0, x1 = max(x0, r.left()), min(x1, r.right())
            rect = QRectF(x0, r.top(), max(x1 - x0, 1.0), r.height() - _BLANK_H)
            if seg.enabled:
                p.fillRect(rect, theme.TL_KEEP)
                p.setPen(QPen(theme.TL_KEEP_EDGE, 1))
                p.drawLine(rect.topLeft(), rect.bottomLeft())
                p.drawLine(rect.topRight(), rect.bottomRight())
            else:
                p.fillRect(rect, QBrush(theme.TL_OFF, Qt.BDiagPattern))

        # score envelope
        width = int(r.width())
        lo, hi = self._compute_envelope(width)
        xs = r.left() + np.arange(width) + 0.5
        path = QPainterPath()
        path.moveTo(xs[0], self._y(float(hi[0])))
        for x, v in zip(xs[1:], hi[1:]):
            path.lineTo(x, self._y(float(v)))
        for x, v in zip(xs[::-1], lo[::-1]):
            path.lineTo(x, self._y(float(v)))
        path.closeSubpath()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.TL_SCORE)
        p.drawPath(path)
        edge = QPainterPath()
        edge.moveTo(xs[0], self._y(float(hi[0])))
        for x, v in zip(xs[1:], hi[1:]):
            edge.lineTo(x, self._y(float(v)))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(theme.TL_SCORE_EDGE, 1.2))
        p.drawPath(edge)
        p.setRenderHint(QPainter.Antialiasing, False)

        # blank ("no signal" screen) strip
        for a, b in self._blank_runs:
            x0, x1 = max(self._x(a), r.left()), min(self._x(b), r.right())
            if x1 > x0:
                p.fillRect(QRectF(x0, r.bottom() - _BLANK_H + 1, max(x1 - x0, 1.0), _BLANK_H - 1), theme.TL_BLANK)

        # threshold
        y = self._y(self._threshold)
        pen = QPen(theme.TL_THRESHOLD, 1.5 if not self._drag_threshold else 2.5, Qt.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        p.setFont(font)
        label = f"порог {int(round(self._threshold * 100))}%"
        lw = p.fontMetrics().horizontalAdvance(label) + 8
        p.fillRect(QRectF(r.right() - lw, y - 16, lw, 14), QColor(18, 19, 22, 200))
        p.setPen(theme.TL_THRESHOLD)
        p.drawText(QRectF(r.right() - lw, y - 16, lw, 14), Qt.AlignCenter, label)

        # time axis
        self._paint_axis(p, r)

        # playhead and hover
        if self._playhead is not None:
            x = self._x(self._playhead)
            if r.left() <= x <= r.right():
                p.setPen(QPen(theme.TL_PLAYHEAD, 1.5))
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        if self._hover is not None and not self._drag_threshold:
            x = self._x(self._hover)
            p.setPen(QPen(QColor(255, 255, 255, 150), 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            text = f"{format_time(self._hover)} · {int(round(self._result.score_at(self._hover) * 100))}%"
            tw = p.fontMetrics().horizontalAdvance(text) + 10
            bx = min(max(x + 6, r.left()), r.right() - tw)
            p.fillRect(QRectF(bx, r.top() + 2, tw, 16), QColor(38, 40, 44, 230))
            p.setPen(theme.TEXT)
            p.drawText(QRectF(bx, r.top() + 2, tw, 16), Qt.AlignCenter, text)

        if self._view != (0.0, max(self._result.duration, 1e-3)):
            p.setPen(theme.MUTED)
            p.drawText(QRectF(r.left() + 4, r.top() + 2, 300, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       "масштаб: двойной щелчок — сбросить")
        p.end()

    def _paint_axis(self, p: QPainter, r: QRectF) -> None:
        v0, v1 = self._view
        span = max(v1 - v0, 1e-6)
        min_px = 80
        step = _TICKS[-1]
        for s in _TICKS:
            if s / span * r.width() >= min_px:
                step = s
                break
        decimals = 1 if step < 1 else 0
        first = np.ceil(v0 / step) * step
        p.setPen(theme.MUTED)
        t = first
        while t <= v1 + 1e-9:
            x = self._x(t)
            p.setPen(QPen(theme.TL_GRID, 1))
            p.drawLine(QPointF(x, r.bottom()), QPointF(x, r.bottom() + 4))
            p.setPen(theme.MUTED)
            left = min(max(x - 40, 0.0), self.width() - 80.0)
            align = Qt.AlignLeft if left == 0.0 else (Qt.AlignRight if left < x - 40 else Qt.AlignHCenter)
            p.drawText(QRectF(left, r.bottom() + 3, 80, _AXIS_H - 3), align | Qt.AlignTop,
                       format_time(t, decimals))
            t += step

    # ---------------------------------------------------------- interaction
    def _near_threshold(self, y: float) -> bool:
        return abs(y - self._y(self._threshold)) <= _GRAB

    def mousePressEvent(self, e) -> None:
        if self._result is None or e.button() != Qt.LeftButton:
            return
        pos = e.position()
        if self._near_threshold(pos.y()):
            self._drag_threshold = True
            self.update()
            return
        t = self._t(pos.x())
        self._playhead = t
        self.positionClicked.emit(t)
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._result is None:
            return
        pos = e.position()
        if self._drag_threshold:
            value = self._threshold_from_y(pos.y())
            if value != self._threshold:
                self._threshold = value
                self.thresholdChanged.emit(value)
            self.update()
            return
        self.setCursor(Qt.SizeVerCursor if self._near_threshold(pos.y()) else Qt.CrossCursor)
        if e.buttons() & Qt.LeftButton:
            t = self._t(pos.x())
            self._playhead = t
            self.positionClicked.emit(t)
        self._hover = self._t(pos.x())
        self.hoverChanged.emit(self._hover)
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_threshold:
            self._drag_threshold = False
            self.update()

    def leaveEvent(self, e) -> None:
        self._hover = None
        self.hoverChanged.emit(-1.0)
        self.update()

    def wheelEvent(self, e) -> None:
        if self._result is None:
            return
        total = max(self._result.duration, 1e-3)
        v0, v1 = self._view
        span = v1 - v0
        delta = e.angleDelta().y() or e.angleDelta().x()
        if not delta:
            return
        if e.modifiers() & Qt.ShiftModifier:
            shift = -span * 0.15 * (1 if delta > 0 else -1)
            v0 = min(max(v0 + shift, 0.0), total - span)
            self._view = (v0, v0 + span)
        else:
            anchor = self._t(e.position().x())
            factor = 0.8 if delta > 0 else 1.25
            new_span = min(max(span * factor, min(2.0, total)), total)
            frac = (anchor - v0) / span if span > 0 else 0.5
            v0 = min(max(anchor - frac * new_span, 0.0), total - new_span)
            self._view = (v0, v0 + new_span)
        self._envelope_key = None
        self.update()
        e.accept()

    def keyPressEvent(self, e) -> None:
        if self._result is None or e.key() not in (Qt.Key_Left, Qt.Key_Right):
            super().keyPressEvent(e)
            return
        step = 0.1 if e.modifiers() & Qt.ShiftModifier else 1.0
        if e.key() == Qt.Key_Left:
            step = -step
        t = min(max((self._playhead or 0.0) + step, 0.0), self.duration())
        self._playhead = t
        self.positionClicked.emit(t)
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        if self._result is not None:
            self._view = (0.0, max(self._result.duration, 1e-3))
            self._envelope_key = None
            self.update()
