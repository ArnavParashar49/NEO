"""Unit tests for core/memory_suggestions.py — after eval() fix."""

from __future__ import annotations


def test_sequence_tracker_load_no_file():
    """SequenceTracker should not crash when the sequence file does not exist."""
    from core.memory_suggestions import SequenceTracker
    tracker = SequenceTracker()
    assert tracker._patterns is not None


def test_memory_suggestion_empty_text():
    from core.memory_suggestions import get_memory_suggestion
    assert get_memory_suggestion("") is None
    assert get_memory_suggestion("   ") is None


def test_memory_suggestion_short_text():
    from core.memory_suggestions import get_memory_suggestion
    assert get_memory_suggestion("hi") is None
    assert get_memory_suggestion("ok") is None
