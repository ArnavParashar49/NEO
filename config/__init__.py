# config/__init__.py
"""Single source of truth for ARIA's local configuration (config/api_keys.json).

Action modules historically each copy-pasted their own loader. Prefer importing
from here:

    from config import get_config, get_api_key, get_os
"""
import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "api_keys.json"


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


def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", "windows").lower()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"
