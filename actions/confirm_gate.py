"""Shared confirm-before-dangerous-action gate (email, delete, merge, shutdown, etc.)."""

from __future__ import annotations

from typing import Any

_pending: dict[str, Any] | None = None


def as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower().strip() in ("true", "1", "yes", "y", "confirm", "ok")


def clear_pending() -> None:
    global _pending
    _pending = None


def cancel_message(label: str = "Action") -> str:
    clear_pending()
    return f"CANCELLED: {label} cancelled."


def needs_confirm(
    action_id: str,
    summary: str,
    params: dict,
    *,
    ask: str = "Should I go ahead?",
) -> str:
    """Store pending op and return NEEDS_CONFIRM for the model."""
    global _pending
    _pending = {"id": action_id, "params": dict(params)}
    return (
        f"NEEDS_CONFIRM: {summary} "
        f"Ask the user: \"{ask}\" "
        "If they clearly say yes, call the same tool again with confirm true. "
        "If no, call with cancel true. Never claim it is done until you see success in the tool result."
    )


def consume_confirmed(params: dict) -> tuple[bool, dict | None, str | None]:
    """
    Returns (proceed, pending_params, error_message).
    proceed=True with pending_params when confirmed and ids match.
    """
    global _pending

    if as_bool(params.get("cancel")):
        return False, None, cancel_message()

    if not as_bool(params.get("confirm")):
        return False, None, None

    if _pending:
        stored = _pending
        clear_pending()
        return True, stored.get("params") or {}, None

    return True, dict(params), None


def merge_params(request_params: dict, stored_params: dict) -> dict:
    """Replay stored params but keep confirm/cancel from the confirmation call."""
    merged = dict(stored_params)
    merged["confirm"] = request_params.get("confirm")
    merged["cancel"] = request_params.get("cancel")
    return merged
