"""Soft cyan/blue edge vignette while ARIA captures / analyzes the screen."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QRect
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication, QWidget


class ScreenBorderOverlay(QWidget):
    """Non-blocking light-red edge glow that fades inward."""

    def __init__(self, geometry: QRect, parent=None):
        super().__init__(parent)
        self._fade_depth = 80
        self._edge_color = QColor(0, 200, 255, 115)
        self._mid_color = QColor(0, 200, 255, 35)
        self._clear = QColor(0, 200, 255, 0)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setGeometry(geometry)

    def _fade_depth_for(self, w: int, h: int) -> int:
        return max(56, min(100, min(w, h) // 12))

    def _paint_edge_strip(
        self,
        p: QPainter,
        rect: QRect,
        start: QPointF,
        end: QPointF,
    ) -> None:
        grad = QLinearGradient(start, end)
        grad.setColorAt(0.0, self._edge_color)
        grad.setColorAt(0.45, self._mid_color)
        grad.setColorAt(1.0, self._clear)
        p.fillRect(rect, grad)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        r = self.rect()
        w, h = r.width(), r.height()
        d = self._fade_depth_for(w, h)

        # Top — strong at outer edge, fades down into the screen
        self._paint_edge_strip(
            p,
            QRect(0, 0, w, d),
            QPointF(0, 0),
            QPointF(0, d),
        )
        # Bottom
        self._paint_edge_strip(
            p,
            QRect(0, h - d, w, d),
            QPointF(0, h),
            QPointF(0, h - d),
        )
        # Left
        self._paint_edge_strip(
            p,
            QRect(0, 0, d, h),
            QPointF(0, 0),
            QPointF(d, 0),
        )
        # Right
        self._paint_edge_strip(
            p,
            QRect(w - d, 0, d, h),
            QPointF(w, 0),
            QPointF(w - d, 0),
        )


class ScreenBorderManager:
    """Soft red vignette on each connected display."""

    def __init__(self):
        self._overlays: list[ScreenBorderOverlay] = []

    def show(self) -> None:
        self.hide()
        app = QApplication.instance()
        if app is None:
            print("[ScreenBorder] ⚠️  No QApplication — border skipped")
            return
        screens = app.screens()
        if not screens:
            return
        for screen in screens:
            overlay = ScreenBorderOverlay(screen.geometry())
            overlay.show()
            overlay.raise_()
            self._overlays.append(overlay)
        app.processEvents()
        print(f"[ScreenBorder] 🔴 Soft vignette on {len(self._overlays)} display(s)")

    def hide(self) -> None:
        if not self._overlays:
            return
        for overlay in self._overlays:
            overlay.close()
            overlay.deleteLater()
        self._overlays.clear()
        print("[ScreenBorder] Vignette off")
