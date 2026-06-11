"""Autonomous project builder — ARIA researches, writes, runs, and fixes it itself.

Replaces the old VS Code / Copilot handoff. On the first call it asks for one
confirmation (the build will run & install things); after a yes it drives the
autonomous tool-use loop (core.agent_loop.run_build) to actually build the
project, falling back to dev_agent's fixed pipeline if the loop stalls.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECTS_DIR = Path.home() / "Desktop" / "AriaProjects"

_STOP_WORDS = {
    "build", "make", "create", "develop", "a", "an", "the", "me", "please",
    "app", "application", "that", "to", "for", "my", "some", "simple", "new",
    "project", "program", "can", "you", "i", "want", "need", "with",
}


def _log(msg: str, player=None) -> None:
    print(f"[ProjectBuilder] {msg}")
    if player:
        try:
            player.write_log(f"[ProjectBuilder] {msg}")
        except Exception:
            pass


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", (name or "new_project").strip())[:40] or "new_project"


def _derive_name(description: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", (description or "").lower())
    kept = [w for w in words if w not in _STOP_WORDS][:3]
    return "_".join(kept) or "new_project"


def _build_goal(description: str, proj_name: str, project_dir: Path) -> str:
    return (
        f"Build this and make it actually run: {description.strip()}\n\n"
        f"Project name: {proj_name}\n"
        f"Project folder (create ALL files here with file_controller create_file, using "
        f"absolute paths): {project_dir}\n\n"
        "Decide the simplest solid stack, write complete real code for every file, then use "
        f"dev_run (project_dir={project_dir}) to install dependencies and run it. Read the real "
        "output; if it errors, rewrite the file and run again until it runs cleanly. For a static "
        "website just create the files. When it works, reply with what you built and the exact "
        "command to run it."
    )


def _stream_step(step, player) -> None:
    """Map an autonomous step to a friendly chat line so the build is watchable."""
    if not player:
        return
    tool = step.tool
    args = step.args or {}
    msg = None
    if tool == "file_controller":
        if str(args.get("action", "")).startswith(("create_file", "write")):
            target = args.get("name") or args.get("path") or "file"
            msg = f"📝 wrote {Path(str(target)).name}"
    elif tool == "dev_run":
        cmd = (args.get("command") or "").strip()
        msg = f"▶️ {cmd}" if cmd else "▶️ running"
    elif tool == "web_search":
        q = (args.get("query") or "").strip()
        msg = f"🔎 researching {q[:40]}" if q else "🔎 researching"
    elif tool == "code_helper":
        msg = "⚙️ running code"
    if msg:
        try:
            player.write_log(f"[build] {msg}")
        except Exception:
            pass


def _build_now(description: str, project_name: str, player=None, speak=None, ctx=None) -> str:
    """Build the project autonomously, immediately — no confirmation gate."""
    if not description.strip():
        return "Please describe what you want to build."
    proj_name = _safe_name(project_name or _derive_name(description))
    project_dir = PROJECTS_DIR / proj_name
    project_dir.mkdir(parents=True, exist_ok=True)

    if speak:
        speak(f"Building {proj_name.replace('_', ' ')} now. I'll write and test it myself.")
    _log(f"Autonomous build: {description} -> {project_dir}", player)
    if player:
        try:
            player.write_log(
                f"Aria: Building {proj_name.replace('_', ' ')} — I'll write, run, and fix it myself."
            )
        except Exception:
            pass

    goal = _build_goal(description, proj_name, project_dir)
    if ctx is None:
        from hybrid.types import ExecutionContext
        ctx = ExecutionContext(ui=player, speak=speak)

    def _stream_plan(plan: str) -> None:
        if not player or not plan:
            return
        try:
            player.write_log("[build] 🧭 Plan:")
            for line in plan.splitlines():
                if line.strip():
                    player.write_log(f"[build]   {line.rstrip()}")
        except Exception:
            pass

    answer, stalled = "", False
    try:
        from core.agent_loop import run_build

        result = run_build(
            goal, ctx,
            on_step=lambda s: _stream_step(s, player),
            on_plan=_stream_plan,
        )
        answer = (result.answer or "").strip()
        stalled = result.stopped_reason in ("max_steps", "loop_guard") or not answer
    except Exception as e:
        _log(f"autonomous build error: {e}", player)
        stalled = True

    if stalled:
        if player:
            try:
                player.write_log("Aria: Letting my built-in builder finish this up…")
            except Exception:
                pass
        try:
            from actions.dev_agent import _build_project

            answer = _build_project(description, "python", proj_name, 30, speak, player)
        except Exception as e:
            answer = answer or f"I set up {project_dir} but couldn't finish the build automatically ({e})."

    final = answer or f"Project set up at {project_dir}."
    if player:
        try:
            player.write_log(f"Aria: {final[:500]}")
        except Exception:
            pass
    return final


def project_builder(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
    ctx=None,
) -> str:
    p = parameters or {}
    action = (p.get("action") or "start").lower().strip()
    description = (p.get("description") or "").strip()
    project_name = (p.get("project_name") or "").strip()

    if action == "cancel" or p.get("cancel"):
        return "CANCELLED: build cancelled."

    if action == "status":
        return "Autonomous builder is ready — say 'build me <something>'."

    # any action (start/build/…) → build immediately, no confirmation
    return _build_now(description, project_name, player, speak, ctx)
