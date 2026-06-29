"""Session lifecycle and state management for NEO.

Extracted from the 2200-line ``main.py`` to make the session state machine,
connection lifecycle, and standby logic independently testable.

Usage::

    mgr = SessionManager(ui)
    mgr.set_phase(MicPhase.USER_SPEAKING)
    mgr.go_standby()
    config = mgr.build_config()
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from enum import Enum, auto

from google.genai import types

from core.models import VOICE_LIVE

logger = logging.getLogger(__name__)

# ── Re-exported constants from main.py ──────────────────────────────────
LIVE_MODEL = VOICE_LIVE
FOLLOWUP_SECONDS = 7
LISTEN_TIMEOUT_SEC = 7
SIRI_IDLE_HIDE_SEC = 7
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024


class MicPhase(Enum):
    STANDBY = auto()       # muted — wake / clap only
    WAKE_SPEAKING = auto() # muted — NEO says greeting
    USER_SPEAKING = auto() # mic open — user talks
    AI_RESPONDING = auto() # muted — NEO thinking / speaking / tools
    FOLLOWUP = auto()      # mic open — 5 s window after NEO reply


class SessionManager:
    """Owns the microphone state machine, Gemini Live session, and UI callbacks.

    Responsibilities:
    - Phase transitions (standby ↔ listening ↔ responding)
    - Gemini Live ``run()`` loop with reconnection backoff
    - Orb hide/show scheduling
    - System prompt / config building
    """

    def __init__(self, ui, *, smart_mode: bool = True) -> None:
        self.ui = ui
        self.session = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._smart_mode = smart_mode

        # ── Phase state ──
        self._mic_phase = MicPhase.STANDBY if smart_mode else MicPhase.USER_SPEAKING
        self._phase_lock = threading.Lock()
        self._user_spoke_this_turn = False
        self._ack_spoken_this_turn = False
        self._followup_deadline = 0.0
        self._listen_deadline = 0.0
        self._wake_block_until = 0.0
        self._wake_listen_blocked_until = 0.0
        self._orb_hide_at = 0.0
        self._had_conversation = False
        self._suppress_live_audio_until = 0.0
        self._followup_voice_frames = 0
        self._followup_voice_engaged = False

        # ── Processing guard ──
        self._processing = False
        self._processing_status_msg = ""
        self._processing_keepalive_stop = threading.Event()

        # ── Text buffers (shared between threads) ──
        self._last_user_log = ""
        self._user_line_logged = False
        self._last_neo_reply = ""
        self._last_search_result = ""

        # ── Asyncio queues (set during connect) ──
        self.audio_in_queue: asyncio.Queue | None = None
        self.out_queue: asyncio.Queue | None = None
        self._turn_done_event: asyncio.Event | None = None
        self._tool_send_lock: asyncio.Lock | None = None

        # ── UI wiring ──
        self.ui.on_text_command = self._on_text_command
        self.ui._visuals_done_cb = self.finish_processing
        self.ui.on_force_listen = self._force_listen

        if smart_mode:
            self.ui.set_standby(True)
            self.ui.set_mic_live(False)
        else:
            self.ui.set_mic_live(True)

    # ── Phase API ───────────────────────────────────────────────────────

    def get_phase(self) -> MicPhase:
        with self._phase_lock:
            return self._mic_phase

    def set_phase(self, phase: MicPhase) -> None:
        with self._phase_lock:
            self._mic_phase = phase
        if not self._smart_mode:
            self.ui.set_mic_live(True)
            return

        mic_live = phase in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP)
        self.ui.set_mic_live(mic_live)
        if not mic_live:
            self._drain_out_queue()

        if phase == MicPhase.STANDBY:
            self.ui.set_standby(True)
            self.ui.set_state("STANDBY")
        elif phase in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP, MicPhase.WAKE_SPEAKING):
            self._listen_deadline = time.time() + LISTEN_TIMEOUT_SEC
            self.ui.set_standby(False)
            self.ui.siri_set_prompt("I'm listening\u2026")
            self.ui.set_state("LISTENING")
        elif phase == MicPhase.AI_RESPONDING:
            self.ui.set_standby(False)
            self.ui.set_state("THINKING")

    @property
    def mic_live(self) -> bool:
        if self.ui.manual_mute:
            return False
        if not self._smart_mode:
            return True
        return self.get_phase() in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP)

    # ── Standby / orb hide ──────────────────────────────────────────────

    def reset_orb_hide_deadline(self, seconds: float | None = None) -> None:
        if self.ui.siri_blocks_wake():
            return
        sec = seconds if seconds is not None else SIRI_IDLE_HIDE_SEC
        self._orb_hide_at = time.time() + sec
        self.ui.siri_schedule_hide(int(sec * 1000))

    def clear_orb_hide_deadline(self) -> None:
        self._orb_hide_at = 0.0
        self.ui.siri_cancel_hide()

    def go_standby(self) -> None:
        """Return to wake-word standby and hide the open mic."""
        self.clear_orb_hide_deadline()
        self.finish_processing(schedule_hide=False)
        self._user_spoke_this_turn = False
        self._user_line_logged = False
        self._listen_deadline = 0.0
        self._followup_deadline = 0.0
        self._turn_finalize_pending = False
        self._neo_streaming = False
        self.set_phase(MicPhase.STANDBY)
        self.ui.siri_hide_now()

    def _begin_followup(self, open_mic_immediately: bool = False) -> None:
        """Switch to follow-up listening mode after NEO finishes speaking."""
        self._followup_deadline = time.time() + FOLLOWUP_SECONDS
        self._followup_voice_frames = 0
        self._followup_voice_engaged = open_mic_immediately
        self._ack_spoken_this_turn = False
        self._user_line_logged = False
        self.set_phase(MicPhase.FOLLOWUP)
        self.ui.siri_wake()
        self.reset_orb_hide_deadline()

    # ── Processing guard ────────────────────────────────────────────────

    def enter_processing(self, eta_sec: int = 15, label: str = "") -> None:
        self.ui.siri_cancel_hide()
        self._processing = True
        now = time.time()
        self._wake_block_until = max(self._wake_block_until, now + 180.0)
        self._wake_listen_blocked_until = max(self._wake_listen_blocked_until, now + 180.0)
        phase = self.get_phase()
        if phase in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
            self._drain_out_queue()
            self.set_phase(MicPhase.AI_RESPONDING)
        self.ui.start_log_progress(eta_sec, label or self._processing_status_msg or "Working...")
        self._start_processing_keepalive()

    def finish_processing(self, *, schedule_hide: bool = True) -> None:
        self._processing = False
        self._stop_processing_keepalive()
        self._processing_status_msg = ""
        self.ui.stop_log_progress()
        phase = self.get_phase()
        if (
            schedule_hide
            and phase in (MicPhase.FOLLOWUP, MicPhase.USER_SPEAKING)
            and not self.ui.siri_blocks_wake()
        ):
            self.reset_orb_hide_deadline()

    def _start_processing_keepalive(self):
        self._processing_keepalive_stop.clear()

        def loop():
            while not self._processing_keepalive_stop.wait(25.0):
                if not self._processing:
                    break

        threading.Thread(target=loop, daemon=True, name="NEO-keepalive").start()

    def _stop_processing_keepalive(self):
        self._processing_keepalive_stop.set()

    # ── Connection lifecycle ────────────────────────────────────────────

    def reset_live_session_state(self) -> None:
        """Clear stuck flags after a dropped live session."""
        self._processing = False
        self.finish_processing()
        self._neo_streaming = False
        self._turn_finalize_pending = False
        self._wake_listen_blocked_until = 0.0
        if self.audio_in_queue:
            while not self.audio_in_queue.empty():
                try:
                    self.audio_in_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._drain_out_queue()
        try:
            from core.conversation_compactor import reset_compactor
            reset_compactor()
        except Exception:
            pass

    def _drain_out_queue(self):
        if not self.out_queue:
            return
        while True:
            try:
                self.out_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def show_status_text(self, text: str) -> None:
        if text and text.strip():
            self.ui.write_log_siri_compact(f"Neo: {text.strip()[:280]}")

    # ── Placeholder stubs (overridden externally) ───────────────────────

    on_input_transcription = None
    _neo_streaming = False
    _turn_finalize_pending = False

    def _on_text_command(self, text: str) -> None:
        pass  # Overridden in NeoLive._on_text_command

    def _force_listen(self) -> None:
        pass  # Overridden in NeoLive._force_listen


# ── Transcript helpers (shared with audio_pipeline) ────────────────────

_CTRL_RE = None


def _clean_transcript(text: str) -> str:
    import re
    global _CTRL_RE
    if _CTRL_RE is None:
        _CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()


def _merge_transcript(parts: list[str], new: str) -> list[str]:
    new = new.strip()
    if not new:
        return parts
    joined = " ".join(parts).strip()
    if not joined:
        return [new]
    if new == joined or new in joined:
        return parts
    if new.startswith(joined) or len(new) > len(joined) + 2:
        return [new]
    return parts + [new]
