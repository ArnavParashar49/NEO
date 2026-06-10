"""Siri-style slide-out overlay for ARIA (corner + margins configurable)."""

from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QColor, QKeySequence, QPainter, QPainterPath, QPen, QRegion, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui_hud_threejs import HAS_WEBENGINE, ThreeJSOrbCanvas
from ui_theme import C, RADIUS_M, ui_font
from ui_buddy import PixelBuddy

_CONFIG_FILE = Path(__file__).resolve().parent / "config" / "api_keys.json"
_ORB_SIZE = 72  # robot buddy — fits inside the 80px disc (no clipping, centred)
_DISC_SIZE = 80  # circular backdrop — orb may extend past it (clipped to disc)
_PANEL_ALPHA = 153  # ~60% opacity
_DISMISS_LEAD_MS = 2000
_DEFAULT_MARGIN_X = 14
_DEFAULT_MARGIN_Y = 36
_DEFAULT_CORNER = "top-right"
_SLIDE_MS = 300
_SLIDE_IN_MS = 480
_SLIDE_IN_MS_FAST = 260   # smooth glide-in (was a near-instant 140)
_SLIDE_OUT_MS = 240       # smooth fade+drift out
_SLIDE_IN_PX = 88
_EXPAND_MS = 380
_CAMERA_EXPAND_MS = 240
_CAMERA_SLIDE_IN_MS = 220
_COLLAPSE_MS = 380
_HIDE_AFTER_STANDBY_MS = 7000
_EXPANDED_W = 440
_EXPANDED_H = 400
_EXPANDED_RADIUS = 24
_MIN_PANEL_W = 360
_MIN_PANEL_H = 280


class _CircleBackdrop(QWidget):
    """Circular dim disc drawn snug behind the orb."""

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        inset = 0.5
        rect = QRectF(inset, inset, side - inset * 2, side - inset * 2)
        p.setPen(QPen(QColor(255, 255, 255, 72), 1.2))
        p.setBrush(QBrush(QColor(28, 28, 32, _PANEL_ALPHA)))
        p.drawEllipse(rect)


_PROMPTS = {
    "STANDBY": "",
    "LISTENING": "I'm listening…",
    "SPEAKING": "",  # transcript only — no label
    "THINKING": "Just a moment…",
    "INITIALISING": "Starting up…",
    "MUTED": "Microphone muted",
}

_VALID_CORNERS = frozenset(
    {"top-left", "top-right", "bottom-left", "bottom-right"}
)


def _load_siri_bar_layout() -> dict:
    layout = {
        "corner": _DEFAULT_CORNER,
        "margin_x": _DEFAULT_MARGIN_X,
        "margin_y": _DEFAULT_MARGIN_Y,
    }
    if not _CONFIG_FILE.exists():
        return layout
    try:
        raw = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        cfg = raw.get("siri_bar") or {}
        corner = str(cfg.get("corner", layout["corner"])).lower().strip()
        if corner in _VALID_CORNERS:
            layout["corner"] = corner
        layout["margin_x"] = max(0, int(cfg.get("margin_x", layout["margin_x"])))
        layout["margin_y"] = max(0, int(cfg.get("margin_y", layout["margin_y"])))
    except Exception:
        pass
    return layout


def _save_siri_bar_layout(layout: dict) -> None:
    try:
        data: dict = {}
        if _CONFIG_FILE.exists():
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        data["siri_bar"] = {
            "corner": layout.get("corner", _DEFAULT_CORNER),
            "margin_x": int(layout.get("margin_x", _DEFAULT_MARGIN_X)),
            "margin_y": int(layout.get("margin_y", _DEFAULT_MARGIN_Y)),
        }
        _CONFIG_FILE.write_text(
            json.dumps(data, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[SiriBar] Could not save position: {exc}")


class ParticleSphereWidget(QWidget):
    """White particle sphere on black — matches WebGL look; no logo image."""

    _POINTS = 1100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._angle_y = 0.0
        self._angle_x = 0.35
        self._energy = 0.06
        self._speaking = False
        self._muted = False
        self._state = "STANDBY"
        self._pts: list[tuple[float, float, float]] = []
        for _ in range(self._POINTS):
            phi = math.acos(2 * random.random() - 1)
            theta = random.random() * math.tau
            self._pts.append(
                (
                    math.sin(phi) * math.cos(theta),
                    math.cos(phi),
                    math.sin(phi) * math.sin(theta),
                )
            )

    def set_audio_bands(self, bands: list[float]) -> None:
        if not bands:
            return
        self._energy = min(1.0, max(0.05, (sum(bands) / max(len(bands), 1)) * 2.0))

    @property
    def speaking(self) -> bool:
        return self._speaking

    @speaking.setter
    def speaking(self, value: bool) -> None:
        self._speaking = value

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self._muted = value

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        self._state = value

    def _step(self) -> None:
        spin = 0.004 if self._muted or self._state == "STANDBY" else 0.009
        spin += self._energy * (0.018 if self._speaking else 0.01)
        self._angle_y += spin
        self._angle_x += 0.0025
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        scale = min(w, h) * 0.40
        cy_r, sy = math.cos(self._angle_y), math.sin(self._angle_y)
        cx_r, sx = math.cos(self._angle_x), math.sin(self._angle_x)
        push = 0.12 + self._energy * (0.35 if self._speaking else 0.18)

        for x, y, z in self._pts:
            x1 = x * cy_r + z * sy
            z1 = -x * sy + z * cy_r
            y2 = y * cx_r - z1 * sx
            z2 = y * sx + z1 * cx_r
            z_cam = z2 + 2.35
            if z_cam < 0.15:
                continue
            inv = 1.0 / z_cam
            px = cx + x1 * scale * inv
            py = cy + y2 * scale * inv
            depth = (z_cam - 1.2) / 1.4
            alpha = int(255 * max(0.35, min(1.0, 1.25 - depth * 0.85)))
            rad = max(0.75, (1.05 + push * 1.1) * inv * scale * 0.011)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(252, 252, 255, alpha)))
            p.drawEllipse(int(px - rad), int(py - rad), int(rad * 2), int(rad * 2))


class SiriOrbSlot(QWidget):
    """WebGL sphere when available; otherwise Qt particle sphere (never the ARIA logo)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget(self)
        lay.addWidget(self._stack)

        self._particles = PixelBuddy()
        self._particles.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._stack.addWidget(self._particles)
        self._web = None  # robot buddy replaces the WebGL / particle sphere

        self._stack.setCurrentWidget(self._particles)
        self._using_web = False

    def _on_web_loaded(self, ok: bool) -> None:
        if ok and self._web is not None:
            self._stack.setCurrentWidget(self._web)
            self._using_web = True
            print("[SiriBar] WebGL particle sphere active")
        else:
            print("[SiriBar] WebGL unavailable — using Qt particle sphere")

    @property
    def uses_webgl(self) -> bool:
        return self._using_web

    def _active(self):
        return self._web if self._using_web and self._web else self._particles

    def set_audio_bands(self, bands: list[float]) -> None:
        self._particles.set_audio_bands(bands)
        if self._web:
            self._web.set_audio_bands(bands)

    def _step(self) -> None:
        pass  # PixelBuddy self-animates via its own timer

    def set_hover(self, on: bool) -> None:
        self._particles.hover(on)

    def trigger_spin(self) -> None:
        self._particles.spin()

    @property
    def speaking(self) -> bool:
        return self._active().speaking

    @speaking.setter
    def speaking(self, value: bool) -> None:
        self._particles.speaking = value
        if self._web:
            self._web.speaking = value

    @property
    def muted(self) -> bool:
        return self._active().muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self._particles.muted = value
        if self._web:
            self._web.muted = value

    @property
    def state(self) -> str:
        return self._active().state

    @state.setter
    def state(self, value: str) -> None:
        self._particles.state = value
        if self._web:
            self._web.state = value


class SiriBarWindow(QWidget):
    """Frameless circular orb overlay."""

    open_full_window_requested = pyqtSignal()

    req_slide_in = pyqtSignal()
    req_slide_out = pyqtSignal()
    req_show_compact = pyqtSignal()
    req_toggle = pyqtSignal()
    req_cancel_hide = pyqtSignal()
    req_schedule_hide = pyqtSignal(int)
    req_apply_state = pyqtSignal(str)
    req_set_prompt = pyqtSignal(str)
    req_append_log = pyqtSignal(str)
    req_stream = pyqtSignal(str)
    req_stream_end = pyqtSignal(str, object)
    req_progress_start = pyqtSignal(int, str)
    req_set_activity = pyqtSignal(str)
    req_progress_stop = pyqtSignal()
    req_audio = pyqtSignal(object)
    req_collapse_panel = pyqtSignal()
    req_collapse_camera = pyqtSignal()

    _log_sig = pyqtSignal(str)
    _stream_sig = pyqtSignal(str)
    _stream_end_sig = pyqtSignal(str, object)
    _progress_start_sig = pyqtSignal(int, str)
    _progress_stop_sig = pyqtSignal()
    _prompt_sig = pyqtSignal(str)
    _expand_sig = pyqtSignal()
    _collapse_sig = pyqtSignal()

    def __init__(self, face_path: str = "", main_window=None, parent=None):
        super().__init__(parent)
        self._main_win = main_window
        self._layout = _load_siri_bar_layout()
        self._expanded = False
        self._panel_kind: str | None = None  # None | "main" | "camera"
        self._pending_camera: tuple[int, int] | None = None
        self.on_camera_session_ready = None  # callable(index, backend)
        self._speaking_active = False
        self._keep_orb_docked = False
        self._animating = False
        self._visible_target = False
        self._docked = False
        self._dragging = False
        self._drag_moved = False
        self._drag_offset = None
        self._force_compact = True
        self._prompt_locked = False
        self._orb_listen = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_hide_timer)
        self._dismiss_hint_timer = QTimer(self)
        self._dismiss_hint_timer.setSingleShot(True)
        # No caption text — dismiss hint is visual-only (orb stays as-is).
        self._anim: QPropertyAnimation | None = None
        self._fade_anim: QPropertyAnimation | None = None

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.setStyleSheet("background: transparent;")

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        from ui_theme import panel_card_compact_stylesheet

        self._panel_card = QFrame(self)
        self._panel_card.setObjectName("ariaPanelCard")
        self._panel_card.setStyleSheet(panel_card_compact_stylesheet())
        card_lay = QVBoxLayout(self._panel_card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        self._view_stack = QStackedWidget(self._panel_card)
        card_lay.addWidget(self._view_stack, stretch=1)
        self._outer.addWidget(self._panel_card, stretch=1)

        self._body_host = QWidget()
        self._body_lay = QVBoxLayout(self._body_host)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(0)
        self._view_stack.addWidget(self._body_host)

        self._stack_host = QWidget()
        self._stack_host.setFixedSize(_DISC_SIZE, _DISC_SIZE)
        self._stack_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        stack_wrap = QWidget()
        stack_wrap_lay = QVBoxLayout(stack_wrap)
        stack_wrap_lay.setContentsMargins(0, 0, 0, 0)
        stack_wrap_lay.addWidget(
            self._stack_host,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        self._view_stack.addWidget(stack_wrap)

        self._camera_shell = self._build_camera_shell()

        self._panel = _CircleBackdrop(self._stack_host)
        self._panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._panel.setGeometry(0, 0, _DISC_SIZE, _DISC_SIZE)
        self._panel.hide()  # no disc behind the robot buddy

        self._orb = SiriOrbSlot(self._stack_host)
        self._orb.setFixedSize(_ORB_SIZE, _ORB_SIZE)
        self._orb.setStyleSheet("background: transparent; border: none;")
        self._layout_orb()

        self._orb_timer = QTimer(self)
        self._orb_timer.timeout.connect(self._tick_orb)
        self._orb_timer.start(16)

        # single-click → spin (cancelled if a double-click arrives → expand)
        self._spin_click_timer = QTimer(self)
        self._spin_click_timer.setSingleShot(True)
        self._spin_click_timer.timeout.connect(self._orb.trigger_spin)

        # Hidden — typing opens via double-click → full window
        self._cmd = QLineEdit(self)
        self._cmd.hide()

        self._activity_row = QWidget(self)
        self._activity_row.hide()
        act_lay = QVBoxLayout(self._activity_row)
        act_lay.setContentsMargins(0, 0, 0, 0)
        self._progress_lbl = QLabel("")
        self._progress_lbl.hide()
        act_lay.addWidget(self._progress_lbl)
        self._progress_bar = QProgressBar()
        self._progress_bar.hide()
        act_lay.addWidget(self._progress_bar)

        self._expanded_wrap = QWidget(self)
        self._expanded_wrap.hide()
        from ui import LogWidget
        self._log = LogWidget(self._expanded_wrap)
        self._log.hide()

        self._progress_eta = 15
        self._progress_elapsed = 0.0
        self._progress_tick = QTimer(self)
        self._progress_tick.timeout.connect(self._tick_progress)

        self._log_sig.connect(self._log.append_log)
        # Live caption goes to the prompt only — not the hidden scroll log.
        self._progress_start_sig.connect(self._start_progress)
        self._progress_stop_sig.connect(self._stop_progress)
        self._prompt_sig.connect(self._set_prompt_text)
        self._expand_sig.connect(self._do_expand)
        self._collapse_sig.connect(self._do_collapse)

        self.req_slide_in.connect(self.slide_in)
        self.req_slide_out.connect(self.slide_out)
        self.req_show_compact.connect(self.show_compact)
        self.req_toggle.connect(self.toggle)
        self.req_cancel_hide.connect(self._cancel_hide)
        self.req_schedule_hide.connect(self.schedule_hide)
        self.req_apply_state.connect(self.apply_ui_state)
        self.req_set_prompt.connect(self.set_prompt_text)
        self.req_append_log.connect(self.append_log_instant)
        self.req_stream.connect(self.update_aria_stream)
        self.req_stream_end.connect(self.finish_aria_stream)
        self.req_progress_start.connect(self.start_progress)
        self.req_set_activity.connect(self.set_activity)
        self.req_progress_stop.connect(self.stop_progress)
        self.req_audio.connect(self._push_orb_bands)
        self.req_collapse_panel.connect(self._on_collapse_requested)
        self.req_collapse_camera.connect(self._on_camera_collapse_requested)

        self.on_text_command = None

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._request_mute_toggle)
        sc_collapse = QShortcut(QKeySequence("Escape"), self)
        sc_collapse.activated.connect(self.collapse_panel)
        self._mute_toggle_cb = None

        from ui_theme import expanded_shell_stylesheet

        self.setStyleSheet(expanded_shell_stylesheet())

        self._apply_disc_size()
        self._place_offscreen()
        self._update_window_mask()
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_window_mask()

    def _update_window_mask(self):
        if self._animating:
            self.clearMask()
            return
        if self._expanded:
            path = QPainterPath()
            path.addRoundedRect(
                QRectF(self.rect()),
                _EXPANDED_RADIUS,
                _EXPANDED_RADIUS,
            )
            self.setMask(QRegion(path.toFillPolygon().toPolygon()))
            return
        g = self.geometry()
        if (
            abs(g.width() - g.height()) > 3
            or abs(g.width() - _DISC_SIZE) > 3
            or abs(g.height() - _DISC_SIZE) > 3
        ):
            self.clearMask()
            return
        self.clearMask()   # robot buddy is square — no circular clip

    def _request_mute_toggle(self):
        if self._mute_toggle_cb:
            self._mute_toggle_cb()

    def blocks_wake(self) -> bool:
        return False

    def _set_orb_listening(self, listening: bool) -> None:
        """Text-only listening hint — orb and bar size stay fixed."""
        if self._orb_listen == listening:
            return
        self._orb_listen = listening
        self._sync_orb_state("LISTENING" if listening else "STANDBY")

    def _is_listening_caption(self, text: str) -> bool:
        t = (text or "").strip().lower()
        return t.startswith("i'm listening") or t == _PROMPTS["LISTENING"].lower()

    def _tick_orb(self):
        if hasattr(self._orb, "_step"):
            self._orb._step()

    def _push_orb_bands(self, bands):
        self._orb.set_audio_bands(bands if isinstance(bands, list) else [])

    def _sync_orb_state(self, state: str):
        self._orb.state = state
        self._orb.speaking = state == "SPEAKING"
        self._orb.muted = state == "MUTED"

    def _send_cmd(self):
        txt = self._cmd.text().strip()
        if not txt:
            return
        self._cmd.clear()
        self._hide_timer.stop()
        if self.on_text_command:
            import threading

            threading.Thread(
                target=self.on_text_command, args=(txt,), daemon=True
            ).start()

    def _on_stream_end(self, body: str, formatted):
        self._log.end_aria_stream(body, formatted if formatted else None)

    def _screen_rect(self) -> QRect:
        if self.isVisible():
            center = self.frameGeometry().center()
            screen = QApplication.screenAt(center)
            if screen is not None:
                return screen.availableGeometry()
        screen = QApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, 1440, 900)
        return screen.availableGeometry()

    def _clamp_geometry(self, rect: QRect) -> QRect:
        scr = self._screen_rect()
        w = max(1, rect.width())
        h = max(1, rect.height())
        x = max(scr.left() + 4, min(rect.x(), scr.right() - w - 4))
        y = max(scr.top() + 4, min(rect.y(), scr.bottom() - h - 4))
        return QRect(x, y, w, h)

    def _expanded_rect_from_orb(self, orb: QRect) -> QRect:
        """Grow a square panel from the orb, aligned to its dock corner."""
        corner = self._layout.get("corner", _DEFAULT_CORNER)
        w, h = _EXPANDED_W, _EXPANDED_H
        if corner.endswith("right"):
            x = orb.right() - w + 1
        else:
            x = orb.left()
        if corner.startswith("bottom"):
            y = orb.bottom() - h + 1
        else:
            y = orb.top()
        return self._clamp_geometry(QRect(x, y, w, h))

    def _orb_rect_from_panel(self, panel: QRect) -> QRect:
        """Compact orb rect anchored to the same corner as the expanded panel."""
        corner = self._layout.get("corner", _DEFAULT_CORNER)
        w, h = _DISC_SIZE, _DISC_SIZE
        if corner.endswith("right"):
            x = panel.right() - w + 1
        else:
            x = panel.left()
        if corner.startswith("bottom"):
            y = panel.bottom() - h + 1
        else:
            y = panel.top()
        return self._clamp_geometry(QRect(x, y, w, h))

    def _target_rect(
        self,
        offscreen: bool = False,
        expanded: bool = False,
        anchor: QRect | None = None,
    ) -> QRect:
        scr = self._screen_rect()
        corner = self._layout.get("corner", _DEFAULT_CORNER)
        mx = int(self._layout.get("margin_x", _DEFAULT_MARGIN_X))
        my = int(self._layout.get("margin_y", _DEFAULT_MARGIN_Y))
        w = _EXPANDED_W if expanded else _DISC_SIZE
        h = _EXPANDED_H if expanded else _DISC_SIZE

        if expanded and anchor is not None and not offscreen:
            return self._expanded_rect_from_orb(anchor)

        if corner.startswith("bottom"):
            y = scr.bottom() - h - my
        else:
            y = scr.top() + my
        y = max(scr.top() + 4, min(y, scr.bottom() - h - 4))

        if offscreen:
            if corner.endswith("right"):
                x = scr.right() + 8
            else:
                x = scr.left() - w - 8
        elif corner.endswith("right"):
            x = scr.right() - w - mx
        else:
            x = scr.left() + mx
        if not offscreen:
            x = max(scr.left() + 4, min(x, scr.right() - w - 4))
        return self._clamp_geometry(QRect(x, y, w, h))

    def _slide_in_start_rect(self) -> QRect:
        """Start offset along the dock edge — do not clamp X or the slide collapses to a pop-in."""
        end = self._target_rect(offscreen=False)
        corner = self._layout.get("corner", _DEFAULT_CORNER)
        w, h = _DISC_SIZE, _DISC_SIZE
        slide_px = max(_SLIDE_IN_PX, int(w * 0.9))
        x = end.x()
        y = end.top()
        if corner.endswith("right"):
            x = end.x() + slide_px
        else:
            x = end.x() - slide_px
        scr = self._screen_rect()
        y = max(scr.top() + 4, min(y, scr.bottom() - h - 4))
        return QRect(int(x), int(y), w, h)

    def _persist_layout_from_geometry(self) -> None:
        g = self.geometry()
        scr = self._screen_rect()
        cx, cy = g.center().x(), g.center().y()
        corner = (
            ("bottom" if cy > scr.center().y() else "top")
            + "-"
            + ("right" if cx > scr.center().x() else "left")
        )
        if corner.endswith("right"):
            margin_x = scr.right() - g.right()
        else:
            margin_x = g.left() - scr.left()
        if corner.startswith("bottom"):
            margin_y = scr.bottom() - g.bottom()
        else:
            margin_y = g.top() - scr.top()
        self._layout = {
            "corner": corner,
            "margin_x": max(0, margin_x),
            "margin_y": max(0, margin_y),
        }
        _save_siri_bar_layout(self._layout)
        print(f"[SiriBar] Position saved: {corner} margin_x={margin_x} margin_y={margin_y}")

    def _layout_orb(self) -> None:
        inset = (_DISC_SIZE - _ORB_SIZE) // 2
        self._orb.setGeometry(inset, inset, _ORB_SIZE, _ORB_SIZE)
        self._orb.raise_()

    def _apply_disc_size(self):
        from ui_theme import panel_card_compact_stylesheet

        self._panel_card.setStyleSheet(panel_card_compact_stylesheet())
        self._view_stack.setCurrentIndex(1)
        self._stack_host.setUpdatesEnabled(True)
        if not self._orb_timer.isActive():
            self._orb_timer.start(16)
        self.setObjectName("")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.setMinimumSize(_DISC_SIZE, _DISC_SIZE)
        self.setMaximumSize(_DISC_SIZE, _DISC_SIZE)
        self.setFixedSize(_DISC_SIZE, _DISC_SIZE)
        self._stack_host.setFixedSize(_DISC_SIZE, _DISC_SIZE)
        self._panel.setGeometry(0, 0, _DISC_SIZE, _DISC_SIZE)
        self._layout_orb()
        self._update_window_mask()

    def _apply_expanded_chrome(self, *, animating: bool = False) -> None:
        self._orb_timer.stop()
        self._stack_host.setUpdatesEnabled(False)
        self._view_stack.setCurrentIndex(0)
        self._body_host.setMinimumSize(
            _DISC_SIZE if animating else _MIN_PANEL_W,
            _DISC_SIZE if animating else _MIN_PANEL_H,
        )
        from ui_theme import expanded_shell_stylesheet, panel_card_stylesheet

        self._panel_card.setStyleSheet(panel_card_stylesheet())
        self.setObjectName("ariaExpandedShell")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(expanded_shell_stylesheet())
        if animating:
            self.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Preferred,
            )
            self.setMinimumSize(_DISC_SIZE, _DISC_SIZE)
            self.setMaximumSize(16777215, 16777215)
        else:
            self.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
            self.setFixedSize(_EXPANDED_W, _EXPANDED_H)
        self._update_window_mask()

    def _snap_expanded_geometry(self, orb_geo: QRect) -> None:
        end = self._expanded_rect_from_orb(orb_geo)
        self._apply_expanded_chrome(animating=False)
        self.setGeometry(end)
        self._outer.activate()
        self.updateGeometry()
        self._update_window_mask()

    def _stop_anim(self) -> None:
        if self._fade_anim:
            self._fade_anim.stop()
            self._fade_anim.deleteLater()
            self._fade_anim = None
        if not self._anim:
            return
        self._anim.stop()
        self._anim.deleteLater()
        self._anim = None
        self._animating = False

    def _on_collapse_requested(self) -> None:
        if self._expanded:
            self.collapse_panel()

    def _on_camera_collapse_requested(self) -> None:
        if self._expanded and self._panel_kind == "camera":
            self.req_collapse_camera.emit()

    def _build_camera_shell(self) -> QWidget:
        from ui import _FooterStrip
        from ui_theme import expanded_shell_stylesheet

        shell = QWidget()
        shell.setObjectName("ariaExpandedShell")
        shell.setStyleSheet(expanded_shell_stylesheet())
        lay = QVBoxLayout(shell)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 0)
        title = QLabel("ARIA")
        title.setStyleSheet(
            f"color: {C.TEXT}; background: transparent; {ui_font(18, bold=True)}"
        )
        header.addWidget(title)
        header.addStretch()
        dot = QLabel("●")
        dot.setStyleSheet(
            f"color: {C.LINK}; background: transparent; font-size: 10pt;"
        )
        header.addWidget(dot)
        cam_lbl = QLabel("Camera")
        cam_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; {ui_font(11)}"
        )
        header.addWidget(cam_lbl)
        lay.addLayout(header)

        section = QLabel("Live view")
        section.setStyleSheet(
            f"color: {C.TEXT_MED}; background: transparent; {ui_font(10, bold=True)}"
        )
        lay.addWidget(section)

        self._camera_video = QLabel("Starting camera…")
        self._camera_video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera_video.setMinimumHeight(200)
        self._camera_video.setStyleSheet(f"""
            QLabel {{
                background: {C.SURFACE2};
                color: {C.TEXT_DIM};
                border: 1px solid #2a2a30;
                border-radius: {RADIUS_M}px;
                padding: 8px;
            }}
        """)
        lay.addWidget(self._camera_video, stretch=1)

        self._camera_status_lbl = QLabel("ARIA is looking…")
        self._camera_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera_status_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; {ui_font(10)}"
        )
        lay.addWidget(self._camera_status_lbl)

        footer = _FooterStrip()
        footer.setFixedHeight(34)
        footer.setStyleSheet("background: transparent;")
        footer.collapse_requested.connect(self._on_camera_collapse_requested)
        foot_lay = QHBoxLayout(footer)
        foot_lay.setContentsMargins(4, 4, 4, 4)
        shrink = QPushButton("click here to shrink")
        shrink.setFlat(True)
        shrink.setCursor(Qt.CursorShape.PointingHandCursor)
        shrink.setStyleSheet(
            f"""
            QPushButton {{
                color: {C.TEXT_MED};
                background: transparent;
                border: none;
                text-align: left;
                padding: 2px 0;
                {ui_font(9)}
            }}
            QPushButton:hover {{ color: {C.TEXT}; }}
            """
        )
        shrink.clicked.connect(self._on_camera_collapse_requested)
        foot_lay.addWidget(shrink, stretch=1)
        sub = QLabel("Vision")
        sub.setStyleSheet(
            f"color: {C.PRI_GHO}; background: transparent; {ui_font(9)}"
        )
        sub.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        foot_lay.addWidget(sub)
        lay.addWidget(footer)
        return shell

    def is_camera_mode(self) -> bool:
        return self._panel_kind == "camera"

    def camera_video_label(self) -> QLabel:
        return self._camera_video

    def set_camera_panel_status(self, text: str) -> None:
        if text:
            self._camera_status_lbl.setText(text)

    def _fire_camera_session_ready(self) -> None:
        if not self._pending_camera or not self.on_camera_session_ready:
            return
        idx, backend = self._pending_camera
        self.on_camera_session_ready(idx, backend)

    def emerge_camera_session(self, camera_index: int, backend: int) -> bool:
        """
        Animate orb → camera panel. Returns False if caller should start capture
        immediately (full chat panel already open).
        """
        self._pending_camera = (camera_index, backend)
        if self._expanded and self._panel_kind == "main":
            return False
        if self._expanded and self._panel_kind == "camera":
            self._fire_camera_session_ready()
            return True

        self.cancel_scheduled_hide()
        self._keep_orb_docked = False
        self._visible_target = True

        def begin_expand() -> None:
            self.expand_camera_panel()

        if not self.isVisible():
            self._stop_anim()
            self._apply_disc_size()
            start = self._slide_in_start_rect()
            end = self._clamp_geometry(self._target_rect(offscreen=False))
            self.setGeometry(start)
            self.clearMask()
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
            self.show()
            self.raise_()

            def _after_slide() -> None:
                self._docked = True
                begin_expand()

            anim = self._run_geometry_anim(start, end, _CAMERA_SLIDE_IN_MS)
            anim.finished.connect(_after_slide)
        else:
            if not self._expanded:
                self._apply_disc_size()
            begin_expand()
        return True

    def expand_camera_panel(self) -> None:
        if self._panel_kind == "camera" and self._body_lay.count() > 0:
            self._fire_camera_session_ready()
            return
        if self._panel_kind == "main":
            return

        self.cancel_scheduled_hide()
        self._keep_orb_docked = False
        self._stop_anim()
        self._force_compact = False
        self._visible_target = True

        if self.isVisible():
            orb_geo = self._clamp_geometry(self.geometry())
        else:
            orb_geo = self._clamp_geometry(self._target_rect(offscreen=False))

        self._detach_body_content()
        self._apply_expanded_chrome(animating=True)
        self._body_lay.addWidget(self._camera_shell, stretch=1)
        self._expanded = True
        self._panel_kind = "camera"
        self._docked = False

        end = self._expanded_rect_from_orb(orb_geo)
        if not self.isVisible():
            self.show()
        self.setGeometry(orb_geo)
        self.clearMask()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        self.raise_()

        anim = self._run_geometry_anim(orb_geo, end, _CAMERA_EXPAND_MS)

        def _on_done() -> None:
            self._animating = False
            self._snap_expanded_geometry(orb_geo)
            self._docked = True
            self._fire_camera_session_ready()
            print("[SiriBar] Camera panel expanded")

        anim.finished.connect(_on_done)

    def _place_offscreen(self):
        self.setGeometry(self._target_rect(offscreen=True))
        self._visible_target = False

    def is_expanded(self) -> bool:
        return self._expanded

    def set_speaking_active(self, active: bool) -> None:
        self._speaking_active = bool(active)
        if active:
            self.cancel_scheduled_hide()

    def is_overlay_visible(self) -> bool:
        return self.isVisible() and self._visible_target

    def show_compact(self, *, fast: bool = True):
        """Slide in the orb overlay (never tears down an open panel)."""
        self._hide_timer.stop()
        if self._expanded:
            self.cancel_scheduled_hide()
            return
        self._force_compact = True
        self._slide_in_fast = fast
        self.slide_in()

    def toggle(self):
        """Tray click — pop ARIA out, or tuck it away if it's already out.

        Decides on intent (_visible_target / _expanded), never on isVisible(),
        which still reads True mid fade-out and caused the 'click does nothing'
        glitch.
        """
        self._hide_timer.stop()
        if self._expanded:
            self.collapse_panel(animate=True, on_finished=self._slide_out_compact)
            return
        if self._visible_target:
            self.slide_out()
        else:
            self.cancel_scheduled_hide()
            self.show_compact()

    def set_prompt_text(self, text: str):
        self._prompt_sig.emit(text)

    def slide_in(self):
        self._hide_timer.stop()
        self._keep_orb_docked = False
        self._visible_target = True
        if self._expanded:
            orb = self._clamp_geometry(self._target_rect(offscreen=False))
            self._apply_expanded_chrome()
            self.show()
            self.raise_()
            self._snap_expanded_geometry(orb)
            return

        end = self._clamp_geometry(self._target_rect(offscreen=False))
        self._apply_disc_size()

        if self._docked and self.isVisible() and self._visible_target and not self._expanded:
            self.setWindowOpacity(1.0)
            self.setGeometry(end)
            self.show()
            self.raise_()
            return

        self._stop_anim()
        start = self._slide_in_start_rect()
        self._docked = False
        self.clearMask()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        self.setWindowOpacity(0.0)
        self.setGeometry(start)
        self.show()
        self.raise_()
        app = QApplication.instance()
        if app:
            app.processEvents()
        slide_ms = _SLIDE_IN_MS_FAST if getattr(self, "_slide_in_fast", False) else _SLIDE_IN_MS
        anim = self._run_geometry_anim(
            start, end, slide_ms,
            easing=QEasingCurve.Type.OutCubic,
            fade=(0.0, 1.0),
        )
        anim.finished.connect(lambda: setattr(self, "_docked", True))

    def _on_hide_timer(self) -> None:
        if self._expanded or self._speaking_active:
            self.cancel_scheduled_hide()
            return
        self.slide_out()

    def slide_out(self):
        if not self.isVisible():
            return
        if self._speaking_active:
            return
        if self._expanded:
            if self._panel_kind == "camera":
                self.collapse_panel(animate=True)
            else:
                self.cancel_scheduled_hide()
            return
        self._slide_out_compact()

    def _slide_out_compact(self):
        if not self.isVisible():
            return
        self._visible_target = False
        self._docked = False
        self._prompt_sig.emit("")
        start = self.geometry()
        # soft exit: a small drift toward the dock edge + a fade to zero
        corner = self._layout.get("corner", _DEFAULT_CORNER)
        drift = 40 if corner.endswith("right") else -40
        end = QRect(start.x() + drift, start.y(), start.width(), start.height())
        anim = self._run_geometry_anim(
            start, end, _SLIDE_OUT_MS,
            easing=QEasingCurve.Type.InCubic,
            fade=(self.windowOpacity(), 0.0),
        )

        def _hide():
            self.hide()
            self.setWindowOpacity(1.0)   # reset so the next show isn't faint

        anim.finished.connect(_hide)

    def schedule_hide(self, delay_ms: int = _HIDE_AFTER_STANDBY_MS):
        if (
            self._expanded
            or self._speaking_active
            or self._keep_orb_docked
            or self._panel_kind == "camera"
        ):
            return
        self._hide_timer.stop()
        self._dismiss_hint_timer.stop()
        if delay_ms > 0:
            lead = min(_DISMISS_LEAD_MS, max(0, delay_ms - 400))
            hint_at = max(0, delay_ms - lead)
            self._dismiss_hint_timer.start(hint_at)
            self._hide_timer.start(delay_ms)
        else:
            self.slide_out()

    def cancel_scheduled_hide(self):
        self._hide_timer.stop()
        self._dismiss_hint_timer.stop()

    def _cancel_hide(self):
        self.cancel_scheduled_hide()

    def _is_at_dock(self) -> bool:
        if not self.isVisible() or not self._visible_target:
            return False
        end = self._target_rect(offscreen=False, expanded=self._expanded)
        g = self.geometry()
        return abs(g.x() - end.x()) <= 2 and abs(g.width() - end.width()) <= 4

    def toggle_expand(self) -> None:
        if self._expanded:
            self.collapse_panel()
        else:
            self.expand_panel()

    def expand_panel(self) -> None:
        if self._panel_kind == "camera":
            self.collapse_panel(animate=True, on_finished=self.expand_panel)
            return
        if self._expanded or not self._main_win:
            return
        if self._body_lay.count() > 0:
            return
        self.cancel_scheduled_hide()
        self._keep_orb_docked = False
        self._stop_anim()
        self._force_compact = False
        self._visible_target = True

        if self.isVisible():
            orb_geo = self._clamp_geometry(self.geometry())
        else:
            orb_geo = self._clamp_geometry(self._target_rect(offscreen=False))

        cw = self._main_win.takeCentralWidget()
        if cw is None:
            return

        self._apply_expanded_chrome(animating=True)
        self._body_lay.addWidget(cw, stretch=1)
        self._expanded = True
        self._panel_kind = "main"
        self._docked = False

        end = self._expanded_rect_from_orb(orb_geo)
        if not self.isVisible():
            self.show()
        self.setGeometry(orb_geo)
        self.clearMask()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        self.raise_()

        anim = self._run_geometry_anim(orb_geo, end, _EXPAND_MS)

        def _on_expand_done() -> None:
            self._animating = False
            self._snap_expanded_geometry(orb_geo)
            self._docked = True
            if self._main_win:
                self._main_win._style_mute_btn()

        anim.finished.connect(_on_expand_done)
        print(f"[SiriBar] Expanding to {_EXPANDED_W}x{_EXPANDED_H}")

    def _detach_main_panel(self) -> None:
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            panel = item.widget() if item else None
            if panel is not None:
                self._body_lay.removeWidget(panel)
                self._main_win.setCentralWidget(panel)
                return

    def _detach_camera_shell(self) -> None:
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                self._body_lay.removeWidget(widget)
        self._camera_video.clear()
        self._camera_video.setText("Starting camera…")
        self._camera_status_lbl.setText("ARIA is looking…")

    def _detach_body_content(self) -> None:
        if self._panel_kind == "main":
            self._detach_main_panel()
        elif self._panel_kind == "camera":
            self._detach_camera_shell()
        self._panel_kind = None

    def collapse_panel(self, animate: bool = True, on_finished=None) -> None:
        if not self._expanded:
            if on_finished:
                on_finished()
            return
        if self._panel_kind == "main" and not self._main_win:
            if on_finished:
                on_finished()
            return
        self.cancel_scheduled_hide()
        self._stop_anim()
        self._force_compact = True

        def _finish_compact() -> None:
            orb_end = self._orb_rect_from_panel(self.geometry())
            self._detach_body_content()
            self._apply_disc_size()
            if self._visible_target:
                self.setGeometry(orb_end)
                self.show()
                self.raise_()
                self._docked = True
            self._expanded = False
            self._update_window_mask()
            self._keep_orb_docked = True
            self.cancel_scheduled_hide()
            if self._main_win:
                self._main_win._style_mute_btn()
            if on_finished:
                on_finished()
            print("[SiriBar] Collapsed to orb")

        if animate and self.isVisible():
            self.setMinimumSize(_DISC_SIZE, _DISC_SIZE)
            self.setMaximumSize(16777215, 16777215)
            self.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Preferred,
            )
            start = self.geometry()
            end = self._orb_rect_from_panel(start)
            self._docked = False
            self.clearMask()
            anim = self._run_geometry_anim(start, end, _COLLAPSE_MS)
            anim.finished.connect(_finish_compact)
        else:
            _finish_compact()

    def expand(self):
        self.expand_panel()

    def allow_expand(self):
        return bool(self._main_win)

    def _do_expand(self):
        self.expand_panel()

    def _do_collapse(self):
        self.collapse_panel(animate=False)
        if self._expanded_wrap:
            self._expanded_wrap.hide()

    def _run_geometry_anim(
        self,
        start: QRect,
        end: QRect,
        duration: int,
        *,
        lock_x: bool = False,
        easing: QEasingCurve.Type = QEasingCurve.Type.InOutCubic,
        fade: tuple[float, float] | None = None,
    ):
        self._stop_anim()
        if lock_x and start.width() == end.width() and start.x() != end.x():
            end = QRect(start.x(), end.y(), start.width(), end.height())
        self._animating = True
        self.clearMask()

        def _on_anim_done():
            self._animating = False
            if not self._expanded:
                self._update_window_mask()

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(duration)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(easing)
        self._anim.finished.connect(_on_anim_done)

        # optional parallel opacity fade for a soft, non-abrupt transition
        if fade is not None:
            self.setWindowOpacity(fade[0])
            self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
            self._fade_anim.setDuration(duration)
            self._fade_anim.setStartValue(fade[0])
            self._fade_anim.setEndValue(fade[1])
            self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._fade_anim.start()

        self._anim.start()
        return self._anim

    def set_prompt_for_state(self, state: str):
        text = _PROMPTS.get(state, _PROMPTS["LISTENING"])
        self._prompt_sig.emit(text)

    def _set_prompt_text(self, text: str):
        """Orb-only overlay — text updates drive listening state, not labels."""
        t = (text or "").strip()
        listening = self._is_listening_caption(t)
        self._prompt_locked = listening
        self._set_orb_listening(listening)

    def set_activity(self, text: str):
        """Show the orb while working — no caption."""
        self._hide_timer.stop()
        if not (text or "").strip():
            return
        if not self._visible_target:
            self.show_compact()

    def append_log_instant(self, text: str):
        self._hide_timer.stop()
        if not self._visible_target:
            self.show_compact()

    def update_aria_stream(self, body: str):
        self._hide_timer.stop()
        if not (body or "").strip():
            return
        if not self._visible_target:
            self.show_compact()
        self._prompt_locked = False
        self._orb.speaking = True

    def finish_aria_stream(self, body: str, formatted):
        if not (body if body else formatted or "").strip():
            return
        self._prompt_locked = False
        self._orb.speaking = False

    def start_progress(self, eta_sec: int, label: str = ""):
        self._hide_timer.stop()
        if not self._visible_target:
            self.show_compact()
        self._progress_start_sig.emit(eta_sec, label or "Working…")

    def stop_progress(self):
        self._progress_stop_sig.emit()

    def _start_progress(self, eta_sec: int, label: str):
        self._progress_eta = max(8, eta_sec)
        self._progress_elapsed = 0.0
        self._progress_tick.start(500)

    def _tick_progress(self):
        self._progress_elapsed += 0.5

    def _stop_progress(self):
        self._progress_tick.stop()

    def apply_ui_state(self, state: str):
        self._sync_orb_state(state)
        if state == "SPEAKING":
            return
        if state == "STANDBY":
            self._set_orb_listening(False)
            return
        text = _PROMPTS.get(state, _PROMPTS["LISTENING"])
        if text:
            self._set_prompt_text(text)
        if state == "LISTENING":
            self._prompt_locked = True
            self._set_orb_listening(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_moved = False
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._docked = False
            if self._anim:
                self._anim.stop()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._dragging
            and self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            scr = self._screen_rect()
            g = self.geometry()
            pos = event.globalPosition().toPoint() - self._drag_offset
            x = max(scr.left(), min(pos.x(), scr.right() - g.width()))
            y = max(scr.top(), min(pos.y(), scr.bottom() - g.height()))
            if abs(x - g.x()) > 2 or abs(y - g.y()) > 2:
                self._drag_moved = True
            self.move(x, y)
        super().mouseMoveEvent(event)

    def enterEvent(self, event):
        self._orb.set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._orb.set_hover(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._drag_offset = None
            if self._drag_moved:
                self._persist_layout_from_geometry()
                self._docked = True
            elif not self._expanded:
                self._spin_click_timer.start(220)   # single click → spin
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._expanded:
            self._spin_click_timer.stop()   # don't spin on double-click
            self.toggle_expand()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
