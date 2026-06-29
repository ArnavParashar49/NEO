"""Unit tests for core/command_runner.py — centralized subprocess runner."""

from __future__ import annotations

import time
import sys


def test_run_safe_success():
    from core.command_runner import run_safe
    ok, out = run_safe([sys.executable, "-c", "print('hello')"], timeout=5)
    assert ok, f"Expected success, got: {out}"
    assert "hello" in out


def test_run_safe_not_found():
    from core.command_runner import run_safe
    ok, out = run_safe(["_nonexistent_binary_xxxx"], timeout=2)
    assert not ok
    assert "not found" in out


def test_run_safe_timeout():
    from core.command_runner import run_safe
    ok, out = run_safe(
        ["python", "-c", "import time; time.sleep(10)"],
        timeout=1,
    )
    assert not ok
    assert "timed out" in out


def test_check_dependencies():
    from core.command_runner import check_dependencies
    result = check_dependencies(["python", "_definitely_not_real"])
    assert result.get("python", False) is True
    assert result.get("_definitely_not_real", True) is False


def test_powershell_returns_tuple():
    from core.command_runner import powershell
    ok, out = powershell("Write-Output 'test'", timeout=5)
    assert isinstance(ok, bool)
    assert isinstance(out, str)


def test_osascript_returns_tuple():
    from core.command_runner import osascript
    ok, out = osascript('return "hello"', timeout=5)
    assert isinstance(ok, bool)
    assert isinstance(out, str)
