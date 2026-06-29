"""Unit tests for core/session_manager.py — session state machine."""

from __future__ import annotations


class _FakeUI:
    """Minimal fake UI for testing SessionManager."""
    def __init__(self):
        self.manual_mute = False
        self._state = ""
        self._standby = False
        self._mic_live = True
        self._logs = []
        self._hidden = False

    def set_state(self, s): self._state = s
    def set_standby(self, v): self._standby = v
    def set_mic_live(self, v): self._mic_live = v
    def siri_blocks_wake(self): return False
    def siri_schedule_hide(self, ms): self._hidden = True
    def siri_cancel_hide(self): self._hidden = False
    def siri_hide_now(self): self._hidden = True
    def siri_set_prompt(self, p): pass
    def write_log_siri_compact(self, msg): self._logs.append(msg)
    def start_log_progress(self, eta, label): pass
    def stop_log_progress(self): pass

    on_text_command = None
    on_force_listen = None
    _visuals_done_cb = None


def test_session_manager_initial_standby():
    from core.session_manager import SessionManager, MicPhase
    mgr = SessionManager(_FakeUI(), smart_mode=True)
    assert mgr.get_phase() == MicPhase.STANDBY


def test_session_manager_phase_transitions():
    from core.session_manager import SessionManager, MicPhase
    mgr = SessionManager(_FakeUI(), smart_mode=True)
    mgr.set_phase(MicPhase.USER_SPEAKING)
    assert mgr.get_phase() == MicPhase.USER_SPEAKING
    assert mgr.mic_live is True


def test_session_manager_go_standby():
    from core.session_manager import SessionManager, MicPhase
    ui = _FakeUI()
    mgr = SessionManager(ui, smart_mode=True)
    mgr.set_phase(MicPhase.USER_SPEAKING)
    mgr.go_standby()
    assert mgr.get_phase() == MicPhase.STANDBY
    assert mgr.mic_live is False
    assert ui._hidden is True


def test_session_manager_manual_mute():
    from core.session_manager import SessionManager, MicPhase
    ui = _FakeUI()
    mgr = SessionManager(ui, smart_mode=True)
    mgr.set_phase(MicPhase.USER_SPEAKING)
    ui.manual_mute = True
    assert mgr.mic_live is False


def test_session_manager_non_smart_mode():
    from core.session_manager import SessionManager, MicPhase
    mgr = SessionManager(_FakeUI(), smart_mode=False)
    assert mgr.get_phase() == MicPhase.USER_SPEAKING
    assert mgr.mic_live is True


def test_session_manager_processing_guard():
    from core.session_manager import SessionManager
    mgr = SessionManager(_FakeUI(), smart_mode=True)
    mgr.enter_processing(eta_sec=5, label="Testing...")
    assert mgr._processing is True
    mgr.finish_processing()
    assert mgr._processing is False


def test_session_manager_show_status():
    from core.session_manager import SessionManager
    ui = _FakeUI()
    mgr = SessionManager(ui, smart_mode=True)
    mgr.show_status_text("Hello world")
    assert any("Hello world" in l for l in ui._logs)
