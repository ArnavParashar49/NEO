"""
Reliable Mac/Windows volume, brightness, and mute.
Volume on macOS uses media keys + AppleScript. Brightness uses macOS display brightness keys (same as keyboard).
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import time

_OS = platform.system()
_OSASCRIPT = "/usr/bin/osascript" if platform.system() == "Darwin" else "osascript"

_PHRASE_TO_COMMAND: list[tuple[tuple[str, ...], str]] = [
    (("volume up", "turn up volume", "increase volume", "louder", "raise volume"), "volume_up"),
    (("volume down", "turn down volume", "decrease volume", "quieter", "lower volume"), "volume_down"),
    (("mute", "unmute", "silence", "toggle mute"), "mute"),
    (
        (
            "brightness up",
            "brighter",
            "increase brightness",
            "screen brightness",
            "raise brightness",
            "make it brighter",
        ),
        "brightness_up",
    ),
    (
        (
            "brightness down",
            "dimmer",
            "decrease brightness",
            "lower brightness",
            "make it dimmer",
        ),
        "brightness_down",
    ),
]

_MORE_PHRASES = (
    "more",
    "increase it",
    "increase more",
    "a bit more",
    "even more",
    "again",
    "keep going",
    "louder still",
    "brighter still",
)


def _run_osascript(*lines: str) -> tuple[bool, str]:
    script = "\n".join(lines)
    try:
        r = subprocess.run(
            [_OSASCRIPT, "-e", script],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if r.returncode == 0:
            return True, (r.stdout or "").strip()
        return False, (r.stderr or r.stdout or f"exit {r.returncode}").strip()
    except Exception as e:
        return False, str(e)


def _mac_get_volume() -> int | None:
    ok, out = _run_osascript("output volume of (get volume settings)")
    if ok and str(out).strip().isdigit():
        return int(str(out).strip())
    return None


def _mac_volume_media_keys(up: bool) -> tuple[bool, str]:
    try:
        import pyautogui

        key = "volumeup" if up else "volumedown"
        for _ in range(8):
            pyautogui.press(key)
        return True, key
    except ImportError:
        return False, "pyautogui missing"
    except Exception as e:
        return False, str(e)


def _mac_volume_up() -> str:
    before = _mac_get_volume()
    ok, detail = _mac_volume_media_keys(True)
    after = _mac_get_volume()
    if before is not None and after is not None and after > before:
        return f"Volume increased ({after}%)."
    ok2, err = _run_osascript(
        "set v to output volume of (get volume settings)",
        "set volume output volume (v + 15)",
    )
    after2 = _mac_get_volume()
    if ok2 and before is not None and after2 is not None and after2 > before:
        return f"Volume increased ({after2}%)."
    if ok2 and before is None:
        return "Volume increased."
    return (
        f"FAILED: Volume did not change (before={before}, after={after2}). "
        f"Keys: {detail}; script: {err if not ok2 else 'ok'}"
    )


def _mac_volume_down() -> str:
    before = _mac_get_volume()
    ok, detail = _mac_volume_media_keys(False)
    after = _mac_get_volume()
    if before is not None and after is not None and after < before:
        return f"Volume decreased ({after}%)."
    ok2, err = _run_osascript(
        "set v to output volume of (get volume settings)",
        "set volume output volume (v - 15)",
    )
    after2 = _mac_get_volume()
    if ok2 and before is not None and after2 is not None and after2 < before:
        return f"Volume decreased ({after2}%)."
    if ok2 and before is None:
        return "Volume decreased."
    return f"FAILED: Volume did not change. Keys: {detail}; script: {err if not ok2 else 'ok'}"


def _mac_mute_toggle() -> str:
    ok, err = _run_osascript("set volume with output muted")
    return "Mute toggled." if ok else f"FAILED: Mute — {err}"


# IOHID ev_keymap — same codes PyAutoGUI uses for special keys
_MAC_KEYTYPE_BRIGHTNESS_UP   = 2
_MAC_KEYTYPE_BRIGHTNESS_DOWN = 3


def _mac_backlight_level() -> float | None:
    """Built-in display brightness 0.0–1.0 from ioreg (None if unavailable)."""
    try:
        r = subprocess.run(
            ["ioreg", "-rw0", "-c", "AppleBacklightDisplay"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        text = r.stdout or ""
        # Modern Macs: "brightness"={"min"=0,"max"=1024,"value"=923}
        m = re.search(
            r'"brightness"=\{"min"=\d+,"max"=(\d+),"value"=(\d+)\}',
            text,
        )
        if m:
            max_v, val = int(m.group(1)), int(m.group(2))
            if max_v > 0:
                return val / max_v
        # Older format: "brightness"=0.766601
        m2 = re.search(r'"brightness"=([0-9.]+)', text)
        if m2:
            v = float(m2.group(1))
            return v if v <= 1.0 else v / 1024.0
    except Exception:
        pass
    return None


def _mac_brightness_media_keys(steps: int) -> tuple[bool, str]:
    """
    Press real brightness-up/down keys (NSSystemDefined), same mechanism as volume keys.
    """
    try:
        import AppKit
        import Quartz
    except ImportError as e:
        return False, f"PyObjC missing: {e}"

    key_type = _MAC_KEYTYPE_BRIGHTNESS_UP if steps > 0 else _MAC_KEYTYPE_BRIGHTNESS_DOWN
    n = max(1, abs(steps))
    try:
        for _ in range(n):
            for is_down in (True, False):
                flags = 0xA00 if is_down else 0xB00
                data1 = (key_type << 16) | ((0xA if is_down else 0xB) << 8)
                ev = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                    Quartz.NSSystemDefined,
                    (0, 0),
                    flags,
                    0,
                    0,
                    0,
                    8,
                    data1,
                    -1,
                )
                Quartz.CGEventPost(0, ev.CGEvent())
                time.sleep(0.04)
        return True, "display brightness keys"
    except Exception as e:
        return False, str(e)


def _mac_brightness_osascript(steps: int) -> tuple[bool, str]:
    """Fallback: F-key codes via System Events (needs Accessibility)."""
    codes = (144, 113) if steps > 0 else (145, 107)
    repeat = max(1, abs(steps) // 2)
    for code in codes:
        script = (
            f'tell application "System Events" to repeat {repeat} times\n'
            f"  key code {code}\n"
            "end repeat"
        )
        ok, err = _run_osascript(script)
        if ok:
            return True, f"key code {code}"
    return False, "osascript key codes failed"


def _mac_brightness_cli(target_delta: float) -> tuple[bool, str]:
    exe = shutil.which("brightness")
    if not exe:
        return False, "install with: brew install brightness"
    try:
        before = _mac_backlight_level()
        if before is not None:
            target = max(0.0, min(1.0, before + target_delta))
            subprocess.run([exe, str(target)], capture_output=True, timeout=5, check=True)
        else:
            sign = "+" if target_delta > 0 else ""
            subprocess.run([exe, f"{sign}{target_delta}"], capture_output=True, timeout=5, check=True)
        return True, "brightness CLI"
    except Exception as e:
        return False, str(e)


def _mac_brightness(steps: int) -> str:
    up = steps > 0
    before = _mac_backlight_level()
    errors: list[str] = []

    if before is not None:
        if up and before >= 0.97:
            return "Already at maximum brightness."
        if not up and before <= 0.03:
            return "Already at minimum brightness."

    ok, detail = _mac_brightness_media_keys(steps)
    if ok:
        time.sleep(0.25)
        after = _mac_backlight_level()
        if before is not None and after is not None:
            if (up and after > before) or (not up and after < before):
                return f"Brightness {'increased' if up else 'decreased'}."
        if before is None:
            return "Brightness adjusted."

    errors.append(detail)

    ok, detail = _mac_brightness_osascript(steps)
    if ok:
        time.sleep(0.25)
        after = _mac_backlight_level()
        if before is None or after is None or (up and after > before) or (not up and after < before):
            return f"Brightness {'increased' if up else 'decreased'}."
    errors.append(detail)

    delta = 0.12 if up else -0.12
    ok, detail = _mac_brightness_cli(delta * max(1, abs(steps) // 3))
    if ok:
        return f"Brightness {'increased' if up else 'decreased'}."
    errors.append(detail)

    return (
        "FAILED: Brightness did not change. "
        "Enable Accessibility for the app that runs ARIA (Terminal or Cursor) under "
        "System Settings → Privacy & Security → Accessibility. "
        "External monitors: use the monitor's brightness buttons. "
        f"Tip: brew install brightness — {errors[0] if errors else 'keys blocked'}"
    )


def _win_volume(direction: int) -> str:
    try:
        import pyautogui

        key = "volumeup" if direction > 0 else "volumedown"
        for _ in range(5):
            pyautogui.press(key)
        return "Volume adjusted."
    except ImportError:
        return "FAILED: PyAutoGUI not installed."
    except Exception as e:
        return f"FAILED: Volume — {e}"


def _dispatch(command: str, steps: int = 1) -> str:
    cmd = command.lower().strip().replace(" ", "_").replace("-", "_")
    bright_steps = 6 * max(1, steps)

    if cmd in ("volume_up", "up"):
        if _OS == "Darwin":
            return _mac_volume_up()
        if _OS == "Windows":
            return _win_volume(1)
        return _linux_volume("+10%")

    if cmd in ("volume_down", "down"):
        if _OS == "Darwin":
            return _mac_volume_down()
        if _OS == "Windows":
            return _win_volume(-1)
        return _linux_volume("-10%")

    if cmd in ("mute", "toggle_mute", "unmute"):
        if _OS == "Darwin":
            return _mac_mute_toggle()
        try:
            import pyautogui

            pyautogui.press("volumemute")
            return "Mute toggled."
        except Exception as e:
            return f"FAILED: Mute — {e}"

    if cmd in ("brightness_up", "brighter"):
        if _OS == "Darwin":
            return _mac_brightness(bright_steps)
        return _brightness_fallback(+1)

    if cmd in ("brightness_down", "dimmer"):
        if _OS == "Darwin":
            return _mac_brightness(-bright_steps)
        return _brightness_fallback(-1)

    return f"Unknown command: {command}"


def _linux_volume(delta: str) -> str:
    try:
        subprocess.run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", delta],
            capture_output=True,
            timeout=5,
            check=True,
        )
        return "Volume adjusted."
    except Exception as e:
        return f"FAILED: Volume — {e}"


def _brightness_fallback(direction: int) -> str:
    try:
        import pyautogui

        if _OS == "Windows":
            pyautogui.press("brightnessup" if direction > 0 else "brightnessdown")
            return "Brightness adjusted."
    except Exception as e:
        return f"FAILED: Brightness — {e}"
    return "FAILED: Brightness not supported on this OS."


def resolve_command_from_text(text: str, last_command: str | None = None) -> str | None:
    t = text.lower().strip()

    if any(p in t for p in _MORE_PHRASES) and last_command:
        return last_command

    for phrases, cmd in _PHRASE_TO_COMMAND:
        if any(p in t for p in phrases):
            return cmd

    if "volume" in t or "loud" in t:
        if any(w in t for w in ("down", "quieter", "lower", "decrease", "soft")):
            return "volume_down"
        if any(w in t for w in ("up", "louder", "raise", "increase", "higher")):
            return "volume_up"
    if "bright" in t or "dim" in t:
        if any(w in t for w in ("down", "dim", "lower", "decrease")):
            return "brightness_down"
        if any(w in t for w in ("up", "brighter", "raise", "increase", "more")):
            return "brightness_up"
    return None


def is_system_control_request(text: str, last_command: str | None = None) -> bool:
    return resolve_command_from_text(text, last_command) is not None


def system_control(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    command = (params.get("command") or params.get("action") or "").strip()
    description = (params.get("description") or "").strip()
    last = (params.get("last_command") or "").strip() or None
    steps = int(params.get("steps") or 1)

    if not command and description:
        command = resolve_command_from_text(description, last) or ""

    if not command:
        return (
            "No command. Use: volume_up | volume_down | mute | "
            "brightness_up | brightness_down"
        )

    print(f"[SystemControl] {command} steps={steps} ({_OS})")
    return _dispatch(command, steps=steps)
