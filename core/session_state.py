"""Session persistence for NEO Orchestrator."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.paths import base_dir

logger = logging.getLogger(__name__)

_SESSION_FILENAME = "neo_session.json"


def _session_path() -> Path:
    """Return the canonical session file path under the project root."""
    return base_dir() / _SESSION_FILENAME


def save_session(state: dict[str, Any]) -> None:
    """Save the current execution state to a JSON file."""
    try:
        path = _session_path()
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to save session: %s", exc)


def load_session() -> dict[str, Any] | None:
    """Load a pending execution state if one exists."""
    path = _session_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load session: %s", exc)
        return None


def clear_session() -> None:
    """Clear the session file after successful completion."""
    path = _session_path()
    if path.exists():
        try:
            path.unlink()
        except Exception as exc:
            logger.warning("Failed to clear session: %s", exc)
