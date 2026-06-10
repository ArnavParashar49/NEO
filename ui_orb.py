"""Premium animated gradient orb — ARIA's signature visual.

A glowing, fluid multi-color sphere (Siri-style aurora) that breathes and reacts
to voice. Drop-in replacement for ParticleSphereWidget — same interface:
``set_audio_bands()``, ``.speaking``, ``.muted``, ``.state``.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

# Signature aurora palette (blue -> violet -> magenta -> cyan) — vivid
_PALETTE = [
    (40, 150, 255),    # electric blue
    (150, 80, 255),    # violet
    (255, 70, 200),    # magenta
    (40, 235, 255),    # cyan
]


class GradientOrb(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._t = 0.0
        self._energy = 0.06          # target (from audio)
        self._energy_s = 0.06        # smoothed
        self._speaking = False
        self._muted = False
        self._state = "STANDBY"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(16)        # ~60 fps

    # --- interface (matches ParticleSphereWidget) ---------------------------
    def set_audio_bands(self, bands) -> None:
        if not bands:
            self._energy = 0.05
            return
        self._energy = min(1.0, max(0.05, (sum(bands) / max(len(bands), 1)) * 2.0))

    @property
    def speaking(self) -> bool:
        return self._speaking

    @speaking.setter
    def speaking(self, v) -> None:
        self._speaking = bool(v)

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, v) -> None:
        self._muted = bool(v)

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, v) -> None:
        self._state = v or "STANDBY"

    # --- animation ----------------------------------------------------------
    def _step(self) -> None:
        calm = self._muted or self._state == "STANDBY"
        speed = 0.006 if calm else 0.013
        if self._speaking or self._state == "SPEAKING":
            speed = 0.022
        self._t += speed
        self._energy_s += (self._energy - self._energy_s) * 0.12
        self.update()

    # --- paint --------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        unit = min(w, h) * 0.5
        e = self._energy_s
        breathe = 0.5 + 0.5 * math.sin(self._t * 1.7)
        r = unit * (0.66 + 0.02 * breathe + e * 0.10)   # core radius

        dim = 1.0
        if self._muted or self._state == "STANDBY":
            dim = 0.55
        elif self._state == "THINKING":
            dim = 0.82

        # outer bloom -------------------------------------------------------
        bloom_r = unit * (1.0 + e * 0.12)
        bloom = QRadialGradient(cx, cy, bloom_r)
        bloom.setColorAt(0.0, QColor(120, 90, 255, int(120 * dim)))
        bloom.setColorAt(0.30, QColor(90, 120, 255, int(70 * dim)))
        bloom.setColorAt(0.55, QColor(70, 100, 255, int(30 * dim)))
        bloom.setColorAt(1.0, QColor(70, 100, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bloom)
        p.drawEllipse(QPointF(cx, cy), bloom_r, bloom_r)

        # core: clip to circle, paint aurora blobs --------------------------
        clip = QPainterPath()
        clip.addEllipse(QPointF(cx, cy), r, r)
        p.save()
        p.setClipPath(clip)

        # luminous glass base — the orb glows blue->violet on its own
        sat = 0.55 + 0.45 * dim   # desaturate toward standby
        def mix(c, lo):
            return QColor(int(lo[0] + (c[0]-lo[0]) * sat),
                          int(lo[1] + (c[1]-lo[1]) * sat),
                          int(lo[2] + (c[2]-lo[2]) * sat))
        base = QRadialGradient(cx - r * 0.16, cy - r * 0.26, r * 1.55)
        base.setColorAt(0.0, mix((95, 120, 255), (40, 42, 70)))
        base.setColorAt(0.52, mix((84, 56, 205), (32, 30, 60)))
        base.setColorAt(1.0, QColor(22, 16, 52))
        p.fillRect(QRectF(cx - r, cy - r, 2 * r, 2 * r), QBrush(base))

        # bright moving accents (cyan / magenta / blue) — Screen for glow
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
        for i, (cr, cg, cb) in enumerate(_PALETTE):
            ph = self._t * (0.85 + 0.12 * i) + i * 1.7
            bx = cx + math.cos(ph + i) * r * 0.5
            by = cy + math.sin(ph * 1.27 + i * 1.3) * r * 0.5
            br = r * (0.55 + 0.12 * math.sin(ph * 1.6))
            inten = (0.7 + 0.4 * breathe) * dim
            if self._speaking or self._state == "SPEAKING":
                inten += e * 0.7
            a0 = int(min(255, 235 * inten))
            g = QRadialGradient(bx, by, br)
            g.setColorAt(0.0, QColor(cr, cg, cb, a0))
            g.setColorAt(0.4, QColor(cr, cg, cb, int(a0 * 0.45)))
            g.setColorAt(1.0, QColor(cr, cg, cb, 0))
            p.setBrush(g)
            p.drawEllipse(QPointF(bx, by), br, br)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # glass specular highlight (top-left) — soft sheen
        hi = QRadialGradient(cx - r * 0.32, cy - r * 0.44, r * 0.85)
        hi.setColorAt(0.0, QColor(255, 255, 255, int(110 * dim)))
        hi.setColorAt(0.22, QColor(255, 255, 255, int(28 * dim)))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(hi)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.restore()

        # glass rim — bright at top, fading down (gradient pen)
        rim_g = QLinearGradient(cx, cy - r, cx, cy + r)
        rim_g.setColorAt(0.0, QColor(255, 255, 255, int(150 * dim)))
        rim_g.setColorAt(0.5, QColor(200, 200, 235, int(45 * dim)))
        rim_g.setColorAt(1.0, QColor(150, 160, 220, int(20 * dim)))
        rim = QPen(QBrush(rim_g), max(1.0, unit * 0.012))
        p.setPen(rim)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.end()
