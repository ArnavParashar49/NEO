"""ARIA's full window — a cozy, polished chat panel that matches the robot pet.

Clean message bubbles with soft shadows, a green/cream/slate palette cohesive
with the robot's CRT screen, a tidy header, and a circular send button. Built as
reusable widgets so they drop into the real app; previewed via ui_preview.py.
"""

from __future__ import annotations

import platform

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ui_buddy import PixelBuddy

# --- palette (cohesive with the cyan robot) --------------------------------
BG       = "#181d1c"   # slate-teal near-black
CARD_B   = "#2c3a38"   # card border
SURFACE  = "#222b2a"   # ARIA bubble / input
GREEN    = "#22a89c"   # (accent = cyan; names kept)
GREEN_D  = "#178a80"
GREEN_L  = "#6fe3d6"
CREAM    = "#ecd6a6"
TEXT     = "#eef3f2"
DIM      = "#9bada9"

_UI = ".AppleSystemUIFont" if platform.system() == "Darwin" else "Segoe UI"


def _f(size: int, bold: bool = False) -> QFont:
    f = QFont(_UI, size)
    f.setBold(bold)
    return f


def _shadow(w, blur=16, dy=4, alpha=110):
    s = QGraphicsDropShadowEffect(w)
    s.setBlurRadius(blur)
    s.setOffset(0, dy)
    s.setColor(QColor(0, 0, 0, alpha))
    w.setGraphicsEffect(s)


class _Bubble(QFrame):
    def __init__(self, text: str, who: str):
        super().__init__()
        self.setObjectName("bubble")
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setFont(_f(12))
        self._lab = lab
        lay = QVBoxLayout(self)
        lay.setContentsMargins(15, 10, 15, 11)
        lay.addWidget(lab)
        self.setMaximumWidth(252)
        if who == "you":
            lab.setStyleSheet("color:#ffffff; background:transparent; border:none;")
            self.setStyleSheet(f"QFrame#bubble {{ background:{GREEN_D}; border:none; border-radius:16px; }}")
        else:
            lab.setStyleSheet(f"color:{TEXT}; background:transparent; border:none;")
            self.setStyleSheet(f"QFrame#bubble {{ background:{SURFACE}; border:none; border-radius:16px; }}")
        _shadow(self, blur=14, dy=3, alpha=70)

    def set_text(self, text: str) -> None:
        self._lab.setText(text)


def _row(bubble: QFrame, who: str) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    if who == "you":
        lay.addStretch(1)
        lay.addWidget(bubble)
    else:
        lay.addWidget(bubble)
        lay.addStretch(1)
    return w


class RetroPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"""
            QFrame#card {{
                background: {BG};
                border: 1px solid {CARD_B};
                border-radius: 22px;
            }}
        """)
        _shadow(self, blur=50, dy=16, alpha=160)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(0)

        # --- header ---
        header = QHBoxLayout()
        header.setSpacing(12)
        pet = PixelBuddy()
        pet.setFixedSize(50, 50)
        header.addWidget(pet)
        tcol = QVBoxLayout()
        tcol.setSpacing(1)
        name = QLabel("ARIA")
        name.setFont(_f(16, bold=True))
        name.setStyleSheet(f"color:{TEXT}; letter-spacing:1px;")
        sub = QLabel("●  online")
        sub.setFont(_f(10))
        sub.setStyleSheet(f"color:{GREEN_L};")
        tcol.addWidget(name)
        tcol.addWidget(sub)
        header.addLayout(tcol)
        header.addStretch(1)
        root.addLayout(header)

        # divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background:{CARD_B}; border:none;")
        root.addSpacing(12)
        root.addWidget(div)
        root.addSpacing(10)

        # --- chat ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                             "QScrollBar:vertical{background:transparent;width:6px;}"
                             f"QScrollBar::handle:vertical{{background:{CARD_B};border-radius:3px;}}"
                             "QScrollBar::add-line,QScrollBar::sub-line{height:0;}")
        chat = QWidget()
        chat.setStyleSheet("background:transparent;")
        cl = QVBoxLayout(chat)
        cl.setContentsMargins(2, 2, 6, 2)
        cl.setSpacing(12)
        demo = [
            ("aria", "hey! i'm right here whenever you need me."),
            ("you", "open my downloads folder"),
            ("aria", "on it — opening Downloads now."),
            ("you", "what's the weather like?"),
            ("aria", "sunny and 24°  — a lovely day to head out."),
        ]
        for who, txt in demo:
            cl.addWidget(_row(_Bubble(txt, who), who))
        cl.addStretch(1)
        scroll.setWidget(chat)
        root.addWidget(scroll, stretch=1)

        # --- input ---
        root.addSpacing(12)
        inp_row = QHBoxLayout()
        inp_row.setSpacing(10)
        inp = QLineEdit()
        inp.setPlaceholderText("Message ARIA…")
        inp.setFont(_f(12))
        inp.setFixedHeight(44)
        inp.setStyleSheet(f"""
            QLineEdit {{
                background:{SURFACE}; color:{TEXT};
                border:1px solid {CARD_B}; border-radius:22px;
                padding:4px 16px;
            }}
            QLineEdit:focus {{ border:1px solid {GREEN}; }}
        """)
        send = QPushButton("↑")
        send.setFont(_f(17, bold=True))
        send.setFixedSize(44, 44)
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background:{GREEN}; color:#0e1a12;
                border:none; border-radius:22px;
            }}
            QPushButton:hover {{ background:{GREEN_L}; }}
            QPushButton:pressed {{ background:{GREEN_D}; }}
        """)
        inp_row.addWidget(inp, stretch=1)
        inp_row.addWidget(send)
        root.addLayout(inp_row)


class ChatView(QScrollArea):
    """Bubble chat — a drop-in for the old LogWidget.

    Implements the methods the app calls: append_log(text), update_aria_stream(body),
    end_aria_stream(body, formatted). All run on the GUI thread.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:6px;margin:2px;}"
            f"QScrollBar::handle:vertical{{background:{CARD_B};border-radius:3px;min-height:24px;}}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;}"
        )
        self._host = QWidget()
        self._host.setStyleSheet("background:transparent;")
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(4, 6, 8, 6)
        self._lay.setSpacing(11)
        self._lay.addStretch(1)
        self.setWidget(self._host)
        self._stream_bubble = None

    # --- internals ----------------------------------------------------------
    def _add(self, widget) -> None:
        self._lay.insertWidget(self._lay.count() - 1, widget)  # before the stretch
        QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self) -> None:
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _system(self, text: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(text)
        lab.setFont(_f(10))
        lab.setWordWrap(True)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setStyleSheet(f"color:{DIM}; background:transparent;")
        lay.addStretch(1)
        lay.addWidget(lab)
        lay.addStretch(1)
        return w

    # --- public API (matches LogWidget) ------------------------------------
    def append_log(self, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        low = t.lower()
        if low.startswith("you:"):
            self._add(_row(_Bubble(t.split(":", 1)[1].strip(), "you"), "you"))
        elif low.startswith("aria:"):
            self._add(_row(_Bubble(t.split(":", 1)[1].strip(), "aria"), "aria"))
        elif low.startswith("file:"):
            self._add(self._system("📎 " + t.split(":", 1)[1].strip()))
        else:
            self._add(self._system(t))

    def update_aria_stream(self, body: str) -> None:
        if not body:
            return
        if self._stream_bubble is None:
            self._stream_bubble = _Bubble(body, "aria")
            self._add(_row(self._stream_bubble, "aria"))
        else:
            self._stream_bubble.set_text(body)
            self._scroll_bottom()

    def end_aria_stream(self, body: str, formatted=None) -> None:
        text = (formatted if formatted is not None else body or "").strip()
        if self._stream_bubble is not None:
            if text:
                self._stream_bubble.set_text(text)
            self._stream_bubble = None
        elif text:
            self._add(_row(_Bubble(text, "aria"), "aria"))
        self._scroll_bottom()
