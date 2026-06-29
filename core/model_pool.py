"""
SmartModelPool — task-aware model routing for NEO.

Instead of a fixed fallback chain, the pool groups models by capability tier
and task affinity, then picks the best *healthy* model with the lowest recorded
latency for each call.  If a model fails it is demoted and the next best is tried.

Usage
-----
    from core.model_pool import pool_ask

    answer = pool_ask("Summarise this text…", task="fast")
    answer = pool_ask("Solve this maths proof", task="reasoning")
    answer = pool_ask("Write a Python parser", task="coding")
"""

from __future__ import annotations

import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

@dataclass
class ModelEntry:
    """A single model in the pool."""
    model_id: str          # nim/..., groq/..., gemini/..., etc.
    tasks: set[str]        # task tags this model is good at
    speed: int             # 1=fastest … 5=slowest
    power: int             # 1=lightest … 5=most capable
    avg_latency_ms: float = 0.0
    failure_count: int = 0
    healthy: bool = True
    last_failure: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    COOLDOWN_S: float = 90.0   # seconds before an unhealthy model is retried

    def is_healthy(self) -> bool:
        if self.healthy:
            return True
        if time.time() - self.last_failure > self.COOLDOWN_S:
            with self._lock:
                self.healthy = True
                self.failure_count = 0
            logger.info("[Pool] %s auto-recovered", self.model_id)
            return True
        return False

    def record_success(self, latency_ms: float) -> None:
        with self._lock:
            self.healthy = True
            self.failure_count = 0
            if self.avg_latency_ms == 0:
                self.avg_latency_ms = latency_ms
            else:
                self.avg_latency_ms = 0.7 * self.avg_latency_ms + 0.3 * latency_ms

    def record_failure(self) -> None:
        with self._lock:
            self.failure_count += 1
            self.last_failure = time.time()
            if self.failure_count >= 2:
                self.healthy = False
                logger.warning("[Pool] %s marked unhealthy after %d failures",
                               self.model_id, self.failure_count)


# ---------------------------------------------------------------------------
# The pool definition — ordered by (speed ASC, power ASC) within each tier
# ---------------------------------------------------------------------------

_ALL_MODELS: list[ModelEntry] = [
    # ── Tier 1: Ultra-fast / lightweight ────────────────────────────────────
    ModelEntry("gemini/gemini-2.5-flash-lite",          {"fast","general","chat"},              speed=1, power=2),
    ModelEntry("groq/llama-3.3-70b-versatile",          {"fast","general","chat","coding"},      speed=1, power=3),
    ModelEntry("nim/stepfun-ai/step-3.7-flash",         {"fast","general","chat"},              speed=1, power=3),
    ModelEntry("nim/deepseek-ai/deepseek-v4-flash",     {"fast","coding","reasoning","general"}, speed=1, power=3),

    # ── Tier 2: Balanced / general purpose ──────────────────────────────────
    ModelEntry("gemini/gemini-2.5-flash",               {"general","chat","reasoning","vision","coding"}, speed=2, power=4),
    ModelEntry("nvidia/meta/llama-3.3-70b-instruct",    {"general","chat","coding"},             speed=2, power=3),
    ModelEntry("nim/google/gemma-4-31b-it",             {"general","chat","creative"},           speed=2, power=3),
    ModelEntry("nim/z-ai/glm-5.1",                      {"general","chat","reasoning"},          speed=2, power=3),
    ModelEntry("nim/minimaxai/minimax-m2.7",            {"general","chat","creative"},           speed=2, power=3),

    # ── Tier 3: High power / reasoning ──────────────────────────────────────
    ModelEntry("nim/qwen/qwen3.5-122b-a10b",            {"reasoning","coding","general"},        speed=3, power=4),
    ModelEntry("nim/deepseek-ai/deepseek-v4-pro",       {"reasoning","coding","analysis"},       speed=3, power=5),
    ModelEntry("nim/minimaxai/minimax-m3",              {"creative","reasoning","general"},      speed=3, power=4),

    # ── Tier 4: Maximum power ────────────────────────────────────────────────
    ModelEntry("nim/nvidia/nemotron-3-ultra-550b-a55b", {"reasoning","analysis","coding"},       speed=4, power=5),
    ModelEntry("cometapi/gpt-4o",                       {"general","coding","reasoning"},        speed=3, power=4),
]

# Task aliases — maps user-facing hints to canonical tags
_TASK_ALIASES: dict[str, str] = {
    "quick": "fast", "simple": "fast", "short": "fast", "lite": "fast",
    "think": "reasoning", "complex": "reasoning", "deep": "reasoning",
    "math": "reasoning", "logic": "reasoning", "analyse": "analysis",
    "code": "coding", "debug": "coding", "script": "coding", "program": "coding",
    "write": "creative", "story": "creative", "poem": "creative",
    "image": "vision", "photo": "vision", "screenshot": "vision",
    "talk": "chat", "converse": "chat",
}


# ---------------------------------------------------------------------------
# SmartModelPool
# ---------------------------------------------------------------------------

class SmartModelPool:
    """Picks the best healthy model for a task, falls back through the pool."""

    def __init__(self, models: list[ModelEntry] | None = None) -> None:
        self._models = list(models or _ALL_MODELS)

    def _resolve_task(self, task: str | None) -> str:
        if not task:
            return "general"
        t = task.strip().lower()
        return _TASK_ALIASES.get(t, t)

    def candidates(self, task: str | None = None) -> list[ModelEntry]:
        """Return healthy models for task, sorted: matching-task first, then by speed, then by latency."""
        tag = self._resolve_task(task)

        healthy = [m for m in self._models if m.is_healthy()]
        if not healthy:
            # All unhealthy — return full list so we still try something
            healthy = self._models

        def sort_key(m: ModelEntry):
            tag_match = 0 if tag in m.tasks else 1   # matching tasks first
            latency = m.avg_latency_ms or 9999        # unknown latency → last
            return (tag_match, m.speed, latency)

        return sorted(healthy, key=sort_key)

    def ask(
        self,
        prompt: str | Sequence[Any],
        *,
        task: str | None = None,
        system: str | None = None,
        temperature: float | None = None,
        images: Sequence[Any] | None = None,
        json_mode: bool = False,
        max_candidates: int = 6,
    ) -> str:
        """Try models in best-for-task order; return first successful response."""
        from core.llm import ask as _ask  # avoid circular at import time

        pool = self.candidates(task)[:max_candidates]
        if not pool:
            raise RuntimeError("SmartModelPool: no models available")

        last_err: Exception | None = None
        for entry in pool:
            t0 = time.perf_counter()
            try:
                result = _ask(
                    prompt,
                    model=entry.model_id,
                    system=system,
                    temperature=temperature,
                    images=images,
                    json_mode=json_mode,
                )
                latency_ms = (time.perf_counter() - t0) * 1000
                entry.record_success(latency_ms)
                logger.debug("[Pool] %s succeeded in %.0fms (task=%s)", entry.model_id, latency_ms, task)
                return result
            except Exception as e:
                entry.record_failure()
                last_err = e
                logger.warning("[Pool] %s failed (%s), trying next…", entry.model_id, str(e)[:80])
                time.sleep(0.5)

        raise last_err or RuntimeError("SmartModelPool: all candidates failed")

    def status(self) -> list[dict]:
        """Return pool status for debugging."""
        out = []
        for m in self._models:
            out.append({
                "model": m.model_id,
                "healthy": m.is_healthy(),
                "failures": m.failure_count,
                "avg_latency_ms": round(m.avg_latency_ms, 1),
                "tasks": sorted(m.tasks),
                "speed": m.speed,
                "power": m.power,
            })
        return out


# ---------------------------------------------------------------------------
# Singleton + convenience function
# ---------------------------------------------------------------------------

_pool: SmartModelPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> SmartModelPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = SmartModelPool()
    return _pool


def pool_ask(
    prompt: str | Sequence[Any],
    *,
    task: str | None = None,
    system: str | None = None,
    temperature: float | None = None,
    images: Sequence[Any] | None = None,
    json_mode: bool = False,
    max_candidates: int = 6,
) -> str:
    """
    Ask the smart model pool.  Picks the best healthy model for the given task.

    task hints: 'fast', 'reasoning', 'coding', 'creative', 'vision', 'chat', 'analysis'
    """
    return get_pool().ask(
        prompt,
        task=task,
        system=system,
        temperature=temperature,
        images=images,
        json_mode=json_mode,
        max_candidates=max_candidates,
    )
