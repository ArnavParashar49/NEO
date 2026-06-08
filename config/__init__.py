# config/__init__.py
"""Single source of truth for ARIA's local configuration (config/api_keys.json).

Action modules historically each copy-pasted their own loader. Prefer importing
from here:

    from config import get_config, get_api_key, get_os
"""
import json
import platform
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "api_keys.json"

_VALID_OS = {"windows", "mac", "linux"}
_PLATFORM_OS = {"Darwin": "mac", "Windows": "windows", "Linux": "linux"}


def get_config() -> dict:
    """Return the parsed config as a fresh dict (safe for callers to mutate)."""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_api_key(name: str = "gemini_api_key", *, required: bool = True) -> str:
    """Return an API key from the config.

    With ``required=True`` (default) a missing key raises ``KeyError`` — matching
    the long-standing ``json.load(f)["gemini_api_key"]`` behavior across modules.
    With ``required=False`` a missing key returns an empty string.
    """
    cfg = get_config()
    if required:
        return cfg[name]
    return cfg.get(name, "") or ""


def detect_os() -> str:
    """The actual running OS as 'windows' | 'mac' | 'linux'."""
    return _PLATFORM_OS.get(platform.system(), "linux")


def get_os() -> str:
    """OS id: 'windows' | 'mac' | 'linux'.

    Auto-detected from the running platform. The optional config "os_system" is
    honored ONLY when explicitly set to one of those three values (a manual
    override); anything else ("auto", missing, or invalid) falls back to
    detection — so a config copied from another machine never forces the wrong OS.
    """
    try:
        override = str(get_config().get("os_system", "")).strip().lower()
    except Exception:
        override = ""
    return override if override in _VALID_OS else detect_os()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"
