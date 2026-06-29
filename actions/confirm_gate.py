"""Shared confirm-before-dangerous-action gate (email, delete, merge, shutdown, etc.).

Security model
--------------
A destructive tool first calls :func:`needs_confirm` to *stage* the operation and
returns ``NEEDS_CONFIRM`` to the model. The model must then re-call the same tool
with ``confirm: true``. :func:`consume_confirmed` only releases the staged
parameters when the re-call comes from the *same* action (``action_id`` match),
so a stale confirmation meant for one tool can never trigger a different staged
operation. Every authorization is written to an append-only audit log.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_pending: dict[str, Any] | None = None

# Append-only audit trail of authorized/cancelled destructive actions.
# Lives under cache/ (gitignored, local-only).
_AUDIT_PATH = Path(__file__).resolve().parent.parent / "cache" / "action_audit.log"


def as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower().strip() in ("true", "1", "yes", "y", "confirm", "ok")


def _summarize(params: dict) -> str:
    """Short, single-line, secret-free rendering of params for the audit log."""
    parts = []
    for k, v in (params or {}).items():
        if k in ("confirm", "cancel"):
            continue
        if any(s in k.lower() for s in ("key", "token", "password", "secret")):
            v = "***"
        text = str(v)
        if len(text) > 80:
            text = text[:77] + "..."
        parts.append(f"{k}={text}")
    return ", ".join(parts)


def audit(action_id: str, params: dict, status: str) -> None:
    """Best-effort append to the local action audit log. Never raises."""
    try:
        from datetime import datetime

        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        line = f"{stamp}\t{status}\t{action_id}\t{_summarize(params)}\n"
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def clear_pending() -> None:
    global _pending
    _pending = None


def peek_pending(action_id: str | None = None) -> dict | None:
    """Return a copy of a matching staged action without authorizing it."""
    if not _pending or (action_id is not None and _pending.get("id") != action_id):
        return None
    return dict(_pending.get("params") or {})


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


def consume_confirmed(
    params: dict,
    action_id: str | None = None,
) -> tuple[bool, dict | None, str | None]:
    """
    Returns (proceed, pending_params, error_message).

    proceed=True with pending_params when confirmed and (if ``action_id`` is
    given) the staged operation matches the calling action. A confirmation that
    does not match the staged op is rejected so the caller re-stages, preventing
    a stale confirm from firing a different destructive action.
    """
    global _pending

    if as_bool(params.get("cancel")):
        if _pending and (action_id is None or _pending.get("id") == action_id):
            audit(action_id or _pending.get("id", "?"), _pending.get("params") or {}, "CANCELLED")
        return False, None, cancel_message()

    if not as_bool(params.get("confirm")):
        return False, None, None

    if _pending:
        # A staged op exists. If the caller identifies itself, it must match.
        if action_id is not None and _pending.get("id") != action_id:
            return False, None, None
        stored = _pending
        clear_pending()
        audit(stored.get("id", action_id or "?"), stored.get("params") or {}, "CONFIRMED")
        return True, stored.get("params") or {}, None

    # confirm=true with nothing staged (e.g. process restarted between turns).
    # Preserve prior behavior but record it so the trail is complete.
    audit(action_id or "?", params, "CONFIRMED_UNSTAGED")
    return True, dict(params), None


def merge_params(request_params: dict, stored_params: dict) -> dict:
    """Replay stored params but keep confirm/cancel from the confirmation call."""
    merged = dict(stored_params)
    merged["confirm"] = request_params.get("confirm")
    merged["cancel"] = request_params.get("cancel")
    return merged
