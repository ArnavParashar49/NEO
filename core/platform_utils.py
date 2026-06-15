"""Cross-platform helpers for opening files/URLs/apps and OS-specific keys.

Centralizes the macOS-vs-Windows-vs-Linux differences that were scattered (and
in places hardcoded to macOS via the `open` command or the Cmd key) across the
action modules.
"""

from __future__ import annotations

import os
import platform
import subprocess
import webbrowser
from pathlib import Path

_OS = platform.system()  # "Darwin" | "Windows" | "Linux"

IS_MAC = _OS == "Darwin"
IS_WINDOWS = _OS == "Windows"
IS_LINUX = not IS_MAC and not IS_WINDOWS


def mod_key() -> str:
    """Primary modifier for hotkeys: Cmd on macOS, Ctrl elsewhere."""
    return "command" if IS_MAC else "ctrl"


def open_path(path, app: str | None = None) -> bool:
    """Open a file/folder with the default app (or a named app). True on success."""
    p = str(path)
    try:
        if IS_MAC:
            subprocess.run(["open", "-a", app, p] if app else ["open", p],
                           check=False, timeout=10)
        elif IS_WINDOWS:
            if app:
                subprocess.run(["cmd", "/c", "start", "", app, p], check=False, timeout=10)
            else:
                os.startfile(p)  # type: ignore[attr-defined]
        else:  # Linux
            subprocess.run([app, p] if app else ["xdg-open", p], check=False, timeout=10)
        return True
    except Exception as e:
        print(f"[platform] open_path failed for {p}: {e}")
        return False


def open_url(url: str) -> bool:
    """Open a URL in the default browser (cross-platform)."""
    try:
        return webbrowser.open(url)
    except Exception as e:
        print(f"[platform] open_url failed for {url}: {e}")
        return False


def reveal_in_file_manager(path) -> bool:
    """Open the OS file manager at a folder (or the given file's parent)."""
    p = Path(str(path))
    folder = str(p if p.is_dir() else p.parent)
    try:
        if IS_MAC:
            subprocess.run(["open", folder], check=False, timeout=10)
        elif IS_WINDOWS:
            subprocess.run(["explorer", folder], check=False, timeout=10)
        else:
            subprocess.run(["xdg-open", folder], check=False, timeout=10)
        return True
    except Exception as e:
        print(f"[platform] reveal_in_file_manager failed for {p}: {e}")
        return False
