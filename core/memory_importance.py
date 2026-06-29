"""Memory importance scoring — tiered weights, access boosting, decay.

Tiers: critical(10) > high(7) > medium(4) > low(2) > trivial(1)
Decay: low/trivial memories older than threshold are auto-pruned.
Access boost: each retrieval bumps a memory's score slightly.
"""

from __future__ import annotations

import json as _json
import logging
import threading
import time
from collections import defaultdict
from typing import Any

from core.paths import base_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

TIER_SCORES: dict[str, int] = {
    "critical": 10, "high": 7, "medium": 4, "low": 2, "trivial": 1,
}

_CATEGORY_TIERS: dict[str, str] = {
    "preference": "high", "contact": "high", "user_info": "critical",
    "lesson": "medium", "project": "medium", "code": "low",
    "conversation": "low", "notes": "low", "search": "trivial",
    "general": "low",
}

# Days before memory is eligible for pruning (None = never)
_DECAY_DAYS: dict[str, int | None] = {
    "critical": None, "high": 365, "medium": 90, "low": 30, "trivial": 7,
}

_RETRIEVAL_BOOST = 0.15
_BOOST_TRACKER_PATH = base_dir() / "memory" / "access_tracker.json"


# ---------------------------------------------------------------------------
# Access tracker
# ---------------------------------------------------------------------------

class AccessTracker:
    """Tracks how many times each memory was accessed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = defaultdict(int)
        self._load()

    def _load(self) -> None:
        try:
            if _BOOST_TRACKER_PATH.exists():
                data = _json.loads(
                    _BOOST_TRACKER_PATH.read_text(encoding="utf-8")
                )
                self._counts = defaultdict(int, data.get("counts", {}))
        except Exception:
            pass

    def _save(self) -> None:
        try:
            _BOOST_TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
            _BOOST_TRACKER_PATH.write_text(
                _json.dumps({"counts": dict(self._counts)}),
                encoding="utf-8",
            )
        except Exception:
            pass

    def record_access(self, memory_id: str) -> None:
        with self._lock:
            self._counts[memory_id] += 1
            if self._counts[memory_id] % 10 == 0:
                self._save()



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def tier_for_category(category: str) -> str:
    """Map a memory category to its importance tier."""
    return _CATEGORY_TIERS.get(category, "low")


def importance_score(
    *,
    category: str = "general",
    access_count: int = 0,
    age_days: float = 0.0,
    override_tier: str | None = None,
) -> float:
    """Calculate a floating importance score.

    Formula: base_tier_score + (access_count * boost) - age_penalty
    """
    tier = override_tier or tier_for_category(category)
    base = float(TIER_SCORES.get(tier, 1))
    boost = min(access_count * _RETRIEVAL_BOOST, 2.0)
    decay_days = _DECAY_DAYS.get(tier)
    if decay_days is None:
        penalty = 0.0
    else:
        penalty = max(0.0, (age_days - decay_days) * 0.05) if age_days > decay_days else 0.0
    return base + boost - penalty


def is_expired(*, category: str, age_days: float) -> bool:
    """Check if a memory should be pruned based on age."""
    tier = tier_for_category(category)
    decay_days = _DECAY_DAYS.get(tier)
    if decay_days is None:
        return False
    return age_days > decay_days * 2


def format_importance_for_prompt(memories: list[dict[str, Any]]) -> str:
    """Sort and filter memories by importance, return prompt-ready block.

    Critical + high always included. Medium only if distance < 1.2.
    Low/trivial filtered out entirely.
    """
    scored = []
    for mem in memories:
        meta = mem.get("metadata", {}) or {}
        cat = meta.get("category", "general")
        tier = tier_for_category(cat)

        if tier in ("low", "trivial"):
            continue

        dist = mem.get("distance", 0.0)
        if tier == "medium" and dist > 1.2:
            continue

        score = importance_score(
            category=cat,
            access_count=int(meta.get("access_count", 0)),
            age_days=float(meta.get("age_days", 0)),
        )
        scored.append((score, mem))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return ""

    lines = ["--- NEO'S MEMORIES (ranked) ---\n"]
    lines.append(
        "You have stored knowledge about this user. "
        "Higher importance items reflect strong preferences.\n"
    )

    for score, mem in scored[:25]:
        meta = mem.get("metadata", {}) or {}
        cat = meta.get("category", "general")
        content = mem.get("content", "").strip()
        if not content:
            continue
        imp_marker = "⭐" if score >= 8 else ("◆" if score >= 5 else "·")
        lines.append(f"  {imp_marker} [{cat}] {content}")

    lines.append(f"\n  ({len(scored)} memories)")
    return "\n".join(lines)

    def get_count(self, memory_id: str) -> int:
        return self._counts.get(memory_id, 0)


_tracker = AccessTracker()
