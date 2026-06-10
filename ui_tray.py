"""ARIA's macOS menu-bar presence.

A QSystemTrayIcon that lives in the top-right menu bar (like Siri). A left
click pops ARIA out / tucks it away; a right click opens a small menu with
Show / Quit. The icon is the cyan pixel robot, rendered crisply from the same
sprite the buddy uses.
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtGui import QColor, QCursor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from ui_buddy import _BODY, _FACES, _PAL, THEMES


def robot_icon(height_px: int = 54) -> QIcon:
    """Render the cyan robot sprite (no glow) to a crisp, transparent icon."""
    gh, gw = len(_BODY), len(_BODY[0])
    ps = max(1, height_px // gh)
    pm = QPixmap(gw * ps, gh * ps)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    s, sd, fc = THEMES["cyan"]
    screen, screen_d, face = QColor(s), QColor(sd), QColor(fc)
    for ry, row in enumerate(_BODY):
        for cx, ch in enumerate(row):
            col = screen if ch == "S" else screen_d if ch == "s" else _PAL.get(ch)
            if col is not None:
                p.fillRect(cx * ps, ry * ps, ps, ps, col)
    f = _FACES["normal"]
    for (cx, ry) in f["eyes"] + f["mouth"]:
        p.fillRect(cx * ps, ry * ps, ps, ps, face)
    p.end()
    return QIcon(pm)


class AriaTray(QSystemTrayIcon):
    """Menu-bar status item. Left click toggles ARIA; right click shows a menu."""

    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
        on_show: Callable[[], None] | None = None,
        parent=None,
    ):
        super().__init__(robot_icon(), parent)
        self._on_toggle = on_toggle
        self._on_show = on_show or on_toggle
        self._on_quit = on_quit
        self.setToolTip("ARIA — click to summon")

        # Build a menu but DON'T setContextMenu(): on macOS that hijacks the
        # left click to open the menu. We pop it manually on right click so the
        # left click stays a Siri-style "summon".
        menu = QMenu()
        show_act = menu.addAction("Show App")
        show_act.triggered.connect(lambda: self._on_show())
        menu.addSeparator()
        quit_act = menu.addAction("Quit")
        quit_act.triggered.connect(lambda: self._on_quit())
        self._menu = menu

        self.activated.connect(self._activated)
        self.show()

    def _activated(self, reason) -> None:
        R = QSystemTrayIcon.ActivationReason
        if reason == R.Context:
            self._menu.popup(QCursor.pos())
        elif reason in (R.Trigger, R.DoubleClick, R.MiddleClick):
            self._on_toggle()
