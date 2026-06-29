"""Conversation compaction — periodic summarization to prevent context loss.

Long conversations push older turns out of Gemini's context window.
This module periodically summarizes the conversation and injects
compressed context back, creating a rolling memory of the session.
"""

from __future__ import annotations

import logging
import re as _re
import time

from core.llm import ask
from core.models import FAST_UTILITY
from core.memory_ext import store_memory_smart

_MEM_COMPACTOR = FAST_UTILITY

logger = logging.getLogger(__name__)

# Summarize after this many turns
_SUMMARIZE_EVERY = 12

# Store last N summaries in a rolling window
_MAX_SUMMARIES = 4

_COMPACTOR_PROMPT = """Summarize the conversation excerpt below into a single dense paragraph.

Include:
- What the user asked for (goals, tasks)
- What NEO did (actions, results)
- Key decisions or preferences revealed
- Any errors and how they were resolved

Be concise. One paragraph, 3-6 sentences. No bullet points.
Return ONLY the summary text, no JSON, no markdown.
"""


class ConversationCompactor:
    """Rolling summarizer that keeps long sessions coherent."""

    def __init__(self) -> None:
        self._turns: list[str] = []
        self._summaries: list[str] = []
        self._turn_count = 0
        self._last_summary_at = 0

    def record_turn(self, user_text: str, neo_response: str) -> None:
        """Record a conversation turn."""
        turn = (
            f"User: {user_text[:300]}\n"
            f"NEO: {(neo_response or '')[:500]}"
        )
        self._turns.append(turn)
        self._turn_count += 1

    def should_summarize(self) -> bool:
        """Return True if enough turns have passed since last summary."""
        return (
            self._turn_count - self._last_summary_at >= _SUMMARIZE_EVERY
        )

    def summarize(self, *, blocking: bool = True) -> str | None:
        """Summarize recent turns and store as compressed context.

        Returns the summary text, or None if nothing to summarize.
        """
        if len(self._turns) < 5:
            return None

        try:
            excerpt = "\n\n".join(self._turns[-_SUMMARIZE_EVERY:])
            summary = ask(
                excerpt,
                model=_MEM_COMPACTOR,
                system=_COMPACTOR_PROMPT,
                temperature=0.2,
            )
            summary = summary.strip()
            if not summary:
                return None

            self._summaries.append(summary)
            if len(self._summaries) > _MAX_SUMMARIES:
                self._summaries = self._summaries[1:]

            self._last_summary_at = self._turn_count

            # Store as a persistent memory with timestamp for cross-session recall
            store_memory_smart(
                "conversation",
                f"Session summary ({time.strftime('%Y-%m-%d %H:%M')}): {summary}",
            )

            logger.debug(
                "Compacted %d turns → %d summaries (latest: %d chars)",
                self._turn_count, len(self._summaries), len(summary),
            )
            return summary
        except Exception as exc:
            logger.warning("Compaction failed: %s", exc)
            return None

    def get_context_block(self, *, include_persistent: bool = True) -> str:
        """Build a conversation context block for system prompt injection.

        When include_persistent is True, also fetches summaries from previous
        sessions stored in ChromaDB, creating cross-session continuity.
        """
        parts = []
        if not self._summaries and not include_persistent:
            return ""

        if include_persistent:
            try:
                from core.memory_rag import retrieve_relevant_memory
                # Fetch summaries from previous sessions
                prev = retrieve_relevant_memory(
                    "recent conversation summary", top_k=3,
                    category="conversation",
                )
                if prev:
                    parts.append("--- PREVIOUS SESSIONS ---\n")
                    for i, mem in enumerate(prev[:2]):
                        if mem.get("distance", 2.0) < 1.5:
                            parts.append(f"  [{i + 1}] {mem['content'][:300]}")
                    parts.append("")
            except Exception:
                pass

        if self._summaries:
            parts.append("--- CONVERSATION HISTORY (compressed) ---\n")
            parts.append("Earlier in this session:\n")
            for i, summary in enumerate(self._summaries[-_MAX_SUMMARIES:]):
                parts.append(f"  [{i + 1}] {summary}")

            # Add recent unsummarised turns
            recent = self._turns[-_SUMMARIZE_EVERY:]
            if recent:
                parts.append("\nRecent exchanges:\n")
                for turn in recent[-5:]:
                    parts.append(f"  {turn[:200]}")

        return "\n".join(parts)


# Singleton for the session
_compactor: ConversationCompactor | None = None


def get_compactor() -> ConversationCompactor:
    """Return the session-level conversation compactor singleton."""
    global _compactor
    if _compactor is None:
        _compactor = ConversationCompactor()
    return _compactor


def reset_compactor() -> None:
    """Reset for a new session."""
    global _compactor
    _compactor = ConversationCompactor()
