"""Canonical filesystem paths for ARIA.

Replaces the ~13 copy-pasted ``_get_base_dir()`` helpers scattered across modules.
Works the same whether running from source or a frozen (PyInstaller) build, and
resolves identically regardless of which module imports it (``core/`` sits at the
project root).

    from core.paths import base_dir, config_path
"""

from __future__ import annotations

import sys
from pathlib import Path


def base_dir() -> Path:
    """Project root (or the executable's dir when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return base_dir() / "config" / "api_keys.json"
