"""Auto-extract memories from conversation turns.

Watches every user↔NEO exchange and uses a lightweight LLM to extract
structured facts (preferences, facts, contacts, lessons). Stored via
store_memory_smart with auto-deduplication.
"""

from __future__ import annotations

import logging
import re as _re
import threading

from core.llm import ask
from core.memory_ext import store_memory_smart

# Groq Llama 3.1 8B — ultra-fast (500+ tok/s), ideal for lightweight extraction
_MEM_EXTRACTOR = "groq/llama-3.1-8b-instant"

logger = logging.getLogger(__name__)

# Minimal token usage
_EXTRACTOR_PROMPT = """Extract structured facts from this conversation as JSON.

RULES:
- Extract ONLY new, factual information the user revealed.
- Skip: greetings, commands (open/close/search), reactions, "thanks", filler.
- Use these categories: user_info | preference | contact | lesson | project | fact
- user_info: name, location, job, tools they use, personal details
- preference: things they like/dislike, preferred settings, habits
- contact: people they mention, relationships
- lesson: mistakes, fixes, "don't do X", workarounds
- project: context about work/projects they're doing
- fact: specific facts that don't fit above
- Be concise. One sentence per fact. Max 5 facts.
- If nothing is new or extractable, return empty list.

Return ONLY valid JSON:
{"facts": [{"category": "...", "content": "..."}]}
"""

# Don't extract on very short / command-only messages
_MIN_WORDS_TO_EXTRACT = 4

# Run extraction in background thread to never block the user
_extraction_executor = None


def _get_executor():
    global _extraction_executor
    if _extraction_executor is None:
        from concurrent.futures import ThreadPoolExecutor
        _extraction_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-obs")
    return _extraction_executor


def observe_turn(user_text: str, neo_response: str, *, blocking: bool = False) -> int:
    """Analyze a conversation turn and auto-extract facts.

    Called after each user→NEO exchange. Runs in background thread
    unless blocking=True (for tests).

    Returns number of facts extracted.
    """
    text = (user_text or "").strip()
    if not text or len(text.split()) < _MIN_WORDS_TO_EXTRACT:
        return 0

    if blocking:
        return _extract_and_store(text, neo_response)
    else:
        _get_executor().submit(_extract_and_store, text, neo_response)
        return 0  # Fire-and-forget, count unknown


def _extract_and_store(user_text: str, neo_response: str) -> int:
    """Core extraction logic — runs in thread."""
    try:
        conversation = (
            f"USER: {user_text[:500]}\n"
            f"NEO: {(neo_response or '')[:300]}"
        )

        raw = ask(
            conversation,
            model=_MEM_EXTRACTOR,
            system=_EXTRACTOR_PROMPT,
            temperature=0.1,
        )
        raw = _re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

        import json as _json
        data = _json.loads(raw)
        facts = data.get("facts", [])
        if not isinstance(facts, list):
            return 0

        stored = 0
        for fact in facts:
            cat = fact.get("category", "notes")
            content = fact.get("content", "").strip()
            if not content or len(content) < 6:
                continue
            if store_memory_smart(cat, content):
                stored += 1
                logger.debug("Auto-extracted [%s]: %s", cat, content[:60])

        return stored
    except Exception as exc:
        logger.debug("Memory extraction skipped: %s", exc)
        return 0
