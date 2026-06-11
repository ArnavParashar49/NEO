"""Persistent state for guided project-building conversations."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import sys


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


SESSION_PATH = _base_dir() / "memory" / "active_project.json"


def _empty() -> dict[str, Any]:
    return {
        "session_id": "",
        "description": "",
        "project_kind": "",
        "project_name": "",
        "stack": [],
        "language": "",
        "architecture": "",
        "research_summary": "",
        "plan": {},
        "questions": [],
        "ready_to_build": False,
        "answers": {},
        "user_brief": "",
        "vscode_prompt": "",
        "delivery_mode": "vscode_ai",
        "phase": "idle",
        "created_at": 0.0,
        "updated_at": 0.0,
    }


def load_session() -> dict[str, Any] | None:
    if not SESSION_PATH.exists():
        return None
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("session_id") else None
    except Exception as e:
        print(f"[ProjectSession] Load error: {e}")
        return None


def save_session(data: dict[str, Any]) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.time()
    SESSION_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_session() -> None:
    if SESSION_PATH.exists():
        try:
            SESSION_PATH.unlink()
        except Exception:
            pass


def start_session(
    description: str,
    research: dict[str, Any],
    project_name: str = "",
) -> dict[str, Any]:
    session = _empty()
    session["session_id"] = uuid.uuid4().hex[:12]
    session["description"] = description.strip()
    session["project_kind"] = research.get("project_kind") or research.get("project_type", "")
    session["project_name"] = project_name or research.get("project_name", "new_project")
    session["stack"] = research.get("stack", [])
    session["language"] = research.get("language", "")
    session["architecture"] = research.get("architecture", "")
    session["research_summary"] = research.get("research_summary", "")
    session["delivery_strategy"] = research.get("delivery_strategy", "")
    session["vscode_workflow"] = research.get("vscode_workflow", "")
    session["plan"] = research.get("plan", {})
    session["questions"] = research.get("questions", [])
    session["ready_to_build"] = bool(research.get("ready_to_build"))
    session["answers"] = {}
    session["phase"] = "gathering"
    session["created_at"] = time.time()
    save_session(session)
    return session


def merge_answers(session: dict[str, Any], user_input: str) -> dict[str, Any]:
    text = (user_input or "").strip()
    if text:
        session.setdefault("raw_answers", []).append(text)
        session["answers"]["latest"] = text
        session["answers"]["combined"] = "\n".join(session.get("raw_answers", []))
    save_session(session)
    return session


def apply_brief(session: dict[str, Any], brief_data: dict[str, Any]) -> dict[str, Any]:
    session["vscode_prompt"] = brief_data.get("vscode_prompt", "").strip()
    session["user_brief"] = (
        brief_data.get("user_brief") or brief_data.get("vscode_prompt", "")
    ).strip()
    if brief_data.get("project_name"):
        session["project_name"] = brief_data["project_name"]
    if brief_data.get("language"):
        session["language"] = brief_data["language"]
    if brief_data.get("stack"):
        session["stack"] = brief_data["stack"]
    if brief_data.get("vscode_workflow"):
        session["vscode_workflow"] = brief_data["vscode_workflow"]
    session["ready_to_build"] = bool(brief_data.get("ready_to_build", True))
    session["phase"] = "ready"
    save_session(session)
    return session


def needs_input(message: str, questions: list[str]) -> str:
    q_text = " ".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    return (
        f"NEEDS_INPUT: {message} "
        f"Ask the user naturally: {q_text} "
        "When they reply, call project_builder with action=answer and user_input set to their full answer."
    )


def needs_confirm_build(summary: str) -> str:
    return (
        f"NEEDS_CONFIRM: {summary} "
        'Ask the user: "Want me to build this now?" '
        "If yes, call project_builder with action=build and confirm true. "
        "If no, call with cancel true."
    )


def cancel_message() -> str:
    clear_session()
    return "CANCELLED: Project build cancelled."


def consume_build_confirm(params: dict) -> tuple[bool, bool, str | None]:
    cancel = str(params.get("cancel", "")).lower() in ("true", "1", "yes")
    confirm = str(params.get("confirm", "")).lower() in ("true", "1", "yes")
    if cancel:
        return False, True, cancel_message()
    if confirm:
        return True, False, None
    return False, False, None
