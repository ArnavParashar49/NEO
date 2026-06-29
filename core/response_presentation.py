"""Pure response-presentation rules shared by voice and UI flows."""

from __future__ import annotations

import re


_COMMAND_REQUEST_PATTERNS = (
    re.compile(r"\b(?:what(?:'s| is)|give|show|tell)\b.*\bcommand\b", re.I),
    re.compile(r"\bcommand\s+(?:to|for)\s+(?:download|install|set up|setup)\b", re.I),
    re.compile(
        r"\bhow\s+(?:do|can|should)\s+i\b.*\b(?:download|install|set up|setup)\b"
        r".*\b(?:cli|terminal|powershell|command prompt|shell)\b",
        re.I,
    ),
)


def is_command_request(text: str) -> bool:
    """Return whether the user wants a command shown, not executed."""
    normalized = " ".join((text or "").split())
    return bool(normalized) and any(
        pattern.search(normalized) for pattern in _COMMAND_REQUEST_PATTERNS
    )
