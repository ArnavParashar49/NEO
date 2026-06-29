"""Proactive suggestion engine — anticipates user needs from learned patterns.

Watches tool sequences and, when a known pattern triggers, proactively offers
to help with the follow-up task the user typically needs next.

Zero LLM cost — pure pattern matching against stored tool-sequence memories.
"""

from __future__ import annotations

import json as _json
import logging
import threading
import time
from collections import defaultdict
from pathlib import Path

from core.memory_ext import retrieve_relevant_memory, store_memory_smart
from core.paths import base_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool sequence tracker
# ---------------------------------------------------------------------------

_SEQUENCE_FILE = base_dir() / "memory" / "tool_sequences.json"
_SEQUENCE_WINDOW = 3  # Track sequences of N recent tools
_SUGGESTION_COOLDOWN = 120  # Don't suggest same thing more than once per 2 min


class SequenceTracker:
    """Tracks tool call sequences to learn user workflow patterns."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recent: list[str] = []
        # pattern: (tool_a, tool_b) → {next_tool: count}
        self._patterns: dict[tuple, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._last_suggested: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        try:
            if _SEQUENCE_FILE.exists():
                data = _json.loads(_SEQUENCE_FILE.read_text(encoding="utf-8"))
                raw = data.get("patterns", {})
                self._patterns = defaultdict(
                    lambda: defaultdict(int),
                    {tuple(_json.loads(k)): defaultdict(int, v) for k, v in raw.items()},
                )
        except Exception:
            pass

    def _save(self) -> None:
        try:
            _SEQUENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
            serialised = {
                str(list(k)): dict(v) for k, v in self._patterns.items()
            }
            _SEQUENCE_FILE.write_text(
                _json.dumps({"patterns": serialised}),
                encoding="utf-8",
            )
        except Exception:
            pass

    def record_tool(self, tool_name: str) -> None:
        """Record a tool call and update sequence patterns."""
        with self._lock:
            self._recent.append(tool_name)
            if len(self._recent) > _SEQUENCE_WINDOW:
                self._recent.pop(0)

            if len(self._recent) >= 2:
                key = tuple(self._recent[-2:])
                self._patterns[key][tool_name] += 1

            if len(self._recent[-1]) and self._patterns:
                self._save()

    def get_suggestion(self) -> str | None:
        """If the recent tool sequence matches a known pattern, suggest next.

        Returns a suggestion string, or None if nothing to suggest.
        """
        with self._lock:
            if len(self._recent) < 2:
                return None

            key = tuple(self._recent[-2:])
            follow_ups = self._patterns.get(key, {})
            if not follow_ups:
                return None

            # Find the most common follow-up that hasn't been suggested recently
            now = time.time()
            for next_tool, count in sorted(
                follow_ups.items(), key=lambda x: x[1], reverse=True
            ):
                last = self._last_suggested.get(next_tool, 0)
                if now - last > _SUGGESTION_COOLDOWN and count >= 2:
                    self._last_suggested[next_tool] = now
                    return self._suggestion_text(next_tool)

            return None

    @staticmethod
    def _suggestion_text(tool: str) -> str:
        suggestions = {
            "web_search": "Want me to search for anything related?",
            "weather_report": "Want the forecast for tomorrow too?",
            "flight_finder": "Need a hotel or car rental at your destination?",
            "file_controller": "Want me to organize your recent files?",
            "browser_control": "Need me to open any related pages?",
            "calendar_control": "Should I check tomorrow's schedule too?",
            "reminder": "Want me to set a follow-up reminder?",
            "notes_control": "Should I save this as a note?",
            "send_email": "Want me to CC anyone else?",
            "desktop_control": "Should I clean up the desktop while I'm at it?",
        }
        return suggestions.get(tool)


# Singleton
_tracker = SequenceTracker()


def record_tool_call(tool_name: str) -> None:
    """Record a tool call for pattern learning."""
    _tracker.record_tool(tool_name)


def get_suggestion() -> str | None:
    """Check if the recent tools suggest a follow-up action."""
    return _tracker.get_suggestion()


# ---------------------------------------------------------------------------
# Memory-based suggestions (recall past similar contexts)
# ---------------------------------------------------------------------------


def get_memory_suggestion(user_text: str) -> str | None:
    """Check memories for relevant past actions and offer to repeat them.

    Example: User says "open VS Code" and NEO remembers they always
    ask for the last project next.
    """
    if not user_text or len(user_text.split()) < 3:
        return None

    memories = retrieve_relevant_memory(user_text, top_k=3, category="lesson")
    if not memories:
        return None

    for mem in memories[:1]:
        content = mem.get("content", "")
        if "project" in content.lower() or "always" in content.lower():
            return f"I remember you usually work with: {content[:100]}. Want me to set that up?"

    return None
