"""Unit tests for core/tool_runner.py — fast path, tool dispatch, error recovery."""

from __future__ import annotations


def test_fast_path_cache_miss():
    from core.tool_runner import FastPathCache
    cache = FastPathCache()
    assert cache.get("web_search", {"q": "test"}) is None


def test_fast_path_cache_hit():
    from core.tool_runner import FastPathCache
    cache = FastPathCache()
    cache.register("web_search", {"q": "test"}, "found it")
    result = cache.get("web_search", {"q": "test"})
    assert result == "found it"


def test_fast_path_cache_different_args():
    from core.tool_runner import FastPathCache
    cache = FastPathCache()
    cache.register("web_search", {"q": "test"}, "found it")
    assert cache.get("web_search", {"q": "other"}) is None


def test_tool_progress_eta_default():
    from core.tool_runner import tool_progress_eta
    assert tool_progress_eta("unknown_tool") == 12


def test_tool_progress_eta_known():
    from core.tool_runner import tool_progress_eta
    assert tool_progress_eta("web_search") == 18
    assert tool_progress_eta("weather_report") == 4


def test_tool_filler_phrase():
    from core.tool_runner import tool_status_line
    line = tool_status_line("web_search", {"query": "python tutorial"})
    assert "python tutorial" in line


def test_run_local_system_control_no_match():
    from core.tool_runner import run_local_system_control
    ok, result = run_local_system_control("what is the weather")
    assert ok is False
    assert result == ""
