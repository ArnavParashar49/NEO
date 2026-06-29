"""Qt wrapper for the fluid voice orb (orbcore.OrbField).

Drop-in widget: ``set_audio_bands()``, ``.speaking``, ``.muted``, ``.state``.
"""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

from orbcore import OrbField

_NEO_TO_ORB = {
    "STANDBY": "idle",
    "MUTED": "idle",
    "LISTENING": "listening",
    "THINKING": "thinking",
    "INITIALISING": "thinking",
    "SPEAKING": "speaking",
}


class PrismOrb(QWidget):
    def __init__(self, parent=None, render_scale: float = 1.0):
        super().__init__(parent)
        self._render_scale = max(1.0, float(render_scale))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._field = OrbField(size=120, supersample=0.75)
        self._speaking = False
        self._muted = False
        self._state = "STANDBY"
        self._energy = 0.06
        self._energy_s = 0.06
        self._pulse_boost = 0.0
        self._frame_rgba = None
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._last_ms = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def set_audio_bands(self, bands) -> None:
        if self._speaking or self._state == "SPEAKING":
            return
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

    def set_hover(self, on: bool) -> None:
        if on:
            self._pulse_boost = min(1.0, self._pulse_boost + 0.35)
        else:
            self._pulse_boost = max(0.0, self._pulse_boost - 0.2)

    def trigger_pulse(self) -> None:
        self._pulse_boost = 1.0

    def _orb_state(self) -> str:
        if self._muted:
            return "idle"
        if self._speaking or self._state == "SPEAKING":
            return "speaking"
        return _NEO_TO_ORB.get(self._state, "idle")

    def _external_level(self, orb_state: str) -> float | None:
        if orb_state == "speaking":
            return None
        level = min(1.0, self._energy_s * 1.25 + self._pulse_boost * 0.25)
        if orb_state == "listening":
            return level
        return None

    def _tick(self) -> None:
        if not self.isVisible():
            return
        now = self._elapsed.elapsed()
        dt = max(0.0, min(0.05, (now - self._last_ms) / 1000.0))
        self._last_ms = now
        self._energy_s += (self._energy - self._energy_s) * 0.07
        self._pulse_boost *= 0.9

        orb_state = self._orb_state()
        self._field.set_state(orb_state)
        self._field.update(dt, external_level=self._external_level(orb_state))
        self._frame_rgba = self._field.render()
        self.update()

    def paintEvent(self, _event) -> None:
        if self._frame_rgba is None or self.width() <= 0 or self.height() <= 0:
            return
        h, w, _ = self._frame_rgba.shape
        if h <= 0 or w <= 0:
            return
        img = QImage(
            self._frame_rgba.data,
            w,
            h,
            4 * w,
            QImage.Format.Format_RGBA8888,
        ).copy()
        p = QPainter(self)
        if not p.isActive():
            return
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            if self.rect().isValid():
                target = QRectF(self.rect())
                if self._render_scale > 1.0:
                    extra_width = target.width() * (self._render_scale - 1.0)
                    extra_height = target.height() * (self._render_scale - 1.0)
                    target.adjust(
                        -extra_width / 2,
                        -extra_height / 2,
                        extra_width / 2,
                        extra_height / 2,
                    )
                p.drawImage(target, img)
        finally:
            p.end()
