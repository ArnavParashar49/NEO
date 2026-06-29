"""Error recovery — circuit breakers, smart retry, graceful degradation.

Circuit breaker: 3 failures in 60s → stop tool, tell user.
Smart retry: rate-limit → exponential backoff; auth → stop immediately.
Graceful degradation: each tool has a fallback chain.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-tool circuit breaker."""
    name: str
    max_failures: int = 3
    window_seconds: float = 60.0
    cooldown_seconds: float = 30.0
    state: CircuitState = CircuitState.CLOSED
    failures: list[float] = field(default_factory=list)
    last_failure_reason: str = ""

    def record_failure(self, error: str) -> CircuitState:
        now = time.time()
        cutoff = now - self.window_seconds
        self.failures = [f for f in self.failures if f > cutoff]
        self.failures.append(now)
        self.last_failure_reason = error[:200]
        if len(self.failures) >= self.max_failures:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit OPEN for '%s': %d failures. %s",
                self.name, len(self.failures), error[:80],
            )
        return self.state

    def record_success(self) -> None:
        self.failures.clear()
        self.state = CircuitState.CLOSED

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            now = time.time()
            if self.failures and now - max(self.failures) > self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit HALF-OPEN for '%s'", self.name)
                return True
            return False
        return True  # HALF_OPEN


class CircuitRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._circuits: dict[str, CircuitBreaker] = {}

    def get(self, tool_name: str) -> CircuitBreaker:
        with self._lock:
            if tool_name not in self._circuits:
                self._circuits[tool_name] = CircuitBreaker(name=tool_name)
            return self._circuits[tool_name]

    def is_open(self, tool_name: str) -> bool:
        return not self.get(tool_name).allow_request()

    def get_open_message(self, tool_name: str) -> str:
        circuit = self.get(tool_name)
        return (
            f"Tool '{tool_name}' blocked — failed {len(circuit.failures)} times. "
            f"Try a different approach."
        )


# ---------------------------------------------------------------------------
# Error classification & retry
# ---------------------------------------------------------------------------

_FALLBACK_CHAINS: dict[str, list[str]] = {
    "web_search": ["browser_control"],
    "browser_control": ["web_search"],
    "flight_finder": ["web_search"],
    "weather_report": ["web_search"],
}


def classify_error(error: str | Exception) -> str:
    """Classify error: 'retry_now' | 'retry_later' | 'retry_never' | 'unknown'"""
    msg = str(error).lower()
    if any(w in msg for w in ("429", "quota", "rate limit", "resource_exhausted",
                               "too many requests", "throttl")):
        return "retry_later"
    if any(w in msg for w in ("401", "403", "unauthor", "forbidden",
                               "invalid api key", "auth", "permission denied")):
        return "retry_never"
    if any(w in msg for w in ("timeout", "connection", "network", "dns",
                               "refused", "reset", "unreachable", "503", "502")):
        return "retry_now"
    if any(w in msg for w in ("unknown tool", "not found", "no such file")):
        return "retry_never"
    return "unknown"


def retry_delay(classification: str, attempt: int) -> float:
    """Calculate backoff delay. Returns -1 if shouldn't retry."""
    if classification == "retry_now":
        return min(0.5 * (2 ** attempt), 5.0)
    if classification == "retry_later":
        return min(2.0 * (2 ** attempt), 30.0)
    return -1.0


def get_fallback(tool_name: str) -> str | None:
    chain = _FALLBACK_CHAINS.get(tool_name, [])
    return chain[0] if chain else None


def should_retry(tool_name: str, error: str | Exception, attempt: int) -> tuple[bool, float]:
    """Decide if tool should retry. Returns (should_retry, delay_seconds)."""
    classification = classify_error(str(error))
    if classification == "retry_never" or _registry.is_open(tool_name):
        return False, 0.0
    delay = retry_delay(classification, attempt)
    return delay > 0, delay


# ---------------------------------------------------------------------------
# Error dashboard
# ---------------------------------------------------------------------------


@dataclass
class ErrorStats:
    total: int = 0
    by_tool: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    circuits_open: list[str] = field(default_factory=list)


class ErrorDashboard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stats = ErrorStats()

    def record(self, tool_name: str, error: str) -> None:
        _registry.get(tool_name).record_failure(error)
        classification = classify_error(error)
        with self._lock:
            self.stats.total += 1
            self.stats.by_tool[tool_name] += 1
            self.stats.by_type[classification] += 1
            if _registry.is_open(tool_name) and tool_name not in self.stats.circuits_open:
                self.stats.circuits_open.append(tool_name)

    def record_success(self, tool_name: str) -> None:
        _registry.get(tool_name).record_success()

    def get_stats(self) -> ErrorStats:
        with self._lock:
            return self.stats


_dashboard = ErrorDashboard()


def record_error(tool_name: str, error: str | Exception) -> dict:
    """Record error and return recovery guidance."""
    msg = str(error)
    _dashboard.record(tool_name, msg)
    return {
        "classification": classify_error(msg),
        "retry_after": retry_delay(classify_error(msg), 1),
        "fallback": get_fallback(tool_name),
        "circuit_open": _registry.is_open(tool_name),
    }


def is_circuit_open(tool_name: str) -> bool:
    return _registry.is_open(tool_name)


_registry = CircuitRegistry()
