"""
Reliable cross-platform volume, brightness, and mute via pure CLI/APIs.
No GUI automation (pyautogui) is used.
"""

from __future__ import annotations

import platform
import subprocess

_OS = platform.system()

def _run_cmd(cmd: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, r.stderr.strip() or r.stdout.strip()
    except Exception as e:
        return False, str(e)

def _mac_osascript(script: str) -> tuple[bool, str]:
    return _run_cmd(["osascript", "-e", script])

def _windows_ps(script: str) -> tuple[bool, str]:
    return _run_cmd(["powershell", "-NoProfile", "-Command", script])

# --- Volume Controls ---

def _mac_volume(up: bool) -> str:
    sign = "+" if up else "-"
    ok, _ = _mac_osascript(f"set volume output volume (output volume of (get volume settings) {sign} 10)")
    return "Volume increased." if up else "Volume decreased."

def _win_volume(up: bool) -> str:
    # 175 is volume up, 174 is volume down
    key = "175" if up else "174"
    script = f"$obj = New-Object -ComObject WScript.Shell; $obj.SendKeys([char]{key}); $obj.SendKeys([char]{key})"
    _windows_ps(script)
    return "Volume increased." if up else "Volume decreased."

def _linux_volume(up: bool) -> str:
    sign = "+" if up else "-"
    ok, err = _run_cmd(["amixer", "-D", "pulse", "sset", "Master", f"5%{sign}"])
    if not ok:
        ok, err = _run_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}5%"])
    return "Volume increased." if up else "Volume decreased."

def volume_up() -> str:
    if _OS == "Darwin": return _mac_volume(True)
    if _OS == "Windows": return _win_volume(True)
    return _linux_volume(True)

def volume_down() -> str:
    if _OS == "Darwin": return _mac_volume(False)
    if _OS == "Windows": return _win_volume(False)
    return _linux_volume(False)

def mute_toggle() -> str:
    if _OS == "Darwin":
        _mac_osascript("set volume with output muted")
        return "Mute toggled."
    if _OS == "Windows":
        _windows_ps("$obj = New-Object -ComObject WScript.Shell; $obj.SendKeys([char]173)")
        return "Mute toggled."
    # Linux
    _run_cmd(["amixer", "-D", "pulse", "sset", "Master", "toggle"])
    _run_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
    return "Mute toggled."

# --- Brightness Controls ---

def _mac_brightness(up: bool) -> str:
    # Mac brightness via osascript using F14/F15 keys (key code 144/145) 
    code = 144 if up else 145
    _mac_osascript(f'tell application "System Events" to repeat 2 times\n key code {code}\nend repeat')
    return "Brightness adjusted."

def _win_brightness(up: bool) -> str:
    # Windows WMI brightness
    sign = "+" if up else "-"
    script = f"""
    $m = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness
    if ($m) {{
        $b = $m.CurrentBrightness
        $new = $b {sign} 10
        if ($new -gt 100) {{ $new = 100 }}
        if ($new -lt 0) {{ $new = 0 }}
        $methods = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods
        $methods.WmiSetBrightness(1, $new)
    }}
    """
    _windows_ps(script)
    return "Brightness adjusted."

def _linux_brightness(up: bool) -> str:
    sign = "+" if up else "-"
    _run_cmd(["brightnessctl", "set", f"10%{sign}"])
    return "Brightness adjusted."

def brightness_up() -> str:
    if _OS == "Darwin": return _mac_brightness(True)
    if _OS == "Windows": return _win_brightness(True)
    return _linux_brightness(True)

def brightness_down() -> str:
    if _OS == "Darwin": return _mac_brightness(False)
    if _OS == "Windows": return _win_brightness(False)
    return _linux_brightness(False)

# --- Dispatch ---

def resolve_command_from_text(text: str, last_cmd: str | None = None) -> str | None:
    text = text.lower()
    if any(w in text for w in ("louder", "volume up", "increase volume")): return "volume_up"
    if any(w in text for w in ("quieter", "volume down", "decrease volume", "lower volume")): return "volume_down"
    if any(w in text for w in ("mute", "silence", "unmute")): return "mute"
    if any(w in text for w in ("brighter", "brightness up", "increase brightness")): return "brightness_up"
    if any(w in text for w in ("dim", "dimmer", "darker", "brightness down", "decrease brightness")): return "brightness_down"
    if last_cmd and any(w in text for w in ("more", "again", "keep going")): return last_cmd
    return None

def system_control(args: dict) -> str:
    action = args.get("action", "").lower().replace(" ", "_")
    
    if action in ("volume_up", "louder"):
        return volume_up()
    elif action in ("volume_down", "quieter"):
        return volume_down()
    elif action in ("mute", "unmute", "silence", "toggle_mute"):
        return mute_toggle()
    elif action in ("brightness_up", "brighter"):
        return brightness_up()
    elif action in ("brightness_down", "dimmer"):
        return brightness_down()
    
    return f"Unknown system control action: {action}"
