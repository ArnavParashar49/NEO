"""Smart project planner — researches deeply, then hands off to VS Code AI."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from actions import project_session as ps
from actions.project_research import research_project, synthesize_brief


PROJECTS_DIR = Path.home() / "Desktop" / "AriaProjects"
PROMPT_FILENAME = "VSCODE_AI_PROMPT.md"
START_HERE = "START_HERE.md"


def _memory_hint() -> str:
    try:
        from memory.memory_manager import load_memory, format_memory_for_prompt

        return format_memory_for_prompt(load_memory())[:800]
    except Exception:
        return ""


def _log(msg: str, player=None):
    print(f"[ProjectBuilder] {msg}")
    if player:
        player.write_log(f"[ProjectBuilder] {msg}")


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", (name or "new_project").strip())[:40]


def _scaffold_folder(project_dir: Path, session: dict, player=None) -> None:
    """Minimal scaffold — the VS Code AI writes the real code."""
    project_dir.mkdir(parents=True, exist_ok=True)
    plan = session.get("plan") or {}
    vscode_prompt = (session.get("vscode_prompt") or "").strip()
    workflow = session.get("vscode_workflow") or (
        "ARIA opens your editor AI and submits the build prompt automatically."
    )

    features = plan.get("features") or []
    feat_lines = "\n".join(f"- {f}" for f in features)
    stack = ", ".join(session.get("stack") or []) or "TBD"

    readme = f"""# {session.get("project_name", "Project")}

**Kind:** {session.get("project_kind", "software project")}

## Your idea
{session.get("description", "")}

## Research summary (ARIA)
{session.get("research_summary", "")}

## Stack
{stack}

## Architecture
{session.get("architecture", "")}

## v1 features
{feat_lines or "- See VSCODE_AI_PROMPT.md"}

## Next step
ARIA opens your editor and **starts the AI build** with the master prompt in `{PROMPT_FILENAME}`.
When generation finishes, run: `{plan.get("run_command", "see README after build")}`

---
Planned by ARIA — implementation delegated to your editor AI.
"""
    (project_dir / "README.md").write_text(readme, encoding="utf-8")

    if vscode_prompt:
        (project_dir / PROMPT_FILENAME).write_text(vscode_prompt, encoding="utf-8")

    start = f"""# Start here

ARIA researched this project and **started the editor AI build** using `{PROMPT_FILENAME}`.

If the chat panel is open, the agent should already be generating code in this folder.
Otherwise open Composer (Cmd+I) or Copilot Chat — the prompt is on your clipboard.

**Folder:** `{project_dir}`
"""
    (project_dir / START_HERE).write_text(start, encoding="utf-8")

    for rel in (plan.get("suggested_files") or []):
        if not rel or rel.endswith((".md", ".txt", ".json")):
            continue
        p = project_dir / rel
        if not p.suffix:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("", encoding="utf-8")

    _log(f"Scaffolded {project_dir} (AI handoff)", player)


def _finalize_brief(session: dict, player=None, speak=None) -> tuple[dict, str]:
    _log("Researching stack and drafting VS Code AI prompt…", player)
    if player:
        player.write_activity("Drafting your VS Code build prompt…")

    brief_data = synthesize_brief(session, memory_hint=_memory_hint())

    missing = brief_data.get("missing_critical") or []
    follow_up = brief_data.get("follow_up_questions") or []
    if missing and follow_up:
        return session, ps.needs_input(
            "I need one more detail before I can prepare the handoff.",
            follow_up[:3],
        )

    session = ps.apply_brief(session, brief_data)
    kind = session.get("project_kind", "project")
    stack = ", ".join(session.get("stack") or []) or session.get("language", "")
    spoken = (session.get("user_brief") or "")[:200]
    msg = (
        f"I researched '{session.get('project_name')}' — a {kind}. "
        f"Recommended stack: {stack}. "
        f"I'll open your editor and **start the AI build** with a detailed prompt — "
        f"faster than coding from scratch. {spoken}"
    )
    if speak:
        speak(
            f"I'll open {session.get('project_name', 'your project')} in the editor "
            "and start the AI agent on your build prompt."
        )
    return session, ps.needs_confirm_build(msg)


def _start(description: str, project_name: str, player=None, speak=None) -> str:
    if not description.strip():
        return "Please describe what you want to build."

    ps.clear_session()

    _log(f"Deep research: {description}", player)
    if player:
        player.write_activity("Researching the best way to build this…")

    research = research_project(description, memory_hint=_memory_hint())
    session = ps.start_session(description, research, project_name=project_name)

    intro = research.get("intro_message") or research.get("research_summary", "")
    questions = session.get("questions") or []

    if questions:
        return ps.needs_input(intro, questions)

    _log("Enough detail — preparing VS Code handoff.", player)
    session, _ = _finalize_brief(session, player, speak=None)
    return _build({"confirm": True}, player, speak)


def _answer(user_input: str, player=None, speak=None) -> str:
    session = ps.load_session()
    if not session or session.get("phase") not in ("gathering", "ready"):
        return "No active project. Call project_builder with action=start first."

    if not user_input.strip():
        qs = session.get("questions") or ["What else should I know?"]
        return ps.needs_input("I didn't catch that.", qs[:3])

    session = ps.merge_answers(session, user_input)
    _, confirm = _finalize_brief(session, player, speak)
    return confirm


def _build(params: dict, player=None, speak=None) -> str:
    proceed, cancelled, err = ps.consume_build_confirm(params)
    if cancelled:
        return err or ps.cancel_message()
    if not proceed:
        session = ps.load_session()
        if not session or session.get("phase") != "ready":
            return "Nothing ready yet. Use action=start first."
        return ps.needs_confirm_build("Ready when you are.")

    session = ps.load_session()
    if not session:
        return "Session expired. Start again with action=start."

    proj_name = _safe_name(session.get("project_name", "new_project"))
    project_dir = PROJECTS_DIR / proj_name
    vscode_prompt = (session.get("vscode_prompt") or "").strip()

    if not vscode_prompt:
        brief_data = synthesize_brief(session, memory_hint=_memory_hint())
        session = ps.apply_brief(session, brief_data)
        vscode_prompt = session.get("vscode_prompt", "")

    _log(f"Starting editor AI build at {project_dir}", player)
    if speak:
        speak(
            f"Opening {proj_name.replace('_', ' ')} and starting the editor AI on your build prompt."
        )

    _scaffold_folder(project_dir, session, player)

    from actions.editor_ai_launch import launch_editor_ai_build
    from actions.editor_open import open_project_folder, open_prompt_in_editor

    opened, editor_id = open_project_folder(project_dir)
    time.sleep(2.0 if opened else 0.5)

    launch = launch_editor_ai_build(
        project_dir,
        vscode_prompt,
        editor=editor_id or "auto",
    )

    if not launch.get("ok"):
        open_prompt_in_editor(project_dir, PROMPT_FILENAME)

    session["phase"] = "handoff"
    ps.save_session(session)

    if launch.get("ok"):
        ai_note = launch.get("detail", "Editor AI build started.")
    else:
        ai_note = (
            "Could not auto-start the editor AI — open Copilot or Composer; "
            "the prompt is on your clipboard."
        )

    result = (
        f"Done. I researched '{proj_name}' and set up the project at {project_dir}. "
        f"{ai_note} "
        f"Watch the editor — it should be writing your app now. "
        f"When finished, run: {session.get('plan', {}).get('run_command', 'see README')}."
    )

    if player:
        player.write_log(f"Aria: {result[:500]}")

    ps.clear_session()
    return result


def project_builder(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    p = parameters or {}
    action = (p.get("action") or "start").lower().strip()
    description = (p.get("description") or "").strip()
    user_input = (p.get("user_input") or p.get("answers") or "").strip()
    project_name = (p.get("project_name") or "").strip()

    if action == "cancel" or p.get("cancel"):
        return ps.cancel_message()

    if action == "status":
        session = ps.load_session()
        if not session:
            return "No active project session."
        return (
            f"Active: {session.get('description')} | "
            f"kind={session.get('project_kind')} | "
            f"phase={session.get('phase')} | "
            f"name={session.get('project_name')} | "
            f"mode=vscode_ai_handoff"
        )

    if action == "answer":
        return _answer(user_input, player, speak)

    if action == "build":
        return _build(p, player, speak)

    return _start(description, project_name, player, speak)
