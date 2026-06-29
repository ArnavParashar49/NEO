"""Unit tests for core/error_recovery.py — circuit breaker & classification."""

from __future__ import annotations


def test_error_classification_retry_later():
    from core.error_recovery import classify_error
    assert classify_error("429 Too Many Requests") == "retry_later"
    assert classify_error("quota exceeded") == "retry_later"
    assert classify_error("rate limit hit") == "retry_later"


def test_error_classification_retry_never():
    from core.error_recovery import classify_error
    assert classify_error("401 Unauthorized") == "retry_never"
    assert classify_error("invalid api key") == "retry_never"
    assert classify_error("403 Forbidden") == "retry_never"


def test_error_classification_retry_now():
    from core.error_recovery import classify_error
    assert classify_error("connection refused") == "retry_now"
    assert classify_error("connection reset") == "retry_now"
    assert classify_error("timeout error") == "retry_now"


def test_circuit_breaker_closed():
    from core.error_recovery import CircuitBreaker
    cb = CircuitBreaker(name="test_tool", max_failures=3)
    assert cb.allow_request() is True


def test_circuit_breaker_opens_after_failures():
    from core.error_recovery import CircuitBreaker
    cb = CircuitBreaker(name="test_tool", max_failures=2, window_seconds=60)
    cb.record_failure("error 1")
    assert cb.state.name == "CLOSED"
    cb.record_failure("error 2")
    assert cb.state.name == "OPEN"
    assert cb.allow_request() is False


def test_circuit_breaker_records_success():
    from core.error_recovery import CircuitBreaker
    cb = CircuitBreaker(name="test_tool", max_failures=2)
    cb.record_failure("error")
    assert cb.state.name == "CLOSED"
    cb.record_success()
    assert cb.state.name == "CLOSED"
    assert cb.allow_request() is True


def test_get_fallback():
    from core.error_recovery import get_fallback
    assert get_fallback("web_search") == "browser_control"
    assert get_fallback("browser_control") == "web_search"
    assert get_fallback("unknown_tool") is None


def test_should_retry():
    from core.error_recovery import should_retry
    yes, delay = should_retry("web_search", "timeout error", attempt=1)
    assert yes is True
    assert delay > 0
    yes, delay = should_retry("web_search", "invalid api key", attempt=1)
    assert yes is False
    assert delay == 0
