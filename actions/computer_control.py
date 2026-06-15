"""Computer Control - CLI/API version only. No GUI injection."""

import json
import random
import string
import subprocess
import time
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_MEMORY_PATH = _BASE / "memory" / "long_term.json"

_FIRST_NAMES = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew"]
_LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com"]

def _get_os() -> str:
    from config import get_os
    return get_os()

def _random_data(data_type: str) -> str:
    dt = data_type.lower().strip()
    if dt == "first_name": return random.choice(_FIRST_NAMES)
    if dt == "last_name": return random.choice(_LAST_NAMES)
    if dt == "name": return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"
    if dt == "email": return f"{random.choice(_FIRST_NAMES).lower()}@{random.choice(_DOMAINS)}"
    if dt == "username": return f"user{random.randint(1000, 9999)}"
    if dt == "phone": return f"+1555{random.randint(1000000, 9999999)}"
    if dt == "city": return random.choice(["New York", "London", "Tokyo", "Paris"])
    return f"fake_{data_type}_{random.randint(100,999)}"

def _user_profile() -> dict:
    try:
        if _MEMORY_PATH.exists():
            data = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            return {k: v.get("value", "") for k, v in data.get("identity", {}).items()}
    except Exception:
        pass
    return {}

def _focus_window(title: str) -> str:
    os_name = _get_os()
    if os_name == "windows":
        script = f'(New-Object -ComObject WScript.Shell).AppActivate("{title}")'
        subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=5)
        return f"Focused window: {title}"
    if os_name == "mac":
        script = f'tell application "System Events" to set frontmost of (first process whose name contains "{title}") to true'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        return f"Focused window: {title}"
    if os_name == "linux":
        subprocess.run(["wmctrl", "-a", title], capture_output=True, timeout=5)
        return f"Focused window: {title}"
    return f"focus_window: unknown OS"

def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for CLI computer control actions.
    Actions: random_data, user_data, focus_window, wait
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return "No action specified."

    if player:
        player.write_log(f"[Computer] {action}")

    if action == "wait":
        secs = min(float(params.get("seconds", 1.0)), 30.0)
        time.sleep(secs)
        return f"Waited {secs}s"
    if action == "focus_window":
        return _focus_window(params.get("title", ""))
    if action == "random_data":
        return _random_data(params.get("type", "name"))
    if action == "user_data":
        field = params.get("field", "name")
        val = _user_profile().get(field, "")
        return val if val else _random_data(field)

    return f"Action '{action}' is not supported in CLI-only mode."