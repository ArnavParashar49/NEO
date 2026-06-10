"""ARIA's desktop buddy — a little pixel robot with a green CRT-screen face.

The face (eyes + mouth) is drawn on the screen, so expressions animate like a
real digital display: blink, talk, happy. Drop-in for the orb — same interface
(set_audio_bands, .state, .speaking, .muted).
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QWidget

# --- palette ---------------------------------------------------------------
_PAL = {
    ".": None,
    "K": QColor("#181712"),   # black outline
    "W": QColor("#c6c6cf"),   # metal highlight
    "G": QColor("#9a9aa3"),   # metal
    "g": QColor("#5f5f68"),   # metal shadow
    "C": QColor("#ecd6a6"),   # cream bezel / panel
    "c": QColor("#cbab73"),   # cream shadow
    "S": QColor("#2f7d4f"),   # screen green
    "s": QColor("#22603c"),   # screen green dark
}
_FACE = QColor("#d6e457")     # lime face
_FACE_DIM = QColor("#9fae3e")

# screen + face themes (metal body stays the same).  name -> (screen, dark, face)
THEMES = {
    "green":  ("#2f7d4f", "#22603c", "#d6e457"),
    "blue":   ("#2f5fa6", "#214680", "#74c8ff"),
    "cyan":   ("#1f8f88", "#136460", "#8ff0e2"),
    "amber":  ("#b07320", "#7d4f15", "#ffd56b"),
    "purple": ("#6b3fae", "#4c2c80", "#d6a8ff"),
    "pink":   ("#b03f7e", "#7d2c59", "#fface0"),
    "red":    ("#b3433a", "#7e2c26", "#ffb27a"),
    "teal":   ("#1f8f6e", "#13644c", "#7df0c4"),
}

# robot body — blank green screen; face drawn separately (18 x 18)
_BODY = [
    "..KKKKKKKKKKKKK...",
    ".KWWWWWWWWWWWWWK..",
    ".KGGgCCCCCCCCCGK..",
    ".KGGgCSSSSSSSCGK..",
    "KKGGgCSSSSSSSCGK..",
    "KgGGgCSSSSSSSCGK..",
    "KKGGgCSSSSSSSCGK..",
    ".KGGgCSSSSSSSCGK..",
    ".KGGgCSSSSSSSCGK..",
    ".KGGgCSSSSSSSCGK..",
    ".KGGgCCCCCCCCCGK..",
    ".KKGGGGGGGGGGGKK..",
    "....KGGGGGGGK.....",
    "....KGCCCCCGK.....",
    "....KGCCCCCGK.....",
    "....KKGGGGGKK.....",
    ".....K.KKK.K......",
    ".....KKC.CKK......",
]

# screen interior: cols 6-12, rows 3-9
_FACES = {
    "normal": {"eyes": [(7, 5), (11, 5)],
               "mouth": [(7, 7), (8, 8), (9, 8), (10, 8), (11, 7)]},   # smile
    "blink":  {"eyes": [(7, 5), (11, 5)], "blink": True,
               "mouth": [(7, 7), (8, 8), (9, 8), (10, 8), (11, 7)]},
    "talk":   {"eyes": [(7, 5), (11, 5)],
               "mouth": [(8, 7), (9, 7), (10, 7), (8, 8), (9, 8), (10, 8)]},  # open
    "happy":  {"eyes": [(7, 5), (11, 5)],
               "mouth": [(7, 7), (8, 8), (9, 8), (10, 8), (11, 7), (8, 6), (10, 6)]},
}


class PixelBuddy(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._t = 0.0
        self._energy = 0.05
        self._energy_s = 0.05
        self._speaking = False
        self._muted = False
        self._state = "STANDBY"
        self._blink_until = 0.0
        self._next_blink = 1.8
        self._preview_pose = None
        self._screen = QColor("#1f8f88")        # cyan (default)
        self._screen_d = QColor("#136460")
        self._face = QColor("#8ff0e2")
        self._face_dim = QColor("#8ff0e2").darker(140)
        self._hover = False
        self._spin = None        # None = idle; else 0..1 progress of a 360
        self._facing = -1        # -1 = face left (mirrored), 1 = right
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(33)

    # --- interface ----------------------------------------------------------
    def set_audio_bands(self, bands) -> None:
        self._energy = 0.05 if not bands else min(1.0, max(0.05, (sum(bands) / max(len(bands), 1)) * 2.0))

    @property
    def speaking(self): return self._speaking
    @speaking.setter
    def speaking(self, v): self._speaking = bool(v)

    @property
    def muted(self): return self._muted
    @muted.setter
    def muted(self, v): self._muted = bool(v)

    @property
    def state(self): return self._state
    @state.setter
    def state(self, v): self._state = v or "STANDBY"

    def set_theme(self, name: str) -> None:
        if name in THEMES:
            s, sd, fc = THEMES[name]
            self._screen, self._screen_d = QColor(s), QColor(sd)
            self._face = QColor(fc)
            self._face_dim = QColor(fc).darker(140)

    # --- interaction --------------------------------------------------------
    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._spin is None:
            self._spin = 0.0     # kick off a 360 spin

    # --- animation ----------------------------------------------------------
    def _step(self) -> None:
        self._t += 0.033
        self._energy_s += (self._energy - self._energy_s) * 0.2
        if self._spin is not None:
            self._spin += 0.05
            if self._spin >= 1.0:
                self._spin = None
        if self._t >= self._next_blink and self._blink_until == 0.0:
            self._blink_until = self._t + 0.14
            self._next_blink = self._t + random.uniform(2.2, 5.5)
        if self._blink_until and self._t >= self._blink_until:
            self._blink_until = 0.0
        self.update()

    def _face_key(self) -> str:
        if self._preview_pose:
            return self._preview_pose
        if self._spin is not None or self._hover:
            return "happy"
        if self._blink_until:
            return "blink"
        if (self._speaking or self._state == "SPEAKING") and int(self._t * 6) % 2 == 0:
            return "talk"
        return "normal"

    # --- paint --------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        gh, gw = len(_BODY), len(_BODY[0])
        w, h = self.width(), self.height()
        ps = max(1, int(min(w / (gw + 1.5), h / (gh + 2))))
        sprite_w, sprite_h = gw * ps, gh * ps

        # gentle hover bob (robots float a touch)
        e = self._energy_s
        amp = 0.8 + (1.4 if self._state in ("LISTENING", "SPEAKING") else 0.0) + e * 2.0
        bob = math.sin(self._t * (3.2 if self._state == "STANDBY" else 4.6)) * amp

        # click spin (cos: 1 -> -1 -> 1 = a full turn) + a little hop
        spin_scale = math.cos(self._spin * math.tau) if self._spin is not None else 1.0
        hop = math.sin(self._spin * math.pi) * ps * 2.2 if self._spin is not None else 0.0

        ox = (w - sprite_w) / 2.0
        oy_ground = (h - sprite_h) / 2.0 - bob - ps
        oy = oy_ground - hop

        # shadow stays on the ground; shrinks when the robot lifts
        sh_w = sprite_w * (0.52 - bob * 0.008 - hop * 0.012)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 75))
        p.drawEllipse(QPointF(w / 2.0, oy_ground + sprite_h + ps * 0.4),
                      max(ps, sh_w / 2.0), ps * 0.9)

        dim = 0.9 if (self._muted or self._state == "STANDBY") else 1.0

        def cell(cx_, ry, col):
            c = col
            if dim < 1.0 and col is not None:
                c = QColor(int(col.red() * dim), int(col.green() * dim), int(col.blue() * dim))
            p.fillRect(int(ox + cx_ * ps), int(oy + ry * ps), ps + 1, ps + 1, c)

        # face-left (mirror) + spin around the vertical centre
        p.save()
        p.translate(w / 2.0, 0.0)
        p.scale(self._facing * spin_scale, 1.0)
        p.translate(-w / 2.0, 0.0)

        # body (S/s use the instance screen colour so themes work)
        for ry, row in enumerate(_BODY):
            for cx_, ch in enumerate(row):
                if ch == "S":
                    col = self._screen
                elif ch == "s":
                    col = self._screen_d
                else:
                    col = _PAL.get(ch)
                if col is not None:
                    cell(cx_, ry, col)

        # face (on the screen)
        face = _FACES[self._face_key()]
        eye_col = self._face if dim >= 1.0 else self._face_dim
        if face.get("blink"):
            for (cxp, ryp) in face["eyes"]:
                cell(cxp, ryp, self._face_dim)  # closed = single dim dash
        else:
            for (cxp, ryp) in face["eyes"]:
                cell(cxp, ryp, eye_col)
        for (cxp, ryp) in face["mouth"]:
            cell(cxp, ryp, eye_col)
        p.restore()
        p.end()
