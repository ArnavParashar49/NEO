from __future__ import annotations

import json
import math
import os
import re
import platform
import random
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QFontDatabase,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QShortcut, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QMenu,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 440, 520
_MIN_W,     _MIN_H     = 400, 460

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"
_UI_FONT_FAMILY = ".AppleSystemUIFont" if _OS == "Darwin" else "Segoe UI"

from ui_theme import (
    C,
    RADIUS_L,
    RADIUS_M,
    RADIUS_S,
    _MONO_FONT,
    command_input_drag_stylesheet,
    embed_panel_stylesheet,
    expanded_shell_stylesheet,
    ghost_action_button_stylesheet,
    header_icon_button_stylesheet,
    icon_button_stylesheet,
    input_container_stylesheet,
    log_widget_stylesheet,
    mono_font,
    panel_card_stylesheet,
    pill_toolbar_button_stylesheet,
    primary_button_stylesheet,
    progress_bar_stylesheet,
    qcol,
    ui_font,
)
from ui_panel import ChatView


_INTERNAL_RESPONSE_HEADINGS = (
    "processing user input",
    "initiating sequential actions",
    "sequencing my approach",
    "analyzing user input",
    "analysis of user request",
    "analyzing mathematical expressions",
    "evaluating mathematical expressions",
    "confirming download absence",
    "reasoning",
)
_INTERNAL_HEADING_RE = re.compile(
    r"^(?:processing|analy[sz]ing|analysis|evaluating|reviewing|considering|"
    r"formulating|crafting|drafting|planning|sequencing|checking|verifying|"
    r"confirming|assessing|interpreting|examining|determining|calculating|"
    r"thinking|reasoning)\b",
    re.I,
)
_META_REASONING_MARKERS = (
    "i need to ",
    "i should ",
    "i must ",
    "i will now ",
    "i'll now ",
    "i am going to ",
    "the user asks",
    "the user wants",
    "the user's request",
    "the user's message",
    "i've just finished",
    "i have analyzed",
    "i've analyzed",
    "my response",
    "system context",
    "tool call",
    "internal instruction",
    "chain of thought",
)


def _response_heading(text: str) -> str:
    return (text or "").strip().lstrip("*#_ `").splitlines()[0].strip("*#_ `:.-")


def _has_internal_heading(text: str) -> bool:
    head = _response_heading(text)
    raw = (text or "").lower()
    known = any(
        heading.startswith(head.lower()) or head.lower().startswith(heading)
        for heading in _INTERNAL_RESPONSE_HEADINGS
        if len(head) >= 3
    )
    return known or bool(_INTERNAL_HEADING_RE.match(head)) and any(
        marker in raw for marker in _META_REASONING_MARKERS
    )


def _is_internal_model_text(text: str) -> bool:
    """Detect model meta-analysis that must never be rendered as an answer."""
    raw = (text or "").strip().lower()
    if not raw:
        return False
    if _has_internal_heading(text):
        return True
    strong_markers = (
        "i've analyzed the user's message",
        "my response incorporates a professional tone",
        "reflecting my identity",
        "cross-referenced the system context",
        "my mind palace",
        "identity & voice protocols",
        "i've streamlined the response",
        "the status check is answered concisely",
    )
    return (
        sum(marker in raw for marker in strong_markers) >= 2
        or sum(marker in raw for marker in _META_REASONING_MARKERS) >= 2
    )


def _normalize_user_facing_model_text(text: str) -> str:
    """Convert known reasoning leaks into the concise answer they contain."""
    value = (text or "").strip()
    raw = value.lower()
    if not value:
        return ""
    if (
        "mathematical expressions" in raw
        and ("resulted in" in raw or "yielded" in raw)
    ):
        first = re.search(r"resulted in\s+(-?\d+(?:\.\d+)?)", raw)
        second = re.search(r"yielded\s+(-?\d+(?:\.\d+)?)", raw)
        if first and second:
            left, right = first.group(1), second.group(1)
            relation = "equal" if left == right else "not equal"
            return f"The results are {left} and {right}, so they are {relation}."
    if _has_internal_heading(value):
        lines = value.splitlines()
        body = "\n".join(lines[1:]).strip()
        sentences = re.split(r"(?<=[.!?])\s+", body)
        user_facing = [
            sentence.strip()
            for sentence in sentences
            if sentence.strip()
            and not any(marker in sentence.lower() for marker in _META_REASONING_MARKERS)
        ]
        return " ".join(user_facing).strip()
    return value


class LogWidget(QTextEdit):
    _sig = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont(_MONO_FONT, 12))
        self.setStyleSheet(log_widget_stylesheet())
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)
        self._neo_stream_active = False
        self._neo_stream_start = 0

    def _ai_fmt(self, cur: QTextCursor):
        fmt = cur.charFormat()
        fmt.setForeground(QBrush(qcol(C.AI)))
        return fmt

    def begin_neo_stream(self):
        self._neo_stream_active = True
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self._neo_stream_start = cur.position()
        cur.insertText("Neo: ", self._ai_fmt(cur))
        self._neo_stream_start = cur.position()
        self.setTextCursor(cur)

    def update_neo_stream(self, body: str):
        if not body:
            return
        if not self._neo_stream_active:
            self.begin_neo_stream()
        cur = self.textCursor()
        cur.setPosition(self._neo_stream_start)
        cur.movePosition(
            cur.MoveOperation.End,
            cur.MoveMode.KeepAnchor,
        )
        cur.removeSelectedText()
        cur.insertText(body, self._ai_fmt(cur))
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def end_neo_stream(self, body: str, formatted: str | None = None):
        if not self._neo_stream_active:
            if body:
                self.append_log_instant(f"Neo: {formatted or body}")
            return
        text = (formatted if formatted is not None else body).strip()
        cur = self.textCursor()
        cur.setPosition(self._neo_stream_start)
        cur.movePosition(
            cur.MoveOperation.End,
            cur.MoveMode.KeepAnchor,
        )
        cur.removeSelectedText()
        if text:
            cur.insertText(text, self._ai_fmt(cur))
        cur.insertText("\n", self._ai_fmt(cur))
        self.setTextCursor(cur)
        self.ensureCursorVisible()
        self._neo_stream_active = False

    def append_log(self, text: str):
        self._sig.emit(text)

    def append_log_instant(self, text: str):
        """Show a full line immediately (no typewriter delay)."""
        tl = text.lower()
        if tl.startswith("you:"):
            tag = "you"
        elif tl.startswith("neo:"):
            tag = "ai"
        else:
            tag = "sys"
        cur = self.textCursor()
        fmt = cur.charFormat()
        col = {
            "you":  qcol(C.USER),
            "ai":   qcol(C.AI),
            "err":  qcol(C.RED),
            "file": qcol(C.GREEN),
            "sys":  qcol(C.ACC2),
        }.get(tag, qcol(C.TEXT))
        fmt.setForeground(QBrush(col))
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(text.strip() + "\n", fmt)
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("neo:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.AI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


_FILE_DIALOG_FILTER = (
    "All Files (*.*);;"
    "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
    "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
    "Data (*.csv *.xlsx *.json *.xml);;"
    "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
    "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
    "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
    "Archives (*.zip *.rar *.tar *.gz *.7z)"
)


class ChatInputBox(QTextEdit):
    returnPressed = Signal()
    focusChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Give it a baseline size so it matches the old QLineEdit
        self.setFixedHeight(40)
        self.textChanged.connect(self._adjust_height)

    def _adjust_height(self):
        doc_height = int(self.document().size().height())
        new_h = min(120, max(40, doc_height + 12))
        self.setFixedHeight(new_h)
        if doc_height > 108:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Return or e.key() == Qt.Key.Key_Enter:
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Insert newline
                super().keyPressEvent(e)
                # optionally adjust height here
            else:
                self.returnPressed.emit()
                e.accept()
                return
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focusChanged.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focusChanged.emit(False)


class CommandBar(QWidget):
    """Command input with attach and drag-and-drop."""

    file_selected = Signal(str)
    reset_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._current_file: str | None = None
        self._is_running = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._file_chip = QLabel("")
        self._file_chip.setStyleSheet(f"""
            color: {C.BLUE_L};
            background: rgba(59, 130, 246, 0.10);
            border: 1px solid rgba(59, 130, 246, 0.28);
            border-radius: {RADIUS_S}px;
            padding: 5px 12px;
            {ui_font(10)}
        """)
        self._file_chip.setVisible(False)
        self._file_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._file_chip.mouseReleaseEvent = self._chip_clicked
        root.addWidget(self._file_chip)

        self._pill_frame = QFrame()
        self._pill_frame.setObjectName("inputContainer")
        self._pill_frame.setStyleSheet(input_container_stylesheet(focused=False))

        pill_layout = QHBoxLayout(self._pill_frame)
        pill_layout.setContentsMargins(16, 4, 8, 4)
        pill_layout.setSpacing(6)

        self.line_edit = ChatInputBox()
        self.line_edit.setPlaceholderText("Ask AI anything…")
        self.line_edit.setFont(QFont(_UI_FONT_FAMILY, 12))
        self.line_edit.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                padding: 6px 0px;
            }}
        """)
        self.line_edit.focusChanged.connect(self._on_focus_changed)
        pill_layout.addWidget(self.line_edit, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_row.setContentsMargins(0, 0, 0, 0)

        attach = QPushButton("@")
        attach.setFixedSize(30, 30)
        attach.setToolTip("Attach a file")
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C.TEXT_MED};
                border: none;
                border-radius: 15px;
                {ui_font(13)}
            }}
            QPushButton:hover {{ color: {C.BLUE_L}; }}
        """)
        attach.clicked.connect(self._browse)
        btn_row.addWidget(attach)

        self.send_btn = QPushButton("↑")
        self.send_btn.setFixedSize(32, 32)
        self.send_btn.setFont(QFont(_UI_FONT_FAMILY, 14, QFont.Weight.Bold))
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.BLUE};
                color: #ffffff;
                border: none;
                border-radius: 16px;
            }}
            QPushButton:hover {{ background: {C.BLUE_L}; }}
            QPushButton:pressed {{ background: {C.BLUE_D}; }}
        """)
        self.send_btn.clicked.connect(self._on_send_btn_clicked)
        btn_row.addWidget(self.send_btn)

        pill_layout.addLayout(btn_row)
        root.addWidget(self._pill_frame)

    def _on_send_btn_clicked(self):
        if self._is_running:
            self.stop_requested.emit()
        else:
            self.line_edit.returnPressed.emit()

    @staticmethod
    def _icon_btn_style() -> str:
        return icon_button_stylesheet()

    def _get_util_button_style(self) -> str:
        return f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.04);
                color: {C.TEXT_MED};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                padding: 4px 10px;
                {ui_font(10)}
            }}
            QPushButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.08);
                color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.16);
            }}
            QPushButton:pressed {{
                background: rgba(255, 255, 255, 0.12);
            }}
        """

    def _get_menu_style(self) -> str:
        return f"""
            QMenu {{
                background: {C.SURFACE};
                color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 4px 0px;
            }}
            QMenu::item {{
                padding: 6px 16px;
                background: transparent;
            }}
            QMenu::item:selected {{
                background: rgba(255, 255, 255, 0.06);
                color: {C.TEXT};
            }}
        """

    def _get_container_style(self, focused: bool = False, dragging: bool = False) -> str:
        return input_container_stylesheet(focused=focused, dragging=dragging)

    def _on_focus_changed(self, focused: bool):
        self._pill_frame.setStyleSheet(
            input_container_stylesheet(focused=focused)
        )

    def set_input_placeholder(self, text: str):
        self.line_edit.setPlaceholderText(text)

    def _change_agent(self, agent_name: str):
        self._agent_btn.setText(f"👤 {agent_name}  ▾")

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None
        self._file_chip.setVisible(False)
        self._file_chip.setText("")

    def _chip_clicked(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clear_file()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach a file for NEO", str(Path.home()), _FILE_DIALOG_FILTER,
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        p = Path(path)
        cat = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        name = p.name if len(p.name) <= 36 else p.name[:33] + "…"
        self._file_chip.setText(f"{icon}  {name}  ·  {size}  ✕")
        self._file_chip.setVisible(True)
        self.file_selected.emit(path)

    def _pick_file_from_mime(self, mime: QMimeData) -> str | None:
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            path = url.toLocalFile()
            if path and Path(path).is_file():
                return path
        return None

    def dragEnterEvent(self, e: QDragEnterEvent):
        if self._pick_file_from_mime(e.mimeData()):
            e.acceptProposedAction()
            self._pill_frame.setStyleSheet(
                input_container_stylesheet(focused=True, dragging=True)
            )

    def dragLeaveEvent(self, e):
        self._pill_frame.setStyleSheet(input_container_stylesheet(focused=False))

    def dropEvent(self, e: QDropEvent):
        self._pill_frame.setStyleSheet(input_container_stylesheet(focused=False))
        path = self._pick_file_from_mime(e.mimeData())
        if path:
            self._set_file(path)
            e.acceptProposedAction()


class SetupOverlay(QWidget):
    done = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: {C.BG};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 18px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont(_UI_FONT_FAMILY, font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        from ui_buddy import PixelBuddy
        _av = PixelBuddy()
        _av.setFixedSize(62, 62)
        _av.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        _avrow = QHBoxLayout()
        _avrow.addStretch(1); _avrow.addWidget(_av); _avrow.addStretch(1)
        layout.addLayout(_avrow)
        layout.addWidget(_lbl("Let's set up NEO", 15, True))
        layout.addWidget(_lbl("Add your Gemini key and you're ready to go.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont(_UI_FONT_FAMILY, 12))
        self._key_input.setFixedHeight(36)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.SURFACE2}; color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.10); border-radius: 12px; padding: 8px 12px;
            }}
            QLineEdit:focus {{ border: 2px solid {C.BLUE}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont(_UI_FONT_FAMILY, 11))
            btn.setFixedHeight(34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("Start NEO  ↑")
        init_btn.setFont(QFont(_UI_FONT_FAMILY, 12, QFont.Weight.Bold))
        init_btn.setFixedHeight(42)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(primary_button_stylesheet())
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        for k, btn in self._os_btns.items():
            if k == key:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #3b82f6; color: #ffffff;
                        border: none; border-radius: 10px; font-weight: bold;
                    }
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.SURFACE}; color: {C.TEXT_DIM};
                        border: 1px solid rgba(255,255,255,0.08); border-radius: 10px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid rgba(255,255,255,0.14); }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class _FooterStrip(QWidget):
    """Double-click anywhere on the footer to collapse back to the orb."""
    collapse_requested = Signal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.collapse_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    _log_sig    = Signal(str)
    _state_sig  = Signal(str)
    _progress_start_sig = Signal(int, str)
    _progress_stop_sig  = Signal()
    _neo_stream_sig    = Signal(str)
    _neo_stream_end_sig = Signal(str, object)
    _neo_stream_cancel_sig = Signal()
    _tool_block_sig = Signal(str, str)
    panel_collapse_requested = Signal()

    def __init__(self, face_path: str = ""):
        super().__init__()
        self.setObjectName("neoExpandedShell")
        self.setStyleSheet(expanded_shell_stylesheet())
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Tool)
        self.setWindowTitle("NEO")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_stop_command  = None
        self.on_force_listen  = None
        self._manual_mute     = False
        self._standby         = False
        self._mic_live        = False
        self._current_file: str | None = None
        self._ui_state        = "INITIALISING"
        self._email_capture   = None  # holder dict while awaiting a typed email address

        central = QWidget()
        central.setObjectName("neoExpandedShell")
        central.setStyleSheet(expanded_shell_stylesheet())
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)

        self._card = QFrame()
        self._card.setObjectName("neoPanelCard")
        self._card.setStyleSheet(panel_card_stylesheet())
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 8)
        shadow.setColor(qcol("#000000", 120))
        self._card.setGraphicsEffect(shadow)

        root = QVBoxLayout(self._card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_main_panel(), stretch=1)
        outer.addWidget(self._card)

        self._log_sig.connect(self._log.append_log)
        self._tool_block_sig.connect(self._log.append_tool_block)
        self._neo_stream_sig.connect(self._log.update_neo_stream)
        self._neo_stream_end_sig.connect(self._on_neo_stream_end)
        self._neo_stream_cancel_sig.connect(self._log.cancel_neo_stream)
        self._state_sig.connect(self._apply_state)
        self._progress_start_sig.connect(self._start_search_progress)
        self._progress_stop_sig.connect(self._stop_search_progress)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay and self._overlay.isVisible():
            ow, oh = min(400, self.width() - 24), min(450, self.height() - 24)
            cw = self.centralWidget()
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )

    def _build_header(self) -> QWidget:
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        w.setFixedHeight(32)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 4, 8, 0)
        lay.setSpacing(0)
        lay.addStretch()

        btn = QPushButton("✕")
        btn.setFixedSize(28, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(header_icon_button_stylesheet())
        btn.setToolTip("Minimize")
        btn.clicked.connect(self.panel_collapse_requested.emit)
        lay.addWidget(btn)

        return w

    def _build_main_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background: {C.BG}; border-bottom-left-radius: {RADIUS_L}px; border-bottom-right-radius: {RADIUS_L}px;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 0, 14, 14)
        lay.setSpacing(8)

        self._log_progress_lbl = QLabel("")
        self._log_progress_lbl.setStyleSheet(
            f"color: {C.BLUE}; background: transparent; {ui_font(9)}"
        )
        self._log_progress_lbl.setVisible(False)
        lay.addWidget(self._log_progress_lbl)
        self._log_progress_bar = QProgressBar()
        self._log_progress_bar.setRange(0, 100)
        self._log_progress_bar.setValue(0)
        self._log_progress_bar.setFixedHeight(3)
        self._log_progress_bar.setTextVisible(False)
        self._log_progress_bar.setStyleSheet(progress_bar_stylesheet())
        self._log_progress_bar.setVisible(False)
        lay.addWidget(self._log_progress_bar)
        self._progress_eta = 15
        self._progress_elapsed = 0.0
        self._progress_tick = QTimer(self)
        self._progress_tick.timeout.connect(self._tick_search_progress)
        self._camera_embed = QLabel()
        self._camera_embed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera_embed.setMinimumHeight(160)
        self._camera_embed.setStyleSheet(embed_panel_stylesheet())
        self._camera_embed.hide()
        lay.addWidget(self._camera_embed, stretch=1)
        self._log = ChatView()
        self._log.retry_last.connect(self._on_retry_last)
        lay.addWidget(self._log, stretch=1)

        self._cmd = CommandBar()
        self._cmd.file_selected.connect(self._on_file_selected)
        self._cmd.line_edit.returnPressed.connect(self._send)
        self._cmd.stop_requested.connect(self._on_stop_requested)
        self._cmd.line_edit.textChanged.connect(self._scroll_log_to_bottom)
        lay.addWidget(self._cmd)

        return w

    def set_running_state(self, running: bool):
        self._cmd.set_running_state(running)

    def _on_stop_requested(self):
        if self.on_stop_command:
            import threading
            threading.Thread(target=self.on_stop_command, daemon=True).start()

    def _scroll_log_to_bottom(self):
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

    def _on_reset_requested(self):
        self._log.clear_view()
        self._log.append_log("System: Conversation reset.")

    def _on_retry_last(self):
        last = getattr(self._log, "_last_user", "") or ""
        if not last or not self.on_text_command:
            return
        import threading
        threading.Thread(target=self.on_text_command, args=(last,), daemon=True).start()


    def _build_right_panel(self) -> QWidget:
        return self._build_main_panel()



    def _start_search_progress(self, eta_sec: int, label: str = ""):
        self._progress_eta = max(8, eta_sec)
        self._progress_label = label or "Searching"
        self._progress_elapsed = 0.0
        self._log_progress_bar.setRange(0, 100)
        self._log_progress_bar.setValue(0)
        self._log_progress_lbl.setText(f"{self._progress_label} · ~{self._progress_eta}s")
        self._log_progress_lbl.setVisible(True)
        self._log_progress_bar.setVisible(True)
        self._progress_tick.start(500)

    def _tick_search_progress(self):
        self._progress_elapsed += 0.5
        pct = min(96, int(100 * self._progress_elapsed / self._progress_eta))
        left = max(0, int(self._progress_eta - self._progress_elapsed))
        self._log_progress_bar.setValue(pct)
        self._log_progress_lbl.setText(f"{getattr(self, '_progress_label', 'Searching')} · ~{left}s left")

    def _stop_search_progress(self):
        self._progress_tick.stop()
        self._log_progress_bar.setValue(100)
        self._log_progress_lbl.setVisible(False)
        self._log_progress_bar.setVisible(False)

    def set_camera_embed_active(self, active: bool) -> None:
        if active:
            self._camera_embed.show()
            self._log.hide()
        else:
            self._camera_embed.hide()
            self._log.show()

    def _on_file_selected(self, path: str):
        self._current_file = path

    def _sync_mic_ui(self):
        if self._manual_mute:
            self._apply_state("MUTED")
        elif self._standby:
            self._apply_state("STANDBY")
        elif self._mic_live:
            self._apply_state("LISTENING")

    def _toggle_mute(self):
        if (
            not self._manual_mute
            and not self._mic_live
            and not self._standby
            and self.on_force_listen
        ):
            self.on_force_listen()
            return
        self._manual_mute = not self._manual_mute
        self._sync_mic_ui()
        self._style_mute_btn()
        if self._manual_mute:
            print("[NEO] Microphone muted.")
        elif self._standby:
            print("[NEO] Standby — say 'Hey Neo' or clap twice.")
        else:
            print("[NEO] Microphone active.")

    def _style_mute_btn(self):
        if not hasattr(self, "_mute_btn"):
            return
        font = ui_font(10)
        radius = RADIUS_M
        if self._mic_live and not self._manual_mute and not self._standby:
            self._mute_btn.hide()
            return
        self._mute_btn.show()
        if self._manual_mute:
            self._mute_btn.setText("Microphone muted — tap to unmute")
            self._mute_btn.setStyleSheet(
                f"""
                QPushButton {{
                    border-radius: {radius}px;
                    {font}
                    background: rgba(248, 113, 113, 0.08);
                    color: {C.RED};
                    border: 1px solid rgba(248, 113, 113, 0.20);
                }}
                """
            )
        elif self._standby:
            self._mute_btn.hide()
            return
        else:
            self._mute_btn.setText("Mic off — tap to listen")
            self._mute_btn.setStyleSheet(
                f"""
                QPushButton {{
                    border-radius: {radius}px;
                    {font}
                    background: rgba(255, 255, 255, 0.04);
                    color: {C.TEXT_MED};
                    border: 1px solid rgba(255, 255, 255, 0.06);
                }}
                QPushButton:hover {{
                    background: rgba(255, 255, 255, 0.08);
                    color: {C.TEXT};
                }}
                """
            )

    def _set_status_ui(self, text: str, color: str):
        pass  # status lives in siri bar only — header stays clean

    def _apply_state(self, state: str):
        self._ui_state = state
        status_map = {
            "STANDBY": ("Standby — say Hey Neo", C.TEXT_MED),
            "LISTENING": ("Listening…", C.BLUE_L),
            "SPEAKING": ("Speaking…", C.BLUE),
            "THINKING": ("Working on it…", C.PURPLE),
            "MUTED": ("Microphone muted", C.RED),
            "INITIALISING": ("Starting up…", C.TEXT_MED),
        }
        text, color = status_map.get(state, (state.title(), C.TEXT_DIM))
        if self._manual_mute:
            text, color = "Microphone muted", C.RED
        elif self._standby and state == "STANDBY":
            text, color = "Standby — say Hey Neo", C.TEXT_MED
        self._set_status_ui(text, color)
        self._style_mute_btn()

    def begin_email_capture(self, holder: dict) -> None:
        """Arm the command input to capture the next line as an email address."""
        self._email_capture = holder
        self._log.append_log(f"NEO: {holder.get('prompt', 'Type the email address.')}")
        self._cmd.line_edit.setText(holder.get("prefill", ""))
        self._cmd.line_edit.setFocus()
        self._cmd.line_edit.selectAll()

    def _send(self):
        txt = self._cmd.line_edit.toPlainText().strip()
        file_path = self._cmd.current_file()
        
        if not txt and not file_path:
            return
            
        self._cmd.line_edit.clear()

        # If we're waiting for a typed email address, consume this line for that
        # instead of sending it to the model.
        cap = self._email_capture
        if cap is not None:
            self._email_capture = None
            self._log.append_log(f"You: {txt}")
            cap["result"] = txt
            cap["event"].set()
            return

        if file_path:
            p = Path(file_path)
            size = _fmt_size(p.stat().st_size)
            short_name = p.name if len(p.name) < 15 else f"file{p.suffix}"
            # Prepend file context
            file_context = (
                f"[FILE_UPLOADED] path={file_path} | name={short_name} | "
                f"type={p.suffix.lstrip('.')} | size={size}\n"
            )
            if txt:
                self._log.append_log(f"You: [Attached {p.name}] {txt}")
                txt = file_context + f"User message: {txt}"
            else:
                self._log.append_log(f"You: [Attached {p.name}]")
                txt = file_context + f"Briefly tell the user you can see the file '{p.name}' ({size}) has been uploaded and ask what they'd like to do with it."
            self._cmd.clear_file()
        else:
            self._log.append_log(f"You: {txt}")

        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _on_neo_stream_end(self, body: str, formatted):
        self._log.end_neo_stream(body, formatted if formatted else None)

    def _check_config(self) -> bool:
        try:
            from config import get_api_key, get_os

            return bool(get_api_key("gemini_api_key", required=False)) and bool(get_os())
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = min(400, cw.width() - 24), min(450, cw.height() - 24)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        from config import set_api_key, set_env_var

        set_api_key("gemini_api_key", key)
        set_env_var("OS_SYSTEM", os_name)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._sync_mic_ui()
        self._style_mute_btn()
        print(f"[NEO] Initialised. OS={os_name.upper()}.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


def _siri_overlay_enabled() -> bool:
    if not API_FILE.exists():
        return True
    try:
        d = json.loads(API_FILE.read_text(encoding="utf-8"))
        return bool(d.get("siri_overlay", True))
    except Exception:
        return True


def _hide_dock_icon() -> None:
    """Hide the app from the macOS Dock and Windows Taskbar."""
    if platform.system() == "Darwin":
        try:
            import AppKit
            AppKit.NSApp.setActivationPolicy_(1)
            print("[Tray] Dock icon hidden successfully via AppKit")
        except Exception as e:  # pragma: no cover - best effort
            print(f"[Tray] could not hide Dock icon: {e}")
    elif platform.system() == "Windows":
        try:
            import ctypes
            # Hide the console window on Windows if it exists
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception as e:
            print(f"[Tray] could not hide console window: {e}")


class _UiDispatcher(QObject):
    """Marshals UI work onto the Qt main thread."""
    hide_main = Signal()
    show_screen_border = Signal()
    hide_screen_border = Signal()
    begin_camera_session = Signal(int, int)  # camera_index, backend
    hide_camera_preview = Signal()
    set_camera_status = Signal(str)
    request_email = Signal(object)  # holder; show main window + capture typed address
    show_command_response = Signal(str)
    show_install_confirmation = Signal(str, str, str)


class NeoUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        # Menu-bar app: don't quit when the (hidden) windows close; no Dock icon.
        self._app.setQuitOnLastWindowClosed(False)
        _hide_dock_icon()
        self._tray = None
        self._face_path = face_path
        self._siri_mode = _siri_overlay_enabled()
        self._dispatch = _UiDispatcher()
        self._win = MainWindow(face_path)
        self._siri = None
        self._spotlight = None
        from ui_screen_border import ScreenBorderManager
        from ui_camera_preview import CameraPreviewManager

        self._screen_border = ScreenBorderManager()
        self._camera_preview = CameraPreviewManager(
            self._camera_embed_target,
            self._camera_embed_release,
        )
        self._dispatch.hide_main.connect(self._win.hide)
        self._dispatch.show_screen_border.connect(
            self._screen_border.show,
            Qt.ConnectionType.BlockingQueuedConnection,
        )
        self._dispatch.hide_screen_border.connect(self._screen_border.hide)
        self._dispatch.begin_camera_session.connect(
            self._begin_camera_session_main,
            Qt.ConnectionType.BlockingQueuedConnection,
        )
        self._dispatch.hide_camera_preview.connect(
            self._end_camera_session_main,
            Qt.ConnectionType.BlockingQueuedConnection,
        )
        self._dispatch.set_camera_status.connect(
            self._set_camera_status_main,
            Qt.ConnectionType.QueuedConnection,
        )
        self._dispatch.request_email.connect(
            self._request_email_main,
            Qt.ConnectionType.QueuedConnection,
        )
        self._dispatch.show_command_response.connect(
            self._show_command_response_main,
            Qt.ConnectionType.QueuedConnection,
        )
        self._dispatch.show_install_confirmation.connect(
            self._show_install_confirmation_main,
            Qt.ConnectionType.QueuedConnection,
        )
        if self._siri_mode:
            self._init_siri_bar()
            if self._win._ready:
                self._win.hide()
            else:
                self._win.show()
        else:
            self._win.show()
        self._init_tray()
        self.root = _RootShim(self._app)

    def _init_tray(self) -> None:
        """Add the macOS menu-bar status item (the robot)."""
        try:
            from ui_tray import NeoTray
            self._tray = NeoTray(
                on_toggle=self._tray_toggle,
                on_show=self._tray_show,
                on_quit=self._tray_quit,
            )
            self._tray.show()
        except Exception as e:  # pragma: no cover - best effort
            print(f"[Tray] unavailable: {e}")
            self._tray = None

    def _tray_show(self) -> None:
        """Pop NEO out (menu 'Show NEO')."""
        if self._siri and self._siri_mode:
            self._siri.req_cancel_hide.emit()
            self._siri.req_show_compact.emit()
        else:
            self._win.show()
            self._win.raise_()
            self._win.activateWindow()

    def _tray_toggle(self) -> None:
        """Left click — pop NEO out, or tuck it away if already showing."""
        if self._siri and self._siri_mode:
            self._siri.req_toggle.emit()
        elif self._win.isVisible():
            self._win.hide()
        else:
            self._win.show()
            self._win.raise_()
            self._win.activateWindow()

    def _tray_quit(self) -> None:
        self._app.quit()

    def _init_siri_bar(self):
        if self._siri or not self._siri_mode:
            return
        from ui_siri_bar import SiriBarWindow

        self._siri = SiriBarWindow(self._face_path, main_window=self._win)
        self._siri.on_text_command = self._win.on_text_command
        self._siri._mute_toggle_cb = self._win._toggle_mute
        self._siri.on_camera_session_ready = self._on_camera_session_ready
        self._siri.req_collapse_camera.connect(self._on_camera_panel_collapse)
        self._siri.spotlight_toggle_requested.connect(self._toggle_spotlight_overlay)
        self._win.panel_collapse_requested.connect(self._request_siri_collapse)

        from spotlight_overlay import SpotlightAssistantOverlay

        self._spotlight = SpotlightAssistantOverlay(
            on_submit=self._submit_spotlight_text,
            can_submit=lambda: self._win.on_text_command is not None,
            on_closed=self._restore_orb_after_spotlight,
        )

    def _toggle_spotlight_overlay(self) -> None:
        if not self._siri or not self._spotlight:
            return
        self._siri.req_cancel_hide.emit()
        opening = not self._spotlight.isVisible()
        if opening:
            self._spotlight.set_orb_state(self._win._ui_state)
        self._spotlight.toggle_from_orb(self._siri.frameGeometry())
        if opening:
            self._siri.set_spotlight_suppressed(True)

    def show_command_response(self, text: str) -> None:
        """Open Spotlight and render a command response on the Qt thread."""
        if text and text.strip():
            self._dispatch.show_command_response.emit(text.strip())

    def _show_command_response_main(self, text: str) -> None:
        if not self._siri or not self._spotlight:
            self.write_log(f"Neo: {text}")
            return
        if not self._spotlight.owns_chat_surface():
            self._siri.req_cancel_hide.emit()
            self._spotlight.set_orb_state(self._win._ui_state)
            self._spotlight.open_from_orb(self._siri.frameGeometry())
            self._siri.set_spotlight_suppressed(True)
        self._spotlight.results.clear_messages()
        self._spotlight.finish_assistant(text)

    def show_install_confirmation(self, title: str, source: str, command: str) -> None:
        self._dispatch.show_install_confirmation.emit(title, source, command)

    def _show_install_confirmation_main(
        self, title: str, source: str, command: str
    ) -> None:
        if not self._siri or not self._spotlight:
            self.write_log(f"Neo: Install {title}? Command: {command}")
            return
        if not self._spotlight.owns_chat_surface():
            self._siri.req_cancel_hide.emit()
            self._spotlight.set_orb_state(self._win._ui_state)
            self._spotlight.open_from_orb(self._siri.frameGeometry())
            self._siri.set_spotlight_suppressed(True)
        self._spotlight.show_install_confirmation(title, source, command)

    def _spotlight_owns_surface(self) -> bool:
        return bool(self._spotlight and self._spotlight.owns_chat_surface())

    def _restore_orb_after_spotlight(self) -> None:
        if not self._siri:
            return
        self._siri.set_spotlight_suppressed(False)
        self._siri.show()
        self._siri.raise_()
        self._siri.activateWindow()
        self._siri.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _submit_spotlight_text(self, text: str) -> bool:
        """Mirror the user line to history, then use the existing backend callback."""
        callback = self._win.on_text_command
        if not callback:
            return False
        accepted = callback(text)
        if accepted is False:
            return False
        self._win._log_sig.emit(f"You: {text}")
        return True

    def _begin_camera_session_main(self, camera_index: int, backend: int) -> None:
        """Main thread — start capture immediately, animate panel in parallel."""
        if not self._camera_preview.is_active():
            self._camera_preview.start(camera_index, backend, defer_display=True)
        if self._siri and self._siri.emerge_camera_session(camera_index, backend):
            return
        self._camera_preview.attach_display()

    def _end_camera_session_main(self) -> None:
        """Main thread only — stop capture and collapse camera panel."""
        self._camera_preview.hide()
        if self._siri and self._siri.is_camera_mode():
            self._siri.collapse_panel(animate=True)

    def _set_camera_status_main(self, text: str) -> None:
        if self._siri and self._siri.is_camera_mode():
            self._siri.set_camera_panel_status(text)
        if self._camera_preview:
            self._camera_preview.set_status(text)

    def _on_camera_session_ready(self, camera_index: int, backend: int) -> None:
        if not self._camera_preview.is_active():
            self._camera_preview.start(camera_index, backend, defer_display=True)
        self._camera_preview.attach_display()

    def _on_camera_panel_collapse(self) -> None:
        self._dispatch.hide_camera_preview.emit()

    def _request_siri_collapse(self) -> None:
        if self._siri:
            self._siri.req_collapse_panel.emit()

    def notify(self, text: str) -> None:
        """Thread-safe: post a status line to the conversation panel.

        Visual feedback that does not depend on TTS (free-tier voice quota can
        run out), so the user always sees what NEO is doing.
        """
        try:
            self._win._log_sig.emit(text)
        except Exception:
            pass

    # --- email address capture (reuses the main window, no extra dialog) ----
    def _request_email_main(self, holder: dict) -> None:
        """Main thread: grow the siri bar into the full panel, then arm capture."""
        try:
            if self._siri and self._siri_mode:
                if not self._siri.is_expanded():
                    self._siri.expand_panel()  # animates orb → full panel
                self._siri.raise_(); self._siri.activateWindow()
            else:
                self._win.show(); self._win.raise_(); self._win.activateWindow()
            self._win.begin_email_capture(holder)
        except Exception as e:
            print(f"[UI] email capture failed: {e}")
            holder["result"] = None
            holder["event"].set()

    def ask_email_address(self, prompt: str = "", prefill: str = "") -> str | None:
        """Worker-thread safe: ask for the recipient in the main window's input.

        Shows the window (as on siri-bar double-click), posts `prompt` in the
        conversation, and returns the next line the user types — or None on
        timeout/cancel. The AI composes the actual email; this is only the address.
        """
        import threading

        holder: dict = {
            "event": threading.Event(),
            "result": None,
            "prompt": prompt or "Who should I email? Type the email address.",
            "prefill": prefill or "",
        }
        self._dispatch.request_email.emit(holder)
        holder["event"].wait(timeout=180)
        return holder["result"]

    @property
    def manual_mute(self) -> bool:
        return self._win._manual_mute

    @property
    def muted(self) -> bool:
        return self._win._manual_mute or self._win._standby

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._manual_mute:
            self._win._toggle_mute()

    def set_standby(self, standby: bool):
        self._win._standby = standby
        self._win._sync_mic_ui()
        self._win._style_mute_btn()
        if self._siri and standby:
            self._siri.req_apply_state.emit("STANDBY")

    def set_mic_live(self, live: bool):
        self._win._mic_live = live
        self._win._sync_mic_ui()
        self._win._style_mute_btn()

    @property
    def current_file(self) -> str | None:
        return self._win._cmd.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb
        if self._siri:
            self._siri.on_text_command = cb

    @property
    def on_force_listen(self):
        return self._win.on_force_listen

    @on_force_listen.setter
    def on_force_listen(self, cb):
        self._win.on_force_listen = cb

    def set_speaking_active(self, active: bool) -> None:
        if self._spotlight:
            self._spotlight.set_orb_speaking(active)
        if self._siri:
            self._siri.set_speaking_active(active)

    def siri_wake(self):
        if self._spotlight_owns_surface():
            return
        if self._siri:
            self._siri.req_cancel_hide.emit()
            if self._siri.is_expanded():
                return
            self._siri.req_show_compact.emit()

    def siri_schedule_hide(self, delay_ms: int = 7000) -> None:
        """Slide orb away after idle (default 7s)."""
        if self._spotlight_owns_surface():
            return
        if self._siri and not self._siri.is_expanded():
            self._siri.req_schedule_hide.emit(max(0, int(delay_ms)))

    def siri_hide_now(self) -> None:
        if self._spotlight_owns_surface():
            return
        if self._siri:
            self._siri.req_schedule_hide.emit(0)

    def siri_cancel_hide(self) -> None:
        if self._siri:
            self._siri.req_cancel_hide.emit()

    def siri_set_prompt(self, text: str):
        if self._spotlight_owns_surface():
            if self._spotlight and text.strip():
                self._spotlight.set_status(text)
            return
        if self._siri:
            self._siri.req_set_prompt.emit(text)

    def siri_blocks_wake(self) -> bool:
        if self._spotlight_owns_surface():
            return True
        if self._siri:
            return self._siri.blocks_wake()
        return False

    def show_screen_border(self) -> None:
        """Red outline on all displays while screen vision runs."""
        self._dispatch.show_screen_border.emit()

    def hide_screen_border(self) -> None:
        self._dispatch.hide_screen_border.emit()

    def show_camera_preview(self, camera_index: int = 0, backend: int = 0) -> None:
        """Orb emerges into the camera panel, then the live feed starts (main thread)."""
        self._dispatch.begin_camera_session.emit(camera_index, backend)

    def hide_camera_preview(self) -> None:
        self._dispatch.hide_camera_preview.emit()

    def set_camera_status(self, text: str) -> None:
        self._dispatch.set_camera_status.emit(text)

    def get_camera_preview(self):
        return self._camera_preview

    def _camera_embed_target(self):
        """Feed target: dedicated camera panel, or conversation embed if chat is open."""
        if self._siri:
            if self._siri.is_camera_mode():
                return self._siri.camera_video_label()
            if self._siri.is_expanded():
                self._win.set_camera_embed_active(True)
                return self._win._camera_embed
        return None

    def _camera_embed_release(self) -> None:
        if self._siri and self._siri.is_camera_mode():
            return
        self._win.set_camera_embed_active(False)

    def set_state(self, state: str):
        self._win._state_sig.emit(state)
        if self._spotlight:
            self._spotlight.set_orb_state(state)
        if self._siri:
            self._siri.req_apply_state.emit(state)
            if state in ("SPEAKING", "THINKING"):
                self._siri.req_cancel_hide.emit()
            elif state == "STANDBY" and not self._siri.is_expanded():
                self.siri_hide_now()

    def stream_neo(self, body: str):
        """Update the live NEO line while she is speaking."""
        if not body or not body.strip():
            return
        if _is_internal_model_text(body):
            self.cancel_neo_stream()
            return
        self.stop_log_progress()
        t = body.strip()
        self._win._neo_stream_sig.emit(t)
        if self._spotlight:
            self._spotlight.stream_assistant(t)
        if self._siri:
            self._siri.req_stream.emit(t)

    def finish_neo_stream(self, body: str):
        """Finalize the live line with optional list formatting."""
        if not body or not body.strip():
            return
        body = _normalize_user_facing_model_text(body)
        if not body or _is_internal_model_text(body):
            self.cancel_neo_stream()
            return
        self.stop_log_progress()
        self._win._neo_stream_end_sig.emit(body.strip(), None)
        if self._spotlight:
            self._spotlight.finish_assistant(body.strip())
        if self._siri:
            self._siri.req_stream_end.emit(body.strip(), None)

    def cancel_neo_stream(self) -> None:
        """Discard pre-tool narration while preserving the final tool result."""
        self._win._neo_stream_cancel_sig.emit()
        if self._spotlight:
            self._spotlight.cancel_assistant_stream()

    def write_log(self, text: str):
        """Activity log — only user and NEO lines, shown immediately."""
        tl = text.strip().lower()
        if not (tl.startswith("you:") or tl.startswith("neo:")):
            return
        if tl.startswith("neo:"):
            self.stop_log_progress()
            body = text.split(":", 1)[1].strip()
            body = _normalize_user_facing_model_text(body)
            if not body or _is_internal_model_text(body):
                self.cancel_neo_stream()
                return
            if self._win._log._neo_stream_active:
                self.finish_neo_stream(body)
                return
            text = "Neo: " + body
            if self._spotlight:
                self._spotlight.append_message("assistant", body)
        self._win._log_sig.emit(text)
        if self._siri:
            self._siri.req_append_log.emit(text)

    def write_log_siri_compact(self, text: str):
        """Update the slim bar prompt only — no log expansion."""
        body = text.split(":", 1)[1].strip() if ":" in text else text.strip()
        if self._spotlight_owns_surface():
            if self._spotlight and body:
                self._spotlight.set_status(body)
            return
        if self._siri:
            self._siri.req_show_compact.emit()
            self._siri.req_set_prompt.emit(body)
            self._siri.req_cancel_hide.emit()

    def write_activity(self, text: str):
        """Status on compact bar only — not chat blocks."""
        if not text or not text.strip():
            return
        t = text.strip()
        if self._spotlight_owns_surface():
            if self._spotlight:
                self._spotlight.set_status(t)
            return
        if self._siri:
            self._siri.req_set_activity.emit(t)

    def start_log_progress(self, eta_sec: int = 15, label: str = ""):
        self._win._progress_start_sig.emit(eta_sec, label)
        if self._spotlight_owns_surface():
            if self._spotlight:
                self._spotlight.set_status(label or "NEO is working…")
            return
        if self._siri:
            self._siri.req_progress_start.emit(eta_sec, label or "Working…")

    def set_activity(self, text: str):
        self.write_activity(text)

    def stop_log_progress(self):
        self._win._progress_stop_sig.emit()
        if self._siri:
            self._siri.req_progress_stop.emit()

    def push_audio_levels(self, bands: list[float]):
        if self._spotlight:
            self._spotlight.push_orb_audio(bands)
        if not self._siri:
            return
        try:
            self._siri.req_audio.emit(bands)
        except RuntimeError:
            self._siri = None

    def show_visuals(self, summary: str, query: str, on_done=None):
        """Open Google Images in the browser for visual/product searches."""
        from actions.browser_native import is_visual_product_query, open_google_images

        q = (query or "").strip()
        if not q and summary:
            q = summary.split("\n", 1)[0].strip()[:120]
        if not q or not is_visual_product_query(f"{q} {summary[:400]}"):
            if on_done:
                try:
                    on_done()
                except Exception as e:
                    print(f"[Visuals] on_done: {e}")
            return

        def run():
            try:
                open_google_images(q)
            except Exception as e:
                print(f"[Visuals] Failed to open images: {e}")
            finally:
                if on_done:
                    try:
                        on_done()
                    except Exception as e:
                        print(f"[Visuals] on_done: {e}")

        threading.Thread(target=run, daemon=True, name="NEO-visuals").start()
        if self._siri:
            self._siri.req_slide_in.emit()

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)
        if self._siri_mode:
            self._dispatch.hide_main.emit()
            # Let the queued hide run on the main thread before audio/UI work.
            for _ in range(40):
                self._app.processEvents()
                time.sleep(0.025)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.manual_mute and self._win._mic_live:
            self.set_state("LISTENING")
