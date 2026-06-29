"""Safe, target-aware tab, window, and application closing."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass


_OS = platform.system()
_BROWSER_PROCESSES = {
    "chrome", "msedge", "firefox", "brave", "opera", "opera_gx",
    "vivaldi", "safari",
}
_BROWSER_ALIASES = {
    "chrome": "chrome", "google chrome": "chrome",
    "edge": "msedge", "microsoft edge": "msedge",
    "firefox": "firefox", "brave": "brave", "opera": "opera",
    "vivaldi": "vivaldi", "safari": "safari",
}
_PROTECTED_TITLES = ("neo", "aria")


@dataclass(frozen=True)
class WindowTarget:
    handle: int
    pid: int
    process: str
    title: str


def _is_protected(target: WindowTarget) -> bool:
    if target.pid == os.getpid():
        return True
    process = target.process.casefold()
    title = target.title.casefold().strip()
    return process in {"neo", "aria"} or title in _PROTECTED_TITLES


def _windows_targets() -> list[WindowTarget]:
    import ctypes
    from ctypes import wintypes

    import psutil

    user32 = ctypes.windll.user32
    targets: list[WindowTarget] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            process = psutil.Process(pid.value).name().rsplit(".", 1)[0]
        except (psutil.Error, OSError):
            process = ""
        target = WindowTarget(int(hwnd), int(pid.value), process, buffer.value.strip())
        if not _is_protected(target):
            targets.append(target)
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return targets


def _match_target(
    targets: list[WindowTarget], *, app: str = "", browsers_only: bool = False
) -> WindowTarget | None:
    query = " ".join((app or "").casefold().split())
    candidates = [
        target for target in targets
        if not browsers_only or target.process.casefold() in _BROWSER_PROCESSES
    ]
    if query:
        exact = [target for target in candidates if target.process.casefold() == query]
        if exact:
            return exact[0]
        matching = [
            target for target in candidates
            if query in target.process.casefold() or query in target.title.casefold()
        ]
        return matching[0] if matching else None
    return candidates[0] if candidates else None


def _window_title(user32, handle: int) -> str:
    import ctypes

    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value.strip()


def _send_windows_hotkey(user32, modifier: int, key: int) -> None:
    key_up = 0x0002
    user32.keybd_event(modifier, 0, 0, 0)
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, key_up, 0)
    user32.keybd_event(modifier, 0, key_up, 0)


def _title_matches(title: str, target: str) -> bool:
    wanted = re.sub(r"[^a-z0-9]+", "", target.casefold())
    actual = re.sub(r"[^a-z0-9]+", "", title.casefold())
    return bool(wanted) and wanted in actual


def _windows_close_tab(app: str, target_name: str = "") -> str:
    import ctypes

    user32 = ctypes.windll.user32
    browser = _BROWSER_ALIASES.get(app.casefold(), app)
    candidates = [
        target for target in _windows_targets()
        if target.process.casefold() in _BROWSER_PROCESSES
        and (not browser or browser.casefold() in (target.process.casefold(), target.title.casefold()))
    ]
    if not candidates:
        return "FAILED: No matching browser window is open."
    for target in candidates:
        user32.ShowWindow(target.handle, 9)  # SW_RESTORE
        if not user32.SetForegroundWindow(target.handle):
            continue
        time.sleep(0.15)
        if int(user32.GetForegroundWindow()) != target.handle:
            continue
        if not target_name:
            _send_windows_hotkey(user32, 0x11, 0x57)  # Ctrl+W
            return f"Closed the active tab in {target.process}."

        original_title = _window_title(user32, target.handle)
        seen = {original_title.casefold()}
        for _ in range(30):
            current = _window_title(user32, target.handle)
            if _title_matches(current, target_name):
                _send_windows_hotkey(user32, 0x11, 0x57)
                return f"Closed the '{target_name}' tab in {target.process}."
            _send_windows_hotkey(user32, 0x11, 0x09)  # Ctrl+Tab
            time.sleep(0.12)
            next_title = _window_title(user32, target.handle)
            if next_title.casefold() in seen:
                break
            seen.add(next_title.casefold())
    return f"FAILED: No browser tab matching '{target_name}' was found. No tab was closed."


def _windows_close_window(app: str, *, all_windows: bool = False) -> str:
    import ctypes

    user32 = ctypes.windll.user32
    targets = _windows_targets()
    first = _match_target(targets, app=app)
    if not first:
        return "FAILED: No matching application window is open."
    selected = (
        [target for target in targets if target.process.casefold() == first.process.casefold()]
        if all_windows else [first]
    )
    for target in selected:
        user32.PostMessageW(target.handle, 0x0010, 0, 0)  # WM_CLOSE
    time.sleep(0.35)
    remaining = sum(bool(user32.IsWindow(target.handle)) for target in selected)
    noun = "application" if all_windows else "window"
    if remaining == len(selected):
        return (
            f"Close requested for {first.process}; it may be waiting for you "
            "to save changes."
        )
    if remaining:
        return f"PARTIAL: Closed {len(selected) - remaining} of {len(selected)} {first.process} windows."
    return f"Closed {first.process} {noun}."


def _run_native(argv: list[str], timeout: int = 10) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def _mac_control(action: str, app: str) -> str:
    app_filter = (app or "").replace('"', "")
    ok, frontmost = _run_native([
        "osascript", "-e",
        'tell application "System Events" to get name of first application process whose frontmost is true',
    ])
    if not ok:
        return f"FAILED: Could not identify the active application: {frontmost}"
    if frontmost.casefold() in {"neo", "aria", "python", "python3"}:
        return "FAILED: Refusing to close NEO's own window."
    if action == "close_tab":
        script = (
            'tell application "System Events" to tell first application process '
            'whose frontmost is true to keystroke "w" using command down'
        )
    elif action == "close_window":
        script = (
            'tell application "System Events" to tell first application process '
            'whose frontmost is true to perform action "AXClose" of window 1'
        )
    else:
        if not app_filter:
            return "FAILED: Specify which application to close."
        script = f'tell application "{app_filter}" to quit'
    ok, output = _run_native(["osascript", "-e", script])
    return (f"Closed {action.replace('_', ' ')}." if ok else f"FAILED: {output}")


def _linux_control(action: str, app: str) -> str:
    ok, active_id = _run_native(["xdotool", "getactivewindow"])
    if not ok:
        return f"FAILED: Could not identify the active window: {active_id}"
    ok, title = _run_native(["xdotool", "getwindowname", active_id])
    if ok and any(protected == title.casefold().strip() for protected in _PROTECTED_TITLES):
        return "FAILED: Refusing to close NEO's own window."
    if action == "close_tab":
        ok, output = _run_native(["xdotool", "key", "ctrl+w"])
    elif app:
        ok, output = _run_native(["wmctrl", "-c", app])
    else:
        ok, output = _run_native(["xdotool", "getactivewindow", "windowclose"])
    return (f"Closed {action.replace('_', ' ')}." if ok else f"FAILED: {output}")


def window_control(parameters: dict | None = None, response=None, player=None,
                   session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "").strip().casefold().replace(" ", "_")
    app = (params.get("app") or params.get("application") or "").strip()
    target = (params.get("target") or params.get("tab") or params.get("title") or "").strip()
    if action not in {"close_tab", "close_window", "close_app"}:
        return "FAILED: Use close_tab, close_window, or close_app."
    if action == "close_app" and not app:
        return "FAILED: Specify which application to close."
    if player:
        player.write_log(f"[window] {action} {target or app}".strip())

    if _OS == "Windows":
        if action == "close_tab":
            if app.casefold() not in _BROWSER_ALIASES and app and not target:
                target, app = app, ""
            return _windows_close_tab(app, target)
        return _windows_close_window(app, all_windows=action == "close_app")
    if action == "close_tab" and target:
        return (
            f"FAILED: Targeted tab closing is not yet supported on {_OS}. "
            "No tab was closed."
        )
    if _OS == "Darwin":
        return _mac_control(action, app)
    return _linux_control(action, app)
