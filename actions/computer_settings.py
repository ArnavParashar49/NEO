"""
Computer Settings - Cross-Platform CLI
Handles lock screen, sleep, dark mode, wifi, bluetooth via OS APIs.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

_OS = platform.system()

def _run_cmd(cmd: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, r.stderr.strip() or r.stdout.strip()
    except Exception as e:
        return False, str(e)

def lock_screen() -> str:
    if _OS == "Windows":
        _run_cmd(["rundll32.exe", "user32.dll,LockWorkStation"])
    elif _OS == "Darwin":
        _run_cmd(["pmset", "displaysleepnow"])
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if _run_cmd(["which", cmd[0]])[0]:
                _run_cmd(cmd)
                break
    return "Screen locked."

def sleep_display() -> str:
    if _OS == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception:
            pass
    elif _OS == "Darwin":
        _run_cmd(["pmset", "displaysleepnow"])
    else:
        _run_cmd(["xset", "dpms", "force", "off"])
    return "Display sleeping."

def dark_mode() -> str:
    if _OS == "Darwin":
        _run_cmd(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences to set dark mode to not dark mode'])
    elif _OS == "Windows":
        script = r"""
        $p = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        $val = (Get-ItemProperty -Path $p -Name AppsUseLightTheme).AppsUseLightTheme
        $new = if ($val -eq 0) { 1 } else { 0 }
        Set-ItemProperty -Path $p -Name AppsUseLightTheme -Value $new
        Set-ItemProperty -Path $p -Name SystemUsesLightTheme -Value $new
        """
        _run_cmd(["powershell", "-Command", script])
    else:
        ok, current = _run_cmd(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
        new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
        _run_cmd(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme])
    return "Dark mode toggled."

def toggle_wifi() -> str:
    if _OS == "Darwin":
        ok, out = _run_cmd(["networksetup", "-getairportpower", "en0"])
        new_power = "On" if "Off" in out else "Off"
        _run_cmd(["networksetup", "-setairportpower", "en0", new_power])
    elif _OS == "Windows":
        _run_cmd(["powershell", "-Command", 
            "if ((Get-NetAdapter -InterfaceDescription '*Wi-Fi*').Status -eq 'Up') "
            "{ Disable-NetAdapter -InterfaceDescription '*Wi-Fi*' -Confirm:$false } "
            "else { Enable-NetAdapter -InterfaceDescription '*Wi-Fi*' -Confirm:$false }"])
    else:
        _run_cmd(["nmcli", "radio", "wifi", "on" if "disabled" in _run_cmd(["nmcli", "radio", "wifi"])[1] else "off"])
    return "Wi-Fi toggled."

def toggle_bluetooth() -> str:
    if _OS == "Darwin":
        return "Bluetooth toggle requires 'blueutil' on macOS."
    elif _OS == "Windows":
        return "Bluetooth toggle not natively supported via CLI on Windows without extra tools."
    else:
        ok, out = _run_cmd(["rfkill", "list", "bluetooth"])
        new_state = "unblock" if "Soft blocked: yes" in out else "block"
        _run_cmd(["rfkill", new_state, "bluetooth"])
        return "Bluetooth toggled."

def empty_trash() -> str:
    if _OS == "Darwin":
        _run_cmd(["osascript", "-e", 'tell app "Finder" to empty trash'])
    elif _OS == "Windows":
        _run_cmd(["powershell", "-Command", "Clear-RecycleBin -Force -Confirm:$false"])
    else:
        trash = Path.home() / ".local" / "share" / "Trash"
        for sub in ["files", "info"]:
            p = trash / sub
            if p.exists():
                _run_cmd(["rm", "-rf", f"{p}/*"])
    return "Trash emptied."

def computer_settings(parameters: dict, response=None, player=None, session_memory=None) -> str:
    action = parameters.get("action", "").lower().replace(" ", "_")
    
    if action == "lock_screen": return lock_screen()
    if action == "sleep_display": return sleep_display()
    if action == "dark_mode": return dark_mode()
    if action == "toggle_wifi": return toggle_wifi()
    if action == "toggle_bluetooth": return toggle_bluetooth()
    if action == "empty_trash": return empty_trash()
    
    return f"Settings action '{action}' is either unknown or no longer supported (CLI-only)."