"""Behavior checks for the state-driven Spotlight assistant shell."""

from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt, qInstallMessageHandler
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel

from spotlight_overlay import (
    BAR_HEIGHT,
    BAR_WIDTH,
    COLLAPSED_HEIGHT,
    MAX_OVERLAY_HEIGHT,
    SpotlightAssistantOverlay,
    SpotlightState,
    WINDOW_WIDTH,
)
from ui_prism_orb import PrismOrb


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _open(overlay: SpotlightAssistantOverlay) -> None:
    overlay.open_from_orb(QRect(350, 80, 96, 96))
    QTest.qWait(340)


def _close(overlay: SpotlightAssistantOverlay) -> None:
    overlay.close_to_orb()
    QTest.qWait(280)


def test_typing_keeps_search_bar_collapsed():
    _app()
    overlay = SpotlightAssistantOverlay()
    _open(overlay)

    assert overlay.state == SpotlightState.SPOTLIGHT_BAR_OPEN
    assert abs(overlay.width() - WINDOW_WIDTH) <= 2
    assert abs(overlay.height() - COLLAPSED_HEIGHT) <= 2
    assert abs(overlay.frameGeometry().center().y() - 128) <= 2
    assert abs(overlay.input_bar.width() - BAR_WIDTH) <= 4
    assert overlay.input_bar.height() == BAR_HEIGHT
    assert overlay.input_bar.input.hasFocus()

    QTest.keyClicks(overlay.input_bar.input, "hello neo")
    QTest.qWait(100)
    assert abs(overlay.height() - COLLAPSED_HEIGHT) <= 2
    assert not overlay.results.isVisible()
    _close(overlay)


def test_search_icon_is_replaced_by_live_prism_orb():
    _app()
    overlay = SpotlightAssistantOverlay()
    _open(overlay)

    orb = overlay.input_bar.orb_slot.orb
    assert isinstance(orb, PrismOrb)
    assert orb.isVisible()

    overlay.set_orb_state("THINKING")
    overlay.push_orb_audio([0.2, 0.5, 0.8])
    overlay.set_orb_speaking(True)
    QTest.qWait(50)

    assert orb.state == "THINKING"
    assert orb.speaking is True
    assert orb._energy == 1.0
    _close(overlay)


def test_submit_is_accepted_then_expands_to_content():
    _app()
    received: list[str] = []
    submitted = threading.Event()

    def on_submit(text: str) -> None:
        received.append(text)
        submitted.set()

    overlay = SpotlightAssistantOverlay(
        on_submit=on_submit,
        can_submit=lambda: True,
    )
    _open(overlay)
    overlay.input_bar.input.setText("hello neo")
    overlay.input_bar._request_submit()

    assert submitted.wait(1.0)
    QTest.qWait(260)
    assert received == ["hello neo"]
    assert overlay.input_bar.input.text() == ""
    assert overlay.state == SpotlightState.SPOTLIGHT_RESPONDING
    assert not overlay.results.isVisible()
    assert abs(overlay.height() - COLLAPSED_HEIGHT) <= 2
    assert overlay.input_bar.input.placeholderText() == "NEO is responding…"
    assert overlay.input_bar.send_button._mode == "responding"

    initial_height = overlay.height()
    overlay.stream_assistant(
        "Here is a concise response with enough content to require a little more room. "
        "The overlay should grow based on this message, not jump to a fixed rectangle."
    )
    QTest.qWait(280)
    assert overlay.height() >= initial_height
    assert overlay.height() <= MAX_OVERLAY_HEIGHT

    overlay.finish_assistant("Done — the response remains inside Spotlight.")
    QTest.qWait(260)
    assert overlay.state == SpotlightState.SPOTLIGHT_WITH_RESPONSE
    assert overlay.isVisible()
    assert overlay.input_bar.input.placeholderText() == "Ask NEO anything…"
    assert overlay.findChild(QLabel, "spotlightNeoAvatar") is None
    assert overlay.findChild(QLabel, "spotlightStatus") is None
    _close(overlay)


def test_unavailable_backend_does_not_clear_input():
    _app()
    overlay = SpotlightAssistantOverlay(
        on_submit=lambda text: None,
        can_submit=lambda: False,
    )
    _open(overlay)
    overlay.input_bar.input.setText("keep this")
    overlay.input_bar._request_submit()
    QTest.qWait(100)
    assert overlay.input_bar.input.text() == "keep this"
    assert overlay.state == SpotlightState.SPOTLIGHT_BAR_OPEN
    _close(overlay)


def test_tool_call_cancels_provisional_sequence_narration():
    _app()
    overlay = SpotlightAssistantOverlay(on_submit=lambda text: True, can_submit=lambda: True)
    _open(overlay)
    overlay.input_bar.input.setText("open notepad")
    overlay.input_bar._request_submit()
    QTest.qWait(180)
    overlay.stream_assistant("**Sequencing My Approach**\n\nFirst I will use a tool.")
    QTest.qWait(100)
    assert overlay.results._stream_row is not None

    overlay.cancel_assistant_stream()
    QTest.qWait(100)
    assert overlay.results._stream_row is None

    overlay.finish_assistant("Opened Notepad.")
    QTest.qWait(180)
    visible_text = " ".join(label.text() for label in overlay.findChildren(QLabel))
    assert "Sequencing My Approach" not in visible_text
    assert "Opened Notepad" in visible_text
    _close(overlay)


def test_latest_neo_response_replaces_previous_response():
    _app()
    overlay = SpotlightAssistantOverlay(on_submit=lambda text: True, can_submit=lambda: True)
    _open(overlay)

    overlay.input_bar.input.setText("hi")
    overlay.input_bar._request_submit()
    QTest.qWait(120)
    overlay.finish_assistant("Hello, how may I assist you?")
    QTest.qWait(180)

    overlay.input_bar.input.setText("open youtube")
    overlay.input_bar._request_submit()
    QTest.qWait(120)
    overlay.finish_assistant("Opened YouTube.")
    QTest.qWait(220)

    visible_text = " ".join(
        label.text() for label in overlay.findChildren(QLabel)
        if label.objectName() == "spotlightMessageText"
    )
    assert "Opened YouTube" in visible_text
    assert "how may I assist" not in visible_text
    _close(overlay)


def test_synchronous_backend_reply_cannot_append_to_previous_response():
    _app()
    overlay: SpotlightAssistantOverlay

    def on_submit(text: str) -> bool:
        overlay.finish_assistant(f"Reply to {text}")
        return True

    overlay = SpotlightAssistantOverlay(on_submit=on_submit, can_submit=lambda: True)
    _open(overlay)
    overlay.finish_assistant("Old greeting")
    QTest.qWait(100)

    overlay.input_bar.input.setText("weather")
    overlay.input_bar._request_submit()
    QTest.qWait(220)

    visible = " ".join(
        label.text() for label in overlay.findChildren(QLabel)
        if label.objectName() == "spotlightMessageText"
    )
    assert "Reply to weather" in visible
    assert "Old greeting" not in visible
    _close(overlay)


def test_clarification_replaces_answer_and_uses_question_card():
    _app()
    overlay = SpotlightAssistantOverlay(on_submit=lambda text: True, can_submit=lambda: True)
    _open(overlay)
    overlay.finish_assistant("Hello, how may I assist you?")
    QTest.qWait(100)

    overlay.input_bar.input.setText("weather")
    overlay.input_bar._request_submit()
    QTest.qWait(100)
    overlay.finish_assistant("Which city should I check?")
    QTest.qWait(220)

    visible = " ".join(
        label.text() for label in overlay.findChildren(QLabel)
        if label.objectName() == "spotlightMessageText"
    )
    assert "Which city should I check?" in visible
    assert "how may I assist" not in visible
    assert overlay.findChild(QLabel, "spotlightQuestionPrompt") is not None
    assert overlay.input_bar.input.placeholderText() == "Type your answer..."
    _close(overlay)


def test_greeting_question_uses_plain_response_and_compact_height():
    _app()
    overlay = SpotlightAssistantOverlay(on_submit=lambda text: True, can_submit=lambda: True)
    _open(overlay)
    overlay.finish_assistant("Hello Arnav. How can I assist you today?")
    QTest.qWait(700)

    assert overlay.findChild(QLabel, "spotlightQuestionPrompt") is None
    assert overlay.input_bar.input.placeholderText() == "Ask NEO anything…"
    assert overlay.results.preferred_height() < 72
    assert overlay.height() < COLLAPSED_HEIGHT + 72
    _close(overlay)


def test_install_confirmation_uses_dedicated_card():
    _app()
    overlay = SpotlightAssistantOverlay(on_submit=lambda text: True, can_submit=lambda: True)
    _open(overlay)
    overlay.show_install_confirmation(
        "Claude Code", "WinGet", "winget install --id Anthropic.ClaudeCode --exact"
    )
    QTest.qWait(300)

    assert overlay.findChild(QLabel, "spotlightInstallTitle").text() == "Claude Code"
    assert overlay.findChild(QLabel, "spotlightInstallSource").text() == "Verified through WinGet"
    command = overlay.findChild(QLabel, "spotlightInstallCommand")
    assert "Anthropic.ClaudeCode" in command.text()
    assert command.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert overlay.findChild(QLabel, "spotlightQuestionPrompt") is None
    _close(overlay)


def test_internal_model_analysis_is_detected_before_rendering():
    from ui import _is_internal_model_text

    leaked = (
        "**Processing User Input**\n\n"
        "I've analyzed the user's message. My response incorporates a professional tone, "
        "reflecting my identity and the system context."
    )
    assert _is_internal_model_text(leaked)
    assert _is_internal_model_text("**Sequencing My Approach**")
    assert _is_internal_model_text(
        "**Confirming download absence**\n\n"
        "I've hit a roadblock. My search for a direct download link came up empty."
    )
    assert not _is_internal_model_text("Hello, how may I assist you?")


def test_gfm_rendering_and_max_height_scroll_cap():
    _app()
    overlay = SpotlightAssistantOverlay(on_submit=lambda text: True, can_submit=lambda: True)
    _open(overlay)
    overlay.input_bar.input.setText("format this")
    overlay.input_bar._request_submit()
    QTest.qWait(180)
    markdown = (
        "**Bold result**\n\n"
        "| Item | Status |\n|---|---|\n| One | Done |\n\n"
        + "\n".join(f"- detail {i}" for i in range(30))
    )
    overlay.finish_assistant(markdown)
    QTest.qWait(280)

    assistant_labels = [
        label.text() for label in overlay.findChildren(QLabel)
        if label.objectName() == "spotlightMessageText"
    ]
    rendered = " ".join(assistant_labels)
    assert "<strong>Bold result</strong>" in rendered
    assert "<table>" in rendered
    assert overlay.height() <= MAX_OVERLAY_HEIGHT
    assert overlay.results._scroll.verticalScrollBar().maximum() > 0
    _close(overlay)


def test_escape_is_the_only_path_that_restores_orb_state():
    _app()
    closed = threading.Event()
    overlay = SpotlightAssistantOverlay(on_closed=closed.set)
    _open(overlay)

    QTest.keyClick(overlay.input_bar.input, Qt.Key.Key_Escape)
    QTest.qWait(280)

    assert not overlay.isVisible()
    assert overlay.state == SpotlightState.ORB_VISIBLE
    assert closed.is_set()


def test_open_type_submit_stream_close_has_no_painter_warnings():
    _app()
    messages: list[str] = []

    def handler(message_type, context, message):
        messages.append(message)

    previous = qInstallMessageHandler(handler)
    try:
        overlay = SpotlightAssistantOverlay(
            on_submit=lambda text: None,
            can_submit=lambda: True,
        )
        _open(overlay)
        QTest.keyClicks(overlay.input_bar.input, "paint safely")
        overlay.input_bar._request_submit()
        QTest.qWait(240)
        overlay.stream_assistant("Painting remains inside paintEvent with valid geometry.")
        QTest.qWait(240)
        overlay.finish_assistant("Finished.")
        QTest.qWait(220)
        _close(overlay)
    finally:
        qInstallMessageHandler(previous)

    painter_messages = [
        message for message in messages
        if "QPainter" in message or "QWidgetEffectSourcePrivate" in message
    ]
    assert painter_messages == []


def test_orb_double_click_requests_spotlight_not_legacy_panel():
    from ui_siri_bar import SiriBarWindow

    _app()
    orb = SiriBarWindow(main_window=None)
    spy = QSignalSpy(orb.spotlight_toggle_requested)
    QTest.mouseDClick(orb, Qt.MouseButton.LeftButton)
    assert spy.count() == 1
    assert not orb.is_expanded()
    orb.hide()


def test_orb_show_paths_are_suppressed_while_spotlight_owns_surface():
    from ui_siri_bar import SiriBarWindow

    _app()
    orb = SiriBarWindow(main_window=None)
    orb.set_spotlight_suppressed(True)
    orb.req_show_compact.emit()
    QTest.qWait(80)
    assert not orb.isVisible()
    assert orb._spotlight_suppressed is True
