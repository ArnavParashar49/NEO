"""NEO chat panel — sharp dark UI with Claude-style blocks."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui_blocks import (
    CodeBlock,
    CommandBlock,
    SourcesBlock,
    _cmd_label,
    extract_urls,
    parse_ai_blocks,
)
from ui_theme import C, ghost_action_button_stylesheet


def _font(size: int, bold: bool = False) -> QFont:
    from ui_theme import _UI_FONT
    f = QFont(_UI_FONT, size)
    f.setBold(bold)
    return f




class _DateStamp(QWidget):
    def __init__(self, when: datetime | None = None):
        super().__init__()
        when = when or datetime.now()
        label = _format_stamp(when)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 4)
        lab = QLabel(label)
        lab.setFont(_font(9))
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addStretch(1)
        lay.addWidget(lab)
        lay.addStretch(1)


def _format_stamp(when: datetime) -> str:
    now = datetime.now()
    time_part = when.strftime("%I:%M %p").lstrip("0")
    if when.date() == now.date():
        return f"Today {time_part}"
    if (now.date() - when.date()).days == 1:
        return f"Yesterday {time_part}"
    return when.strftime("%b %d, %Y · %I:%M %p").lstrip("0")


class _UserBubble(QFrame):
    def __init__(self, text: str):
        super().__init__()
        self.setObjectName("userBubble")
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setFont(_font(12))
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lab.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.addWidget(lab)
        self._lab = lab
        self._apply_label_width()
        self.setMaximumWidth(280)
        self.setStyleSheet(f"""
            QFrame#userBubble {{
                background: {C.USER_BUB};
                border: none;
                border-radius: 16px;
            }}
        """)

    def set_text(self, text: str) -> None:
        self._lab.setText(text)

    def set_max_bubble_width(self, w: int) -> None:
        self.setMaximumWidth(w)
        self._apply_label_width()

    def _apply_label_width(self) -> None:
        inner = max(120, self.maximumWidth() - 32)
        self._lab.setMaximumWidth(inner)


class _AiMessage(QWidget):
    def __init__(self, text: str, *, show_actions: bool = True, streaming: bool = False):
        super().__init__()
        self._plain = text
        self._streaming = streaming
        self._prose_label: QLabel | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(10)
        self._root = root

        if streaming:
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setFont(_font(12))
            lab.setTextFormat(Qt.TextFormat.MarkdownText)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lab.setStyleSheet(
                f"color: {C.AI}; background: transparent; line-height: 1.45;"
            )
            root.addWidget(lab)
            self._prose_label = lab
            return

        self._build_content(text, show_actions=show_actions)

    def _build_content(self, text: str, *, show_actions: bool) -> None:
        segments = parse_ai_blocks(text)
        prose_chunks: list[str] = []
        cmd_list: list[str] = []
        code_blocks: list[tuple[str, str]] = []
        all_sources: list[dict] = []

        for kind, payload in segments:
            if kind == "code":
                lang, code = payload  # type: ignore[misc]
                code_blocks.append((lang, code))
            elif kind == "cmd":
                cmd_list.append(str(payload))
            elif kind == "prose":
                prose, urls = extract_urls(str(payload))
                all_sources.extend(urls)
                if prose.strip():
                    prose_chunks.append(prose)

        if prose_chunks:
            combined = "\n\n".join(prose_chunks)
            lab = QLabel()
            lab.setWordWrap(True)
            lab.setFont(_font(12))
            lab.setTextFormat(Qt.TextFormat.MarkdownText)
            lab.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            lab.setStyleSheet(
                f"color: {C.AI}; background: transparent; line-height: 1.45;"
            )
            lab.setText(combined)
            self._root.addWidget(lab)
            self._prose_label = lab

        for lang, code in code_blocks:
            self._root.addWidget(CodeBlock(code, lang))

        for cmd in cmd_list:
            self._root.addWidget(CommandBlock(cmd, label=_cmd_label(cmd)))

        if all_sources:
            # dedupe sources by url
            seen: set[str] = set()
            unique: list[dict] = []
            for s in all_sources:
                u = s.get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    unique.append(s)
            if unique:
                self._root.addWidget(SourcesBlock(unique))

        if show_actions and text.strip():
            actions = QHBoxLayout()
            actions.setSpacing(8)
            copy_btn = QPushButton("⎘  Copy")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet(ghost_action_button_stylesheet())
            copy_btn.clicked.connect(self._copy)
            actions.addWidget(copy_btn)
            actions.addStretch(1)
            self._root.addLayout(actions)

    def set_text(self, text: str) -> None:
        self._plain = text
        if self._prose_label is not None:
            self._prose_label.setText(text)

    def set_max_content_width(self, w: int) -> None:
        self.setMaximumWidth(w)
        inner = max(120, w - 4)
        if self._prose_label is not None:
            self._prose_label.setMaximumWidth(inner)
        for block in self.findChildren(QFrame):
            if block.objectName() == "contentBlock":
                block.setMaximumWidth(inner)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._plain)


class _EmptyState(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 16)
        lay.setSpacing(6)
        greet = QLabel("Hi, I'm NEO")
        greet.setFont(_font(16, bold=True))
        greet.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        sub = QLabel(
            "Ask me anything — voice, typing, or drag a file in.\n"
            "Double-click the orb to open chat."
        )
        sub.setFont(_font(10))
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; line-height: 1.5;"
        )
        lay.addWidget(greet)
        lay.addWidget(sub)
        lay.addWidget(_DateStamp())
        lay.addStretch(1)


def _user_row(bubble: QFrame) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addStretch(1)
    lay.addWidget(bubble)
    return w


def _ai_row(widget: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget, stretch=1)
    return w


class ChatView(QScrollArea):
    retry_last = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setStyleSheet(
            f"QScrollArea{{background:{C.BG};border:none;}}"
            "QScrollBar:vertical{background:transparent;width:5px;margin:2px 1px;}"
            "QScrollBar::handle:vertical{background:rgba(255,255,255,0.12);"
            "border-radius:2px;min-height:24px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;}"
        )
        self._host = QWidget()
        self._host.setStyleSheet(f"background:{C.BG};")
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(10, 0, 10, 8)
        self._lay.setSpacing(16)
        self._empty = _EmptyState()
        self._lay.addWidget(self._empty)
        self._lay.addStretch(1)
        self.setWidget(self._host)
        self._stream_msg: _AiMessage | None = None
        self._last_user = ""
        self._last_tool_status = ""
        self._last_stamp_day: str | None = None
        self._scroll_debounce = QTimer(self)
        self._scroll_debounce.setSingleShot(True)
        self._scroll_debounce.setInterval(40)
        self._scroll_debounce.timeout.connect(self._scroll_bottom)

    @property
    def _neo_stream_active(self) -> bool:
        return self._stream_msg is not None

    def _hide_empty(self) -> None:
        if self._empty.isVisible():
            self._empty.hide()

    def _maybe_stamp(self) -> None:
        day = datetime.now().strftime("%Y-%m-%d")
        if self._last_stamp_day == day:
            return
        self._last_stamp_day = day
        self._add(_DateStamp(), count=False)

    def _add(self, widget, *, count: bool = True) -> None:
        self._hide_empty()
        self._lay.insertWidget(self._lay.count() - 1, widget)
        self._reflow_widths()
        QTimer.singleShot(50, self._scroll_bottom)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow_widths()

    def _reflow_widths(self) -> None:
        mw = max(160, self.viewport().width() - 24)
        user_w = int(mw * 0.86)
        for i in range(self._lay.count()):
            item = self._lay.itemAt(i)
            w = item.widget()
            if w is None or w is self._empty:
                continue
            w.setMaximumWidth(mw)
            for bubble in w.findChildren(_UserBubble):
                bubble.set_max_bubble_width(user_w)
            for msg in w.findChildren(_AiMessage):
                msg.set_max_content_width(mw)

    def _scroll_bottom(self) -> None:
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _save_history(self, role: str, text: str):
        from core.paths import base_dir

        mem_dir = base_dir() / "memory"
        mem_dir.mkdir(exist_ok=True, parents=True)
        hist_file = mem_dir / "conversation_history.txt"
        lines = []
        if hist_file.exists():
            try:
                lines = hist_file.read_text(encoding="utf-8").strip().split("\n")
            except Exception:
                pass
        clean_text = text.replace("\n", " ")
        lines.append(json.dumps({"role": role, "text": clean_text}))
        lines = lines[-20:]
        try:
            hist_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

    def append_tool_block(self, label: str, detail: str = "") -> None:
        """Legacy hook — tool status is shown on the bar, not as chat blocks."""
        return

    def _add_ai(self, text: str, *, streaming: bool = False) -> _AiMessage:
        self._last_tool_status = ""
        self._maybe_stamp()
        msg = _AiMessage(text, show_actions=not streaming)
        self._add(_ai_row(msg))
        return msg

    def append_log(self, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        low = t.lower()
        if low.startswith("system:"):
            return  # hide system noise (e.g. conversation reset)
        if low.startswith("you:"):
            msg = t.split(":", 1)[1].strip()
            self._last_user = msg
            self._maybe_stamp()
            self._add(_user_row(_UserBubble(msg)))
            self._save_history("user", msg)
        elif low.startswith("neo:"):
            msg = t.split(":", 1)[1].strip()
            self._add_ai(msg)
            self._save_history("model", msg)
        elif low.startswith("file:"):
            self._maybe_stamp()
            chip = QLabel("📎 " + t.split(":", 1)[1].strip())
            chip.setFont(_font(9))
            chip.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.addStretch(1)
            lay.addWidget(chip)
            lay.addStretch(1)
            self._add(w)
        else:
            pass  # ignore other system lines

    def append_log_instant(self, text: str) -> None:
        self.append_log(text)

    def update_neo_stream(self, body: str) -> None:
        if not body:
            return
        if self._stream_msg is None:
            self._maybe_stamp()
            self._stream_msg = _AiMessage(body, show_actions=False, streaming=True)
            self._add(_ai_row(self._stream_msg))
        else:
            self._stream_msg.set_text(body)
        self._scroll_debounce.start()

    def end_neo_stream(self, body: str, formatted=None) -> None:
        # Always render the raw model text — ChatView parses code/cmd blocks itself.
        text = (body or "").strip()
        if self._stream_msg is not None:
            if text:
                parent = self._stream_msg.parentWidget()
                if parent:
                    idx = self._lay.indexOf(parent)
                    if idx >= 0:
                        self._lay.removeWidget(parent)
                        parent.deleteLater()
                self._stream_msg = None
                self._add_ai(text)
            else:
                self._stream_msg = None
        elif text:
            self._add_ai(text)
        if text:
            self._save_history("model", text)
        QTimer.singleShot(50, self._scroll_bottom)

    def cancel_neo_stream(self) -> None:
        """Remove provisional model narration when the model proceeds to a tool call."""
        if self._stream_msg is None:
            return
        parent = self._stream_msg.parentWidget()
        if parent:
            self._lay.removeWidget(parent)
            parent.deleteLater()
        self._stream_msg = None

    def clear_view(self) -> None:
        self._stream_msg = None
        self._last_user = ""
        self._last_tool_status = ""
        self._last_stamp_day = None
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._empty = _EmptyState()
        self._lay.insertWidget(0, self._empty)
        self._empty.show()
