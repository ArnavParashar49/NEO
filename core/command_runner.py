"""Centralized subprocess runner — consistent timeout, logging, platform safety.

Replaces the ~15 scattered ``_run_cmd()`` / ``subprocess.run()`` / ``subprocess.Popen()``
patterns across actions, agent, and core modules with a single, auditable entry point.

Usage::

    from core.command_runner import run, powershell, osascript

    ok, out = run_safe(["ls", "-la"], timeout=5)
    ok, out = powershell("Get-Process")
    ok, out = osascript('tell app "Finder" to empty trash')
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_OS = platform.system()
IS_MAC = _OS == "Darwin"
IS_WINDOWS = _OS == "Windows"
IS_LINUX = not IS_MAC and not IS_WINDOWS


# ---------------------------------------------------------------------------
# Base runner
# ---------------------------------------------------------------------------


def run(
    cmd: list[str],
    *,
    timeout: int | float = 30,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    cwd: str | Path | None = None,
    input: str | None = None,
    shell: bool = False,
) -> subprocess.CompletedProcess:
    """Run a subprocess command with consistent defaults.

    Args:
        cmd: Command as list of strings.
        timeout: Seconds before TimeoutExpired is raised.
        check: If True, raises CalledProcessError on non-zero exit.
        capture_output: If True, captures stdout/stderr.
        text: If True, decode output as text (vs bytes).
        cwd: Working directory for the subprocess.
        input: String to pass to stdin.
        shell: If True, run through the shell (avoid when possible).

    Returns:
        ``subprocess.CompletedProcess`` instance.

    Raises:
        subprocess.TimeoutExpired: If the command exceeds *timeout*.
        subprocess.CalledProcessError: If *check* is True and rc != 0.
        FileNotFoundError: If the executable is not found.
    """
    log_cmd = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    logger.debug("Running: %s (timeout=%s, cwd=%s)", log_cmd[:200], timeout, cwd)

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            check=check,
            cwd=str(cwd) if cwd else None,
            input=input,
            shell=shell,
        )
    except FileNotFoundError:
        logger.warning("Command not found: %s", log_cmd[:120])
        raise
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out (%ss): %s", timeout, log_cmd[:120])
        raise

    elapsed = time.monotonic() - start
    if result.returncode != 0:
        logger.debug(
            "Command exited %d after %.1fs: %s | stderr: %.200s",
            result.returncode, elapsed, log_cmd[:80], result.stderr or "",
        )
    else:
        logger.debug("Command OK (%.1fs): %s", elapsed, log_cmd[:80])

    return result


# ---------------------------------------------------------------------------
# Safe variant — never raises, returns (success, output_or_error)
# ---------------------------------------------------------------------------


def run_safe(
    cmd: list[str],
    *,
    timeout: int | float = 30,
    cwd: str | Path | None = None,
    input: str | None = None,
    shell: bool = False,
) -> tuple[bool, str]:
    """Run a command safely — returns ``(success, output_or_error)`` tuple.

    Never raises. Collapses timeout, missing-binary, and non-zero exit
    into a ``(False, message)`` result.
    """
    try:
        result = run(
            cmd, timeout=timeout, check=False, capture_output=True,
            text=True, cwd=cwd, input=input, shell=shell,
        )
        if result.returncode == 0:
            return True, (result.stdout or "").strip()
        err = (result.stderr or result.stdout or "").strip()[:400]
        return False, err
    except FileNotFoundError:
        return False, f"Command not found: {cmd[0] if cmd else '?'}"
    except subprocess.TimeoutExpired:
        return False, f"Command timed out ({timeout}s)"
    except Exception as exc:
        logger.exception("run_safe unexpected error")
        return False, str(exc)[:300]


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def powershell(script: str, *, timeout: int = 15) -> tuple[bool, str]:
    """Run a PowerShell script on Windows. Returns ``(ok, output_or_error)``."""
    return run_safe(
        ["powershell", "-NoProfile", "-Command", script],
        timeout=timeout,
    )


def osascript(script: str, *, timeout: int = 10) -> tuple[bool, str]:
    """Run an AppleScript snippet on macOS. Returns ``(ok, output_or_error)``."""
    return run_safe(["osascript", "-e", script], timeout=timeout)


# ---------------------------------------------------------------------------
# Platform-aware file / URL / app openers
# ---------------------------------------------------------------------------


def open_path(path: str | Path, app: str | None = None) -> bool:
    """Open a file or folder with the OS default handler (or a specific app)."""
    p = str(path)
    try:
        if IS_MAC:
            cmd = ["open", "-a", app, p] if app else ["open", p]
            run(cmd, check=False, timeout=10)
        elif IS_WINDOWS:
            if app:
                run(["cmd", "/c", "start", "", app, p], check=False, timeout=10)
            else:
                os.startfile(p)  # type: ignore[attr-defined]
        else:
            cmd = [app, p] if app else ["xdg-open", p]
            run(cmd, check=False, timeout=10)
        return True
    except Exception as e:
        logger.warning("open_path failed for %s: %s", p, e)
        return False


def reveal_in_file_manager(path: str | Path) -> bool:
    """Open the OS file manager at a folder (or the given file's parent)."""
    p = Path(str(path))
    folder = str(p if p.is_dir() else p.parent)
    try:
        if IS_MAC:
            run(["open", folder], check=False, timeout=10)
        elif IS_WINDOWS:
            run(["explorer", folder], check=False, timeout=10)
        else:
            run(["xdg-open", folder], check=False, timeout=10)
        return True
    except Exception as e:
        logger.warning("reveal_in_file_manager failed for %s: %s", folder, e)
        return False


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------


def check_executable(name: str) -> bool:
    """Return True if *name* is available on PATH."""
    return shutil.which(name) is not None


def check_dependencies(names: list[str]) -> dict[str, bool]:
    """Return ``{name: found}`` for every executable in *names*."""
    return {n: check_executable(n) for n in names}
