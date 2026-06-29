"""Memory extensions — auto-loading, session injection, smart store.

Extends core.memory_rag with:
- get_session_memory_block() — injects ALL memories at session start
- store_memory_smart() — deduplicated storage
- get_memory_stats() — memory analytics
"""

from __future__ import annotations

import logging
from typing import Any

from core.memory_rag import (
    _init_db,
    _collection,
    _format_memories_block,
    format_memory_for_prompt,
    retrieve_relevant_memory,
    store_memory,
)

logger = logging.getLogger(__name__)


def get_all_memories(
    limit: int = 50,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Return all stored memories, optionally filtered by category."""
    _init_db()
    if not _collection:
        return []

    try:
        where_clause: dict[str, str] | None = (
            {"category": category} if category else None
        )
        results = _collection.get(
            where=where_clause,
            limit=limit,
            include=["documents", "metadatas"],
        )
        memories: list[dict[str, Any]] = []
        if results and results.get("documents"):
            for doc, meta in zip(
                results["documents"],
                results["metadatas"] or [],
            ):
                memories.append({
                    "content": doc,
                    "metadata": meta or {},
                    "distance": 0.0,
                })
        return memories
    except Exception as exc:
        logger.error("Failed to fetch all memories: %s", exc)
        return []


def get_session_memory_block(
    *,
    recent_context: str = "",
    max_memories: int = 30,
) -> str:
    """Build comprehensive memory block for session injection.

    Retrieves ALL stored memories, adds relevance-matched ones for current
    context, deduplicates, and formats as an LLM-ready system prompt block.

    Should be called once at session start and injected into system_instruction.
    """
    all_memories = get_all_memories(limit=max_memories)

    if recent_context:
        relevant = retrieve_relevant_memory(recent_context, top_k=10)
    else:
        relevant = []

    seen: set[str] = set()
    combined: list[dict[str, Any]] = []
    for mem in all_memories + relevant:
        key = (mem.get("content", "") or "").strip()[:120]
        if key and key not in seen:
            seen.add(key)
            combined.append(mem)

    if not combined:
        return ""

    return _format_memories_block(combined, compact=False)


def store_memory_smart(category: str, content: str) -> bool:
    """Store with deduplication — skips near-duplicate memories.

    Returns False if a near-identical memory already exists,
    True if the memory was stored successfully.
    """
    existing = retrieve_relevant_memory(content, top_k=3, category=category)
    for mem in existing:
        if mem.get("distance", 2.0) < 0.15:
            return False

    return store_memory(category, content)


def get_memory_stats() -> dict[str, int]:
    """Return counts of memories by category for analytics."""
    _init_db()
    if not _collection:
        return {}

    try:
        results = _collection.get(include=["metadatas"])
        counts: dict[str, int] = {}
        if results and results.get("metadatas"):
            for meta in results["metadatas"]:
                cat = (meta or {}).get("category", "general")
                counts[cat] = counts.get(cat, 0) + 1
        return counts
    except Exception as exc:
        logger.error("Failed to fetch memory stats: %s", exc)
        return {}
