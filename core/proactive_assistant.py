"""Bounded proactive behavior: suggestions, confirmations, and daily briefings."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from core.paths import base_dir


_STATE_PATH = base_dir() / "memory" / "proactive_state.json"
_YES = re.compile(r"^(?:yes|yeah|yep|sure|okay|ok|do it|please do|set it)(?:\s+please)?[.!]?$", re.I)
_NO = re.compile(r"^(?:no|nope|nah|cancel|don't|do not|not now)[.!]?$", re.I)
_DATE_QUERY = re.compile(r"\b(?:when|what day|what date|how long until)\b", re.I)
_IMPORTANT_WORK = re.compile(
    r"\b(?:deadline|due|submission|appointment|meeting|interview|exam|renewal|payment|work)\b",
    re.I,
)
_pending_lock = threading.Lock()
_pending: "PendingAction | None" = None
_news_lock = threading.Lock()
_news_inflight = False


@dataclass(frozen=True)
class PendingAction:
    kind: str
    question: str
    parameters: dict


def _tomorrow() -> str:
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


def analyze_turn(user_text: str, response: str) -> str | None:
    """Offer one high-confidence follow-up and stage its concrete action."""
    global _pending
    user = (user_text or "").strip()
    answer = (response or "").strip()
    if not user or re.search(r"\bremind(?:er| me)?\b", f"{user} {answer}", re.I):
        return None

    target_date = ""
    if re.search(r"\btomorrow\b", answer, re.I) and _DATE_QUERY.search(user):
        target_date = _tomorrow()
    elif re.search(r"\btomorrow\b", user, re.I) and _IMPORTANT_WORK.search(user):
        target_date = _tomorrow()
    if not target_date:
        return None

    event = re.sub(r"\s+", " ", user).strip(" ?.!")[:120]
    question = "Want me to remind you tomorrow at 9:00 AM?"
    action = PendingAction(
        kind="reminder",
        question=question,
        parameters={
            "action": "set",
            "message": event or "Follow up",
            "date": target_date,
            "time": "09:00",
        },
    )
    with _pending_lock:
        _pending = action
    return question


def consume_reply(text: str) -> tuple[str, dict] | None:
    """Consume an unambiguous yes/no reply to the current proactive question."""
    global _pending
    value = (text or "").strip()
    with _pending_lock:
        if _pending is None:
            return None
        if _NO.fullmatch(value):
            _pending = None
            return "cancelled", {}
        if not _YES.fullmatch(value):
            return None
        action = _pending
        _pending = None
        return action.kind, dict(action.parameters)


def stage_news_offer() -> str:
    """Offer a briefing without fetching or displaying anything first."""
    global _pending
    question = "Want me to check for any genuinely important updates too?"
    with _pending_lock:
        _pending = PendingAction(kind="news", question=question, parameters={})
    return question


def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_daily_news_briefing(ui, *, force: bool = False) -> None:
    """Fetch an explicitly approved briefing, at most once daily unless forced."""
    global _news_inflight
    today = date.today().isoformat()
    with _news_lock:
        if _news_inflight or (not force and _load_state().get("last_news_date") == today):
            return
        _news_inflight = True
    try:
        from actions.web_search import _gemini_search_with_retry

        result = _gemini_search_with_retry(
            "Return at most 2 urgent developments from the last 24 hours only if they require "
            "attention or have major immediate consequences for AI safety, cybersecurity, India, "
            "or global safety. Ordinary news, company milestones, product releases, funding, "
            "politics-as-usual, research announcements, and forecasts are NOT important. "
            "If nothing clears that threshold, reply exactly NO_IMPORTANT_NEWS. "
            "Use one concise sentence per item and include a source URL.",
            attempts=2,
        ).strip()
        if result and result != "NO_IMPORTANT_NEWS":
            ui.write_log(f"Neo: Important update: {result}")
        elif result == "NO_IMPORTANT_NEWS":
            ui.write_log("Neo: Nothing genuinely important needs your attention right now.")
        state = _load_state()
        state["last_news_date"] = today
        _save_state(state)
    except Exception as exc:
        print(f"[Proactive] Daily briefing skipped: {exc}")
    finally:
        with _news_lock:
            _news_inflight = False
