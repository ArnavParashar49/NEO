"""Premium compact Spotlight-style chat surface for NEO."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from enum import Enum, auto
from html import escape

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui_prism_orb import PrismOrb

BAR_WIDTH = 520
BAR_HEIGHT = 52
SHADOW_MARGIN = 10
WINDOW_WIDTH = BAR_WIDTH + SHADOW_MARGIN * 2
COLLAPSED_HEIGHT = BAR_HEIGHT + SHADOW_MARGIN * 2
MAX_OVERLAY_HEIGHT = 330
# Backward-compatible name used by older UI tests.
EXPANDED_HEIGHT = MAX_OVERLAY_HEIGHT
RADIUS = BAR_HEIGHT // 2


def _render_gfm(text: str) -> str:
    """Render safe GFM-style Markdown with tables and strikethrough support."""
    try:
        from markdown_it import MarkdownIt

        parser = MarkdownIt(
            "commonmark",
            {"html": False, "linkify": False, "breaks": True},
        ).enable(("table", "strikethrough"))
        return parser.render(text)
    except Exception:
        return escape(text).replace("\n", "<br>")


def _is_clarification_question(text: str) -> bool:
    """Detect a short question NEO needs answered before it can continue."""
    plain = re.sub(r"[`*_#>]", "", text or "").strip()
    if not plain or not plain.endswith("?"):
        return False
    first_line = plain.splitlines()[0].lower()
    prompts = (
        "what ", "which ", "where ", "when ", "who ", "how ",
        "could you ", "can you ", "would you ", "do you ",
        "please specify", "please provide", "may i ",
    )
    return first_line.startswith(prompts)


class SpotlightState(Enum):
    ORB_VISIBLE = auto()
    SPOTLIGHT_BAR_OPEN = auto()
    SPOTLIGHT_RESPONDING = auto()
    SPOTLIGHT_WITH_RESPONSE = auto()
    CLOSING = auto()


_ACTIVE_STATES = {
    SpotlightState.SPOTLIGHT_BAR_OPEN,
    SpotlightState.SPOTLIGHT_RESPONDING,
    SpotlightState.SPOTLIGHT_WITH_RESPONSE,
    SpotlightState.CLOSING,
}


class SpotlightOrbSlot(QWidget):
    """Small live instance of the same audio-reactive orb used on the desktop."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # The source animation contains transparent edge padding. Zooming only
        # this instance gives the pill a visually 34px orb without changing the
        # desktop orb renderer.
        self.orb = PrismOrb(render_scale=1.35)
        self.orb.setFixedSize(40, 40)
        self.orb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.orb)

    def set_state(self, state: str) -> None:
        self.orb.state = state
        self.orb.muted = state == "MUTED"
        self.orb.speaking = state == "SPEAKING"

    def set_speaking(self, active: bool) -> None:
        self.orb.speaking = bool(active)

    def set_audio_bands(self, bands: list[float]) -> None:
        self.orb.set_audio_bands(bands)


class SpotlightInputBar(QFrame):
    submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("spotlightInputBar")
        self.setFixedHeight(BAR_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(9)
        self.orb_slot = SpotlightOrbSlot()
        layout.addWidget(self.orb_slot, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.input = QLineEdit()
        self.input.setObjectName("spotlightInput")
        self.input.setPlaceholderText("Ask NEO anything…")
        self.input.setClearButtonEnabled(False)
        self.input.returnPressed.connect(self._request_submit)
        layout.addWidget(self.input, stretch=1)

        self.send_button = SpotlightSendButton()
        self.send_button.setFixedSize(30, 30)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self._request_submit)
        layout.addWidget(self.send_button)

    def _request_submit(self) -> None:
        text = self.input.text().strip()
        if text:
            self.submitted.emit(text)

    def accept_submission(self) -> None:
        self.input.clear()

    def set_responding(self, responding: bool) -> None:
        self.send_button.set_mode("responding" if responding else "send")
        self.input.setPlaceholderText(
            "NEO is responding…" if responding else "Ask NEO anything…"
        )

    def set_question_mode(self, active: bool) -> None:
        if active:
            self.input.setPlaceholderText("Type your answer...")

    def show_send_error(self) -> None:
        self.send_button.set_mode("error")
        QTimer.singleShot(1400, lambda: self.send_button.set_mode("send"))


class SpotlightSendButton(QPushButton):
    """Painted bold send symbol; avoids a thin font-dependent arrow glyph."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "send"
        self.setObjectName("spotlightSend")

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        if not painter.isActive():
            return
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor("#f3f3f5"), 2.25)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            cx = self.width() / 2.0
            cy = self.height() / 2.0
            if self._mode == "responding":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#f0f0f2"))
                painter.drawRoundedRect(QRectF(cx - 4, cy - 4, 8, 8), 2.0, 2.0)
            elif self._mode == "error":
                painter.drawLine(int(cx), int(cy - 5), int(cx), int(cy + 2))
                painter.drawPoint(int(cx), int(cy + 6))
            else:
                painter.drawLine(int(cx), int(cy + 6), int(cx), int(cy - 6))
                painter.drawLine(int(cx), int(cy - 6), int(cx - 5), int(cy - 1))
                painter.drawLine(int(cx), int(cy - 6), int(cx + 5), int(cy - 1))
        finally:
            painter.end()


class SpotlightMessageRow(QWidget):
    def __init__(self, role: str, text: str, parent=None):
        super().__init__(parent)
        self._role = role
        self.setObjectName("spotlightMessageRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.bubble = QFrame()
        bubble_name = {
            "user": "spotlightUserMessage",
            "question": "spotlightQuestionMessage",
        }.get(role, "spotlightNeoMessage")
        self.bubble.setObjectName(bubble_name)
        if role == "user":
            self.bubble.setMaximumWidth(360)
        else:
            self.bubble.setMinimumWidth(430)
            self.bubble.setMaximumWidth(478)
        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(
            11 if role == "user" else 3,
            7 if role == "user" else 4,
            11 if role == "user" else 3,
            7 if role == "user" else 4,
        )
        if role == "question":
            prompt = QLabel("NEO NEEDS ONE DETAIL")
            prompt.setObjectName("spotlightQuestionPrompt")
            bubble_layout.addWidget(prompt)
        self.label = QLabel()
        self.label.setObjectName("spotlightMessageText")
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(450 if role in ("assistant", "question") else 334)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        if role in ("assistant", "question"):
            self.label.setTextFormat(Qt.TextFormat.RichText)
            self.label.setOpenExternalLinks(True)
        self._set_label_text(text)
        self.label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        bubble_layout.addWidget(self.label)

        if role == "user":
            layout.addStretch(1)
            layout.addWidget(self.bubble)
        else:
            layout.addWidget(self.bubble)
            layout.addStretch(1)

    def set_text(self, text: str) -> None:
        self._set_label_text(text)
        self.label.adjustSize()
        self.bubble.adjustSize()
        self.adjustSize()

    def _set_label_text(self, text: str) -> None:
        rich_role = self._role in ("assistant", "question")
        self.label.setText(_render_gfm(text) if rich_role else text)


class SpotlightInstallConfirmation(QWidget):
    """Purpose-built confirmation card for a verified terminal install."""

    def __init__(self, title: str, source: str, command: str, parent=None):
        super().__init__(parent)
        self.setObjectName("spotlightMessageRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("spotlightInstallConfirmation")
        card.setMinimumWidth(430)
        card.setMaximumWidth(478)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(6)

        eyebrow = QLabel("READY TO INSTALL")
        eyebrow.setObjectName("spotlightInstallEyebrow")
        layout.addWidget(eyebrow)

        heading = QLabel(title)
        heading.setObjectName("spotlightInstallTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        source_label = QLabel(f"Verified through {source}")
        source_label.setObjectName("spotlightInstallSource")
        layout.addWidget(source_label)

        command_label = QLabel(command)
        command_label.setObjectName("spotlightInstallCommand")
        command_label.setWordWrap(True)
        command_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(command_label)

        instruction = QLabel('Say “yes” to install  ·  “no” to copy the command')
        instruction.setObjectName("spotlightInstallInstruction")
        layout.addWidget(instruction)

        row.addWidget(card)
        row.addStretch(1)


class SpotlightResultsPanel(QFrame):
    height_hint_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("spotlightResultsPanel")
        self._stream_row: SpotlightMessageRow | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 8, 14, 14)
        root.setSpacing(7)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("spotlightScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self._scroll.setMinimumHeight(0)
        root.addWidget(self._scroll, stretch=1)

        self._content = QWidget()
        self._content.setObjectName("spotlightContent")
        self._messages = QVBoxLayout(self._content)
        self._messages.setContentsMargins(2, 2, 2, 2)
        self._messages.setSpacing(9)
        self._messages.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._content)

    def add_message(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if role == "assistant":
            self._stream_row = None
        self._messages.addWidget(SpotlightMessageRow(role, text))
        self._content_changed()

    def show_install_confirmation(self, title: str, source: str, command: str) -> None:
        self.clear_messages()
        self._messages.addWidget(SpotlightInstallConfirmation(title, source, command))
        self._content_changed()

    def update_stream(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._stream_row is None:
            self._stream_row = SpotlightMessageRow("assistant", text)
            self._messages.addWidget(self._stream_row)
        else:
            self._stream_row.set_text(text)
        self._content_changed()

    def finish_stream(self, text: str, *, as_question: bool = False) -> None:
        text = (text or "").strip()
        if as_question and text:
            self.cancel_stream()
            self.add_message("question", text)
            return
        if self._stream_row is not None:
            if text:
                self._stream_row.set_text(text)
            self._stream_row = None
            self._content_changed()
        elif text:
            self.add_message("assistant", text)

    def set_waiting(self, text: str = "NEO is thinking…") -> None:
        return

    def cancel_stream(self) -> None:
        if self._stream_row is not None:
            self._messages.removeWidget(self._stream_row)
            self._stream_row.deleteLater()
            self._stream_row = None
        self._content_changed()

    def has_messages(self) -> bool:
        return self._messages.count() > 0

    def clear_messages(self) -> None:
        self._stream_row = None
        while self._messages.count():
            item = self._messages.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._content_changed()

    def preferred_height(self) -> int:
        self._messages.activate()
        self._content.adjustSize()
        rows = [
            self._messages.itemAt(i).widget()
            for i in range(self._messages.count())
            if self._messages.itemAt(i).widget() is not None
        ]
        messages_height = sum(row.sizeHint().height() for row in rows)
        if len(rows) > 1:
            messages_height += self._messages.spacing() * (len(rows) - 1)
        top, bottom = self._messages.contentsMargins().top(), self._messages.contentsMargins().bottom()
        messages_height += top + bottom
        root_margins = self.layout().contentsMargins()
        chrome = root_margins.top() + root_margins.bottom()
        return min(MAX_OVERLAY_HEIGHT - COLLAPSED_HEIGHT, messages_height + chrome)

    def _content_changed(self) -> None:
        self._messages.invalidate()
        QTimer.singleShot(0, self.height_hint_changed.emit)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())


class SpotlightAssistantOverlay(QWidget):
    """State-driven Spotlight shell that owns typed chat until explicitly closed."""

    append_requested = Signal(str, str)
    stream_requested = Signal(str)
    stream_end_requested = Signal(str)
    stream_cancel_requested = Signal()
    status_requested = Signal(str)
    backend_failed = Signal(str)
    submission_finished = Signal(str, bool, str)
    state_changed = Signal(object)
    orb_state_requested = Signal(str)
    orb_speaking_requested = Signal(bool)
    orb_audio_requested = Signal(object)

    def __init__(
        self,
        on_submit: Callable[[str], bool | None] | None = None,
        can_submit: Callable[[], bool] | None = None,
        on_closed: Callable[[], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._on_submit = on_submit
        self._can_submit = can_submit
        self._on_closed = on_closed
        self._state = SpotlightState.ORB_VISIBLE
        self._source_geometry = QRect()
        self._collapsed_geometry = QRect()
        self._open_animation: QParallelAnimationGroup | None = None
        self._geometry_animation: QPropertyAnimation | None = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(55)
        self._resize_timer.timeout.connect(self._resize_to_content)
        self._height_target = COLLAPSED_HEIGHT
        self._awaiting_replacement = False
        self._outside_click_timer = QTimer(self)
        self._outside_click_timer.setInterval(45)
        self._outside_click_timer.timeout.connect(self._check_global_click)

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("spotlightOverlay")
        self.setMinimumSize(0, 0)
        self.setMaximumHeight(MAX_OVERLAY_HEIGHT)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN
        )
        outer.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("spotlightShell")
        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        outer.addWidget(self._shell)

        self.input_bar = SpotlightInputBar()
        self.results = SpotlightResultsPanel()
        self.results.hide()
        shell_layout.addWidget(self.input_bar)
        shell_layout.addWidget(self.results, stretch=1)

        self.input_bar.submitted.connect(self._submit)
        self.results.height_hint_changed.connect(self._schedule_content_resize)
        self.append_requested.connect(self._append_message)
        self.stream_requested.connect(self._stream)
        self.stream_end_requested.connect(self._finish_stream)
        self.stream_cancel_requested.connect(self._cancel_stream)
        self.status_requested.connect(self.results.set_waiting)
        self.backend_failed.connect(self._show_backend_error)
        self.submission_finished.connect(self._finish_submission)
        self.orb_state_requested.connect(self.input_bar.orb_slot.set_state)
        self.orb_speaking_requested.connect(self.input_bar.orb_slot.set_speaking)
        self.orb_audio_requested.connect(self.input_bar.orb_slot.set_audio_bands)

        self._escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._escape_shortcut.activated.connect(self.close_to_orb)
        self._apply_style()

    @property
    def state(self) -> SpotlightState:
        return self._state

    def owns_chat_surface(self) -> bool:
        return self._state in _ACTIVE_STATES

    def toggle_from_orb(self, orb_geometry: QRect) -> None:
        if self.owns_chat_surface() and self._state != SpotlightState.CLOSING:
            self.close_to_orb()
        elif self._state == SpotlightState.ORB_VISIBLE:
            self.open_from_orb(orb_geometry)

    def open_from_orb(self, orb_geometry: QRect) -> None:
        self._stop_animations()
        self._source_geometry = QRect(orb_geometry)
        self._collapsed_geometry = self._target_geometry(COLLAPSED_HEIGHT)
        self._height_target = COLLAPSED_HEIGHT
        self.results.hide()
        self.input_bar.set_responding(False)
        self.setWindowOpacity(0.55)
        self.setGeometry(self._source_geometry)
        self._set_state(SpotlightState.SPOTLIGHT_BAR_OPEN)
        self.show()
        self.raise_()

        group = QParallelAnimationGroup(self)
        group.addAnimation(
            self._make_geometry_animation(self.geometry(), self._collapsed_geometry, 300)
        )
        group.addAnimation(self._make_opacity_animation(0.55, 1.0, 220))
        group.finished.connect(self._focus_input)
        self._open_animation = group
        group.start()
        self._outside_click_timer.start()
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def close_to_orb(self) -> None:
        if not self.isVisible() or self._state == SpotlightState.CLOSING:
            return
        self._set_state(SpotlightState.CLOSING)
        self._stop_animations()
        group = QParallelAnimationGroup(self)
        group.addAnimation(self._make_geometry_animation(self.geometry(), self._source_geometry, 240))
        group.addAnimation(self._make_opacity_animation(self.windowOpacity(), 0.0, 190))
        group.finished.connect(self._finish_close)
        self._open_animation = group
        group.start()

    def append_message(self, role: str, text: str) -> None:
        if self.owns_chat_surface() and self._state != SpotlightState.CLOSING:
            self.append_requested.emit(role, text)

    def stream_assistant(self, text: str) -> None:
        if self.owns_chat_surface() and self._state != SpotlightState.CLOSING:
            self.stream_requested.emit(text)

    def finish_assistant(self, text: str) -> None:
        if self.owns_chat_surface() and self._state != SpotlightState.CLOSING:
            self.stream_end_requested.emit(text)

    def show_install_confirmation(self, title: str, source: str, command: str) -> None:
        if self.owns_chat_surface() and self._state != SpotlightState.CLOSING:
            self.results.show_install_confirmation(title, source, command)
            self.results.show()
            self.input_bar.set_responding(False)
            self.input_bar.set_question_mode(True)
            self._set_state(SpotlightState.SPOTLIGHT_WITH_RESPONSE)
            self._schedule_content_resize()

    def cancel_assistant_stream(self) -> None:
        if self.owns_chat_surface() and self._state != SpotlightState.CLOSING:
            self.stream_cancel_requested.emit()

    def set_status(self, text: str) -> None:
        if self.owns_chat_surface() and self._state != SpotlightState.CLOSING:
            self.status_requested.emit(text)

    def set_orb_state(self, state: str) -> None:
        self.orb_state_requested.emit(state)

    def set_orb_speaking(self, active: bool) -> None:
        self.orb_speaking_requested.emit(bool(active))

    def push_orb_audio(self, bands: list[float]) -> None:
        self.orb_audio_requested.emit(bands)

    def eventFilter(self, watched, event):
        if (
            self.isVisible()
            and event.type() == QEvent.Type.MouseButtonPress
            and isinstance(watched, QWidget)
            and watched.window() is not self
        ):
            self.close_to_orb()
        return super().eventFilter(watched, event)

    def _check_global_click(self) -> None:
        if not self.isVisible() or self._state == SpotlightState.CLOSING:
            return
        buttons = QApplication.mouseButtons()
        if buttons & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton):
            if not self.frameGeometry().contains(QCursor.pos()):
                self.close_to_orb()

    def paintEvent(self, event) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        if not painter.isActive():
            return
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            base = QRectF(
                SHADOW_MARGIN,
                SHADOW_MARGIN,
                max(1, self.width() - SHADOW_MARGIN * 2),
                max(1, self.height() - SHADOW_MARGIN * 2),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            for spread, alpha in ((8.0, 8), (5.0, 13), (2.5, 20)):
                shadow = base.adjusted(-spread, -spread, spread, spread)
                if shadow.width() > 0 and shadow.height() > 0:
                    painter.setBrush(QColor(0, 0, 0, alpha))
                    painter.drawRoundedRect(shadow, RADIUS + spread, RADIUS + spread)
        finally:
            painter.end()

    def _submit(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if not self._on_submit or (self._can_submit and not self._can_submit()):
            self.input_bar.show_send_error()
            return
        # The callback may synchronously emit a reply before it returns. Mark
        # replacement first so that reply cannot be appended to stale content.
        self._awaiting_replacement = True
        self.input_bar.set_responding(True)

        def dispatch() -> None:
            try:
                accepted = self._on_submit(text)
                self.submission_finished.emit(text, accepted is not False, "")
            except Exception as exc:
                self.submission_finished.emit(text, False, str(exc))

        threading.Thread(target=dispatch, daemon=True, name="NEO-spotlight-submit").start()

    def _finish_submission(self, text: str, accepted: bool, error: str) -> None:
        if not accepted:
            self._awaiting_replacement = False
            self.input_bar.set_responding(False)
            self.input_bar.show_send_error()
            return
        if self.input_bar.input.text().strip() == text:
            self.input_bar.accept_submission()
        # Spotlight is an answer surface: keep the prompt in the normal hidden
        # conversation history, and unfold only when NEO produces visible text.
        self._set_state(SpotlightState.SPOTLIGHT_RESPONDING)

    def _append_message(self, role: str, text: str) -> None:
        self._prepare_latest_response(role)
        self.results.show()
        is_question = role == "assistant" and _is_clarification_question(text)
        self.results.add_message("question" if is_question else role, text)
        if role == "assistant":
            self.input_bar.set_responding(False)
            self.input_bar.set_question_mode(is_question)
            self._set_state(SpotlightState.SPOTLIGHT_WITH_RESPONSE)
        self._schedule_content_resize()

    def _stream(self, text: str) -> None:
        self._prepare_latest_response("assistant")
        self.results.show()
        self.results.update_stream(text)
        self.input_bar.set_responding(True)
        self._set_state(SpotlightState.SPOTLIGHT_RESPONDING)
        self._schedule_content_resize()

    def _finish_stream(self, text: str) -> None:
        self._prepare_latest_response("assistant")
        self.results.show()
        is_question = _is_clarification_question(text)
        self.results.finish_stream(text, as_question=is_question)
        self.input_bar.set_responding(False)
        self.input_bar.set_question_mode(is_question)
        self._set_state(SpotlightState.SPOTLIGHT_WITH_RESPONSE)
        self._schedule_content_resize()

    def _cancel_stream(self) -> None:
        self.results.cancel_stream()
        if not self.results.has_messages():
            self.results.hide()
        self.input_bar.set_responding(True)
        self._set_state(SpotlightState.SPOTLIGHT_RESPONDING)
        self._schedule_content_resize()

    def _show_backend_error(self, error: str) -> None:
        self._prepare_latest_response("assistant")
        self.results.show()
        self.results.add_message("assistant", f"Could not send that message: {error}")
        self.input_bar.set_responding(False)
        self._set_state(SpotlightState.SPOTLIGHT_WITH_RESPONSE)
        self._schedule_content_resize()

    def _prepare_latest_response(self, role: str) -> None:
        if role == "assistant" and self._awaiting_replacement:
            self.results.clear_messages()
            self._awaiting_replacement = False

    def _schedule_content_resize(self) -> None:
        if self._state != SpotlightState.CLOSING:
            self._resize_timer.start()

    def _resize_to_content(self) -> None:
        if self._state == SpotlightState.CLOSING:
            return
        desired = COLLAPSED_HEIGHT
        if self.results.isVisible() and self.results.has_messages():
            desired = min(
                MAX_OVERLAY_HEIGHT,
                COLLAPSED_HEIGHT + self.results.preferred_height(),
            )
        current = self.geometry()
        if abs(current.height() - desired) <= 2:
            self._height_target = desired
            return
        if abs(self._height_target - desired) <= 3:
            return
        if (
            self._state == SpotlightState.SPOTLIGHT_RESPONDING
            and desired < current.height()
            and self.results.isVisible()
        ):
            return
        self._height_target = desired
        end = QRect(current.x(), current.y(), current.width(), desired)
        if self._geometry_animation:
            self._geometry_animation.stop()
        delta = abs(desired - current.height())
        duration = min(520, max(320, 300 + delta))
        self._geometry_animation = self._make_geometry_animation(
            current,
            end,
            duration,
            easing=QEasingCurve.Type.OutQuint,
        )
        self._geometry_animation.start()

    def _target_geometry(self, height: int) -> QRect:
        screen = QApplication.screenAt(self._source_geometry.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1440, 900)
        width = min(WINDOW_WIDTH, max(404, available.width() - 24))
        x = self._source_geometry.center().x() - width // 2
        y = self._source_geometry.center().y() - COLLAPSED_HEIGHT // 2
        x = max(available.left() + 8, min(x, available.right() - width - 7))
        y = max(available.top() + 8, min(y, available.bottom() - height - 7))
        return QRect(x, y, width, min(height, available.height() - 48))

    def _make_geometry_animation(
        self,
        start: QRect,
        end: QRect,
        duration: int,
        *,
        easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
    ):
        animation = QPropertyAnimation(self, b"geometry")
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(duration)
        animation.setEasingCurve(easing)
        return animation

    def _make_opacity_animation(self, start: float, end: float, duration: int):
        animation = QPropertyAnimation(self, b"windowOpacity")
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        return animation

    def _focus_input(self) -> None:
        if self._state == SpotlightState.CLOSING:
            return
        self.activateWindow()
        self.input_bar.input.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _finish_close(self) -> None:
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        self.hide()
        self._outside_click_timer.stop()
        self.setWindowOpacity(1.0)
        self.results.hide()
        self.input_bar.set_responding(False)
        self._set_state(SpotlightState.ORB_VISIBLE)
        if self._on_closed:
            self._on_closed()

    def _set_state(self, state: SpotlightState) -> None:
        if self._state == state:
            return
        self._state = state
        self.state_changed.emit(state)

    def _stop_animations(self) -> None:
        self._resize_timer.stop()
        for animation in (self._open_animation, self._geometry_animation):
            if animation:
                animation.stop()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#spotlightOverlay {{ background: transparent; }}
            QFrame#spotlightShell {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(48, 48, 52, 246),
                    stop: 1 rgba(32, 32, 36, 246)
                );
                border: 1px solid rgba(255, 255, 255, 34);
                border-radius: {RADIUS}px;
            }}
            QFrame#spotlightInputBar {{
                background: rgba(255, 255, 255, 7);
                border: none;
                border-radius: {RADIUS}px;
            }}
            QLineEdit#spotlightInput {{
                color: #f2f2f4;
                background: transparent;
                border: none;
                padding: 2px 0;
                selection-background-color: rgba(120, 140, 175, 110);
                font-family: "Segoe UI";
                font-size: 13px;
            }}
            QPushButton#spotlightSend {{
                background: rgba(255, 255, 255, 18);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 15px;
            }}
            QPushButton#spotlightSend:hover {{
                background: rgba(255, 255, 255, 31);
                border-color: rgba(255, 255, 255, 42);
            }}
            QFrame#spotlightResultsPanel {{
                background: rgba(27, 27, 30, 226);
                border: none;
                border-top: 1px solid rgba(255, 255, 255, 18);
                border-bottom-left-radius: {RADIUS}px;
                border-bottom-right-radius: {RADIUS}px;
            }}
            QScrollArea#spotlightScroll,
            QWidget#spotlightContent,
            QWidget#spotlightMessageRow {{
                background: transparent;
                border: none;
            }}
            QFrame#spotlightUserMessage {{
                background: rgba(255, 255, 255, 16);
                border: 1px solid rgba(255, 255, 255, 19);
                border-radius: 12px;
            }}
            QFrame#spotlightNeoMessage {{
                background: transparent;
                border: none;
            }}
            QFrame#spotlightQuestionMessage {{
                background: rgba(255, 255, 255, 10);
                border: 1px solid rgba(255, 255, 255, 28);
                border-radius: 14px;
                padding: 8px 10px;
            }}
            QFrame#spotlightInstallConfirmation {{
                background: rgba(255, 255, 255, 9);
                border: 1px solid rgba(255, 255, 255, 24);
                border-radius: 14px;
            }}
            QLabel#spotlightInstallEyebrow {{
                color: rgba(255, 255, 255, 112);
                font-family: "Segoe UI Semibold";
                font-size: 9px;
                letter-spacing: 1px;
            }}
            QLabel#spotlightInstallTitle {{
                color: #f4f4f6;
                font-family: "Segoe UI Semibold";
                font-size: 14px;
            }}
            QLabel#spotlightInstallSource,
            QLabel#spotlightInstallInstruction {{
                color: rgba(255, 255, 255, 135);
                font-family: "Segoe UI";
                font-size: 10px;
            }}
            QLabel#spotlightInstallCommand {{
                color: #dedee4;
                background: rgba(0, 0, 0, 42);
                border: 1px solid rgba(255, 255, 255, 18);
                border-radius: 8px;
                padding: 8px 10px;
                font-family: "Consolas";
                font-size: 10px;
            }}
            QLabel#spotlightQuestionPrompt {{
                color: rgba(255, 255, 255, 112);
                background: transparent;
                font-family: "Segoe UI Semibold";
                font-size: 9px;
                letter-spacing: 1px;
            }}
            QLabel#spotlightMessageText {{
                color: #e9e9ec;
                background: transparent;
                font-family: "Segoe UI";
                font-size: 11px;
            }}
            QScrollBar:vertical {{ background: transparent; width: 5px; }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 34);
                border-radius: 2px;
                min-height: 22px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; }}
            """
        )
