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

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QFontDatabase,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QShortcut, QTextCharFormat, QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 440, 400
_MIN_W,     _MIN_H     = 360, 280

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

from ui_theme import (
    C,
    RADIUS_L,
    RADIUS_M,
    RADIUS_S,
    _MONO_FONT,
    command_input_drag_stylesheet,
    command_input_stylesheet,
    embed_panel_stylesheet,
    expanded_shell_stylesheet,
    icon_button_stylesheet,
    log_widget_stylesheet,
    mono_font,
    primary_button_stylesheet,
    progress_bar_stylesheet,
    qcol,
    ui_font,
)
from actions.list_format import format_list_for_log as _format_list_for_log
from ui_panel import ChatView


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

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
        self._aria_stream_active = False
        self._aria_stream_start = 0

    def _ai_fmt(self, cur: QTextCursor):
        fmt = cur.charFormat()
        fmt.setForeground(QBrush(qcol(C.AI)))
        return fmt

    def begin_aria_stream(self):
        self._aria_stream_active = True
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self._aria_stream_start = cur.position()
        cur.insertText("Aria: ", self._ai_fmt(cur))
        self._aria_stream_start = cur.position()
        self.setTextCursor(cur)

    def update_aria_stream(self, body: str):
        if not body:
            return
        if not self._aria_stream_active:
            self.begin_aria_stream()
        cur = self.textCursor()
        cur.setPosition(self._aria_stream_start)
        cur.movePosition(
            cur.MoveOperation.End,
            cur.MoveMode.KeepAnchor,
        )
        cur.removeSelectedText()
        cur.insertText(body, self._ai_fmt(cur))
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def end_aria_stream(self, body: str, formatted: str | None = None):
        if not self._aria_stream_active:
            if body:
                self.append_log_instant(f"Aria: {formatted or body}")
            return
        text = (formatted if formatted is not None else body).strip()
        cur = self.textCursor()
        cur.setPosition(self._aria_stream_start)
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
        self._aria_stream_active = False

    def append_log(self, text: str):
        self._sig.emit(text)

    def append_log_instant(self, text: str):
        """Show a full line immediately (no typewriter delay)."""
        tl = text.lower()
        if tl.startswith("you:"):
            tag = "you"
        elif tl.startswith("aria:"):
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
        elif tl.startswith("aria:"): self._tag = "ai"
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


class CommandBar(QWidget):
    """Command input with attach and drag-and-drop."""

    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._current_file: str | None = None
        self._input_style = command_input_stylesheet()
        self._input_drag_style = command_input_drag_stylesheet()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._file_chip = QLabel("")
        self._file_chip.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(110, 231, 160, 0.12);
            border: 1px solid rgba(110, 231, 160, 0.35);
            border-radius: {RADIUS_S}px;
            padding: 6px 10px;
            {ui_font(11)}
        """)
        self._file_chip.setVisible(False)
        self._file_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._file_chip.mouseReleaseEvent = self._chip_clicked
        root.addWidget(self._file_chip)

        row = QHBoxLayout()
        row.setSpacing(8)

        attach = QPushButton("Attach")
        attach.setFixedHeight(40)
        attach.setToolTip("Attach a file")
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setStyleSheet(self._icon_btn_style())
        attach.clicked.connect(self._browse)
        row.addWidget(attach)

        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText("Ask ARIA anything…")
        self.line_edit.setFont(QFont(_MONO_FONT, 12))
        self.line_edit.setFixedHeight(40)
        self.line_edit.setStyleSheet(self._input_style)
        row.addWidget(self.line_edit, stretch=1)

        send = QPushButton("Send")
        send.setFixedSize(64, 40)
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(primary_button_stylesheet())
        send.clicked.connect(self.line_edit.returnPressed.emit)
        row.addWidget(send)

        root.addLayout(row)

    @staticmethod
    def _icon_btn_style() -> str:
        return icon_button_stylesheet()

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
            self, "Attach a file for ARIA", str(Path.home()), _FILE_DIALOG_FILTER,
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
            self.line_edit.setStyleSheet(self._input_drag_style)

    def dragLeaveEvent(self, e):
        self.line_edit.setStyleSheet(self._input_style)

    def dropEvent(self, e: QDropEvent):
        self.line_edit.setStyleSheet(self._input_style)
        path = self._pick_file_from_mime(e.mimeData())
        if path:
            self._set_file(path)
            e.acceptProposedAction()


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: #181d1c;
                border: 1px solid #2c3a38;
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
            w.setFont(QFont("Courier New", font_size,
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
        layout.addWidget(_lbl("Let's set up ARIA", 15, True))
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
        self._key_input.setFont(QFont("Courier New", 15))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #222b2a; color: {C.TEXT};
                border: 1px solid #2c3a38; border-radius: 10px; padding: 6px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid #22a89c; }}
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
            btn.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("Start ARIA  ▸")
        init_btn.setFont(QFont("Courier New", 15, QFont.Weight.Bold))
        init_btn.setFixedHeight(40)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet("""
            QPushButton {
                background: #22a89c; color: #07201c;
                border: none; border-radius: 12px;
            }
            QPushButton:hover { background: #6fe3d6; }
            QPushButton:pressed { background: #178a80; }
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        for k, btn in self._os_btns.items():
            if k == key:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #22a89c; color: #07201c;
                        border: none; border-radius: 10px; font-weight: bold;
                    }
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #222b2a; color: {C.TEXT_DIM};
                        border: 1px solid #2c3a38; border-radius: 10px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid #3d514e; }}
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
    collapse_requested = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.collapse_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    _log_sig    = pyqtSignal(str)
    _state_sig  = pyqtSignal(str)
    _progress_start_sig = pyqtSignal(int)
    _progress_stop_sig  = pyqtSignal()
    _aria_stream_sig    = pyqtSignal(str)
    _aria_stream_end_sig = pyqtSignal(str, object)
    panel_collapse_requested = pyqtSignal()

    def __init__(self, face_path: str = ""):
        super().__init__()
        self.setObjectName("ariaExpandedShell")
        self.setStyleSheet(expanded_shell_stylesheet())
        self.setWindowTitle("ARIA")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_force_listen  = None
        self._manual_mute     = False
        self._standby         = False
        self._mic_live        = False
        self._current_file: str | None = None
        self._ui_state        = "INITIALISING"
        self._email_capture   = None  # holder dict while awaiting a typed email address

        central = QWidget()
        central.setObjectName("ariaExpandedShell")
        central.setStyleSheet(expanded_shell_stylesheet())
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)
        root.addWidget(self._build_header())
        root.addWidget(self._build_main_panel(), stretch=1)
        root.addWidget(self._build_footer())

        self._log_sig.connect(self._log.append_log)
        self._aria_stream_sig.connect(self._log.update_aria_stream)
        self._aria_stream_end_sig.connect(self._on_aria_stream_end)
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
            ow, oh = min(400, self.width() - 24), min(300, self.height() - 24)
            cw = self.centralWidget()
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )

    def _build_header(self) -> QWidget:
        from ui_buddy import PixelBuddy
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(10)

        avatar = PixelBuddy()
        avatar.setFixedSize(40, 40)
        avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(avatar)

        title = QLabel("ARIA")
        title.setStyleSheet(f"color: {C.TEXT}; background: transparent; {ui_font(18, bold=True)}")
        lay.addWidget(title)

        lay.addStretch()

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 10pt;")
        lay.addWidget(self._status_dot)

        self._status_lbl = QLabel("Starting…")
        self._status_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; {ui_font(11)}"
        )
        lay.addWidget(self._status_lbl)

        return w

    def _build_main_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(10)

        conv_label = QLabel("Conversation")
        conv_label.setStyleSheet(
            f"color: {C.TEXT_MED}; background: transparent; {ui_font(10, bold=True)}"
        )
        lay.addWidget(conv_label)

        self._log_progress_lbl = QLabel("")
        self._log_progress_lbl.setStyleSheet(
            f"color: {C.LINK}; background: transparent; {ui_font(10)}"
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
        self._log = ChatView()   # bubble chat (drop-in for LogWidget)
        lay.addWidget(self._log, stretch=1)

        self._cmd = CommandBar()
        self._cmd.file_selected.connect(self._on_file_selected)
        self._cmd.line_edit.returnPressed.connect(self._send)
        lay.addWidget(self._cmd)

        self._mute_btn = QPushButton()
        self._mute_btn.setFixedHeight(36)
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        return w

    def _build_right_panel(self) -> QWidget:
        return self._build_main_panel()

    def _build_footer(self) -> QWidget:
        w = _FooterStrip()
        self._footer_strip = w
        w.setFixedHeight(34)
        w.setStyleSheet("background: transparent;")
        w.collapse_requested.connect(self.panel_collapse_requested.emit)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        self._shrink_btn = QPushButton("F4 mute  ·  click here to shrink")
        self._shrink_btn.setFlat(True)
        self._shrink_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shrink_btn.setStyleSheet(
            f"""
            QPushButton {{
                color: {C.TEXT_MED};
                background: transparent;
                border: none;
                text-align: left;
                padding: 2px 0;
                {ui_font(9)}
            }}
            QPushButton:hover {{
                color: {C.TEXT};
            }}
            """
        )
        self._shrink_btn.clicked.connect(self.panel_collapse_requested.emit)
        lay.addWidget(self._shrink_btn, stretch=1)
        sub = QLabel("Voice assistant")
        sub.setStyleSheet(
            f"color: {C.PRI_GHO}; background: transparent; {ui_font(9)}"
        )
        sub.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(sub)
        return w

    def _start_search_progress(self, eta_sec: int):
        self._progress_eta = max(8, eta_sec)
        self._progress_elapsed = 0.0
        self._log_progress_bar.setRange(0, 100)
        self._log_progress_bar.setValue(0)
        self._log_progress_lbl.setText(f"Searching · ~{self._progress_eta}s")
        self._log_progress_lbl.setVisible(True)
        self._log_progress_bar.setVisible(True)
        self._progress_tick.start(500)

    def _tick_search_progress(self):
        self._progress_elapsed += 0.5
        pct = min(96, int(100 * self._progress_elapsed / self._progress_eta))
        left = max(0, int(self._progress_eta - self._progress_elapsed))
        self._log_progress_bar.setValue(pct)
        self._log_progress_lbl.setText(f"Searching · ~{left}s left")

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
        p = Path(path)
        size = _fmt_size(p.stat().st_size)
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

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
            print("[ARIA] Microphone muted.")
        elif self._standby:
            print("[ARIA] Standby — say 'Aria' or clap twice.")
        else:
            print("[ARIA] Microphone active.")

    def _style_mute_btn(self):
        if not hasattr(self, "_mute_btn"):
            return
        font = ui_font(12, bold=True)
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
                    background: rgba(248, 113, 113, 0.12);
                    color: {C.RED};
                    border: 1px solid rgba(248, 113, 113, 0.35);
                }}
                """
            )
        elif self._standby:
            self._mute_btn.setText("Standby — say Aria or clap twice")
            self._mute_btn.setStyleSheet(
                f"""
                QPushButton {{
                    border-radius: {radius}px;
                    {font}
                    background: {C.SURFACE2};
                    color: {C.TEXT_DIM};
                    border: 1px solid #2a2a30;
                }}
                QPushButton:hover {{
                    background: #2a2a30;
                    color: {C.TEXT};
                }}
                """
            )
        else:
            self._mute_btn.setText("Mic off — tap to listen")
            self._mute_btn.setStyleSheet(
                f"""
                QPushButton {{
                    border-radius: {radius}px;
                    {font}
                    background: {C.SURFACE2};
                    color: {C.TEXT_DIM};
                    border: 1px solid #2a2a30;
                }}
                QPushButton:hover {{
                    background: #2a2a30;
                    color: {C.TEXT};
                }}
                """
            )

    def _set_status_ui(self, text: str, color: str):
        if hasattr(self, "_status_lbl"):
            self._status_lbl.setText(text)
            self._status_lbl.setStyleSheet(
                f"color: {color}; background: transparent; {ui_font(11)}"
            )
        if hasattr(self, "_status_dot"):
            self._status_dot.setStyleSheet(
                f"color: {color}; background: transparent; font-size: 10pt;"
            )

    def _apply_state(self, state: str):
        self._ui_state = state
        status_map = {
            "STANDBY": ("Standby", C.TEXT_MED),
            "LISTENING": ("Listening", C.GREEN),
            "SPEAKING": ("Speaking", C.LINK),
            "THINKING": ("Thinking", C.ACC2),
            "MUTED": ("Muted", C.RED),
            "INITIALISING": ("Starting…", C.TEXT_MED),
        }
        text, color = status_map.get(state, (state.title(), C.TEXT_DIM))
        if self._manual_mute:
            text, color = "Muted", C.RED
        elif self._standby and state == "STANDBY":
            text, color = "Standby", C.TEXT_MED
        self._set_status_ui(text, color)
        self._style_mute_btn()

    def begin_email_capture(self, holder: dict) -> None:
        """Arm the command input to capture the next line as an email address."""
        self._email_capture = holder
        self._log.append_log(f"ARIA: {holder.get('prompt', 'Type the email address.')}")
        self._cmd.line_edit.setText(holder.get("prefill", ""))
        self._cmd.line_edit.setFocus()
        self._cmd.line_edit.selectAll()

    def _send(self):
        txt = self._cmd.line_edit.text().strip()
        if not txt:
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

        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _on_aria_stream_end(self, body: str, formatted):
        self._log.end_aria_stream(body, formatted if formatted else None)

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = min(400, cw.width() - 24), min(300, cw.height() - 24)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._sync_mic_ui()
        self._style_mute_btn()
        print(f"[ARIA] Initialised. OS={os_name.upper()}.")

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


class _UiDispatcher(QObject):
    """Marshals UI work onto the Qt main thread."""
    hide_main = pyqtSignal()
    show_screen_border = pyqtSignal()
    hide_screen_border = pyqtSignal()
    begin_camera_session = pyqtSignal(int, int)  # camera_index, backend
    hide_camera_preview = pyqtSignal()
    set_camera_status = pyqtSignal(str)
    request_email = pyqtSignal(object)  # holder; show main window + capture typed address


class AriaUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._face_path = face_path
        self._siri_mode = _siri_overlay_enabled()
        self._dispatch = _UiDispatcher()
        self._win = MainWindow(face_path)
        self._siri = None
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
        if self._siri_mode:
            self._init_siri_bar()
            if self._win._ready:
                self._win.hide()
            else:
                self._win.show()
        else:
            self._win.show()
        self.root = _RootShim(self._app)

    def _init_siri_bar(self):
        if self._siri or not self._siri_mode:
            return
        from ui_siri_bar import SiriBarWindow

        self._siri = SiriBarWindow(self._face_path, main_window=self._win)
        self._siri.on_text_command = self._win.on_text_command
        self._siri._mute_toggle_cb = self._win._toggle_mute
        self._siri.on_camera_session_ready = self._on_camera_session_ready
        self._siri.req_collapse_camera.connect(self._on_camera_panel_collapse)
        self._win.panel_collapse_requested.connect(self._request_siri_collapse)

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
        run out), so the user always sees what ARIA is doing.
        """
        try:
            self._win._log.append_log(text)  # append_log is signal-backed / thread-safe
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
        if self._siri:
            self._siri.set_speaking_active(active)

    def siri_wake(self):
        if self._siri:
            self._siri.req_cancel_hide.emit()
            if self._siri.is_expanded():
                return
            self._siri.req_show_compact.emit()

    def siri_schedule_hide(self, delay_ms: int = 7000) -> None:
        """Slide orb away after idle (default 7s)."""
        if self._siri and not self._siri.is_expanded():
            self._siri.req_schedule_hide.emit(max(0, int(delay_ms)))

    def siri_hide_now(self) -> None:
        if self._siri:
            self._siri.req_schedule_hide.emit(0)

    def siri_cancel_hide(self) -> None:
        if self._siri:
            self._siri.req_cancel_hide.emit()

    def siri_set_prompt(self, text: str):
        if self._siri:
            self._siri.req_set_prompt.emit(text)

    def siri_blocks_wake(self) -> bool:
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
        if self._siri:
            self._siri.req_apply_state.emit(state)
            if state in ("SPEAKING", "THINKING"):
                self._siri.req_cancel_hide.emit()
            elif state == "STANDBY" and not self._siri.is_expanded():
                self.siri_hide_now()

    def stream_aria(self, body: str):
        """Update the live ARIA line while she is speaking."""
        if not body or not body.strip():
            return
        self.stop_log_progress()
        t = body.strip()
        self._win._aria_stream_sig.emit(t)
        if self._siri:
            self._siri.req_stream.emit(t)

    def finish_aria_stream(self, body: str):
        """Finalize the live line with optional list formatting."""
        if not body or not body.strip():
            return
        self.stop_log_progress()
        formatted = _format_list_for_log(body.strip())
        self._win._aria_stream_end_sig.emit(body.strip(), formatted)
        if self._siri:
            self._siri.req_stream_end.emit(body.strip(), None)

    def write_log(self, text: str):
        """Activity log — only user and ARIA lines, shown immediately."""
        tl = text.strip().lower()
        if not (tl.startswith("you:") or tl.startswith("aria:")):
            return
        if tl.startswith("aria:"):
            self.stop_log_progress()
            body = text.split(":", 1)[1].strip()
            if self._win._log._aria_stream_active:
                self.finish_aria_stream(body)
                return
            text = "Aria: " + _format_list_for_log(body)
        self._win._log.append_log_instant(text)
        if self._siri:
            self._siri.req_append_log.emit(text)

    def write_log_siri_compact(self, text: str):
        """Update the slim bar prompt only — no log expansion."""
        body = text.split(":", 1)[1].strip() if ":" in text else text.strip()
        if self._siri:
            self._siri.req_show_compact.emit()
            self._siri.req_set_prompt.emit(body)
            self._siri.req_cancel_hide.emit()

    def write_activity(self, text: str):
        """Status line on the compact bar."""
        if not text or not text.strip():
            return
        t = text.strip()
        if self._siri:
            self._siri.req_set_activity.emit(t)

    def start_log_progress(self, eta_sec: int = 15, label: str = ""):
        self._win._progress_start_sig.emit(eta_sec)
        if self._siri:
            self._siri.req_progress_start.emit(eta_sec, label or "Working…")

    def set_activity(self, text: str):
        self.write_activity(text)

    def stop_log_progress(self):
        self._win._progress_stop_sig.emit()
        if self._siri:
            self._siri.req_progress_stop.emit()

    def push_audio_levels(self, bands: list[float]):
        if not self._siri:
            return
        try:
            self._siri.req_audio.emit(bands)
        except RuntimeError:
            self._siri = None

    def show_visuals(self, summary: str, query: str, on_done=None):
        """Compact UI: skip image grid; finish callback immediately."""
        if self._siri:
            self._siri.req_slide_in.emit()
        if on_done:
            try:
                on_done()
            except Exception as e:
                print(f"[VisualFeed] on_done: {e}")

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
