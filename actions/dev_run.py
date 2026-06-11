"""Sandbox-guarded command runner for the autonomous build loop.

The build loop (core.agent_loop.run_build) uses this to install dependencies and
run/test the project it is writing — reading the *real* stdout/stderr so it can
fix its own errors. Execution is confined to ~/Desktop/AriaProjects/<name>; any
path outside that sandbox is refused.
"""
from __future__ import annotations

from pathlib import Path

from actions.dev_agent import PROJECTS_DIR, run_command_in


def _resolve_sandbox(project: str) -> Path | None:
    """Resolve a project folder, but only if it lives under AriaProjects."""
    base = PROJECTS_DIR.resolve()
    if not project:
        return None
    p = Path(project).expanduser()
    cand = (p if p.is_absolute() else base / project).resolve()
    if cand != base and base not in cand.parents:
        return None  # outside the sandbox — refuse
    return cand


def dev_run(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    p = parameters or {}
    command = (p.get("command") or "").strip()
    project = (p.get("project_dir") or p.get("project_name") or "").strip()
    try:
        timeout = int(p.get("timeout", 60) or 60)
    except (TypeError, ValueError):
        timeout = 60

    if not command:
        return "dev_run needs a command (e.g. 'python main.py')."

    proj = _resolve_sandbox(project)
    if proj is None:
        return (
            f"dev_run refused: '{project}' is outside the build sandbox "
            f"({PROJECTS_DIR}). Only commands inside a project there are allowed."
        )
    if not proj.exists():
        return f"dev_run: project folder does not exist yet: {proj}"

    if player:
        try:
            player.write_log(f"[build] ▶ {command}")
        except Exception:
            pass

    return run_command_in(command, proj, timeout=min(max(timeout, 5), 180))
