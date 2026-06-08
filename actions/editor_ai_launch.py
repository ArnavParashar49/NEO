"""Start in-editor AI builds: open project, inject prompt, submit."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from actions.editor_open import _activate_app, copy_text_to_clipboard
from core.platform_utils import mod_key


def _load_os() -> str:
    from config import get_os
    return get_os()


def _is_mac() -> bool:
    return _load_os() in ("mac", "darwin") or sys.platform == "darwin"


def _cursor_cli() -> str | None:
    p = Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor")
    if p.exists():
        return str(p)
    return shutil.which("cursor")


def _code_cli() -> str | None:
    for p in (
        Path("/usr/local/bin/code"),
        Path.home() / "Library/Application Support/Code/bin/code",
        Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
    ):
        if p.exists():
            return str(p)
    return shutil.which("code")


def _app_installed(app_name: str) -> bool:
    if not _is_mac():
        return False
    return Path(f"/Applications/{app_name}.app").exists()


def detect_editor(preference: str = "auto") -> str:
    """Return 'cursor' | 'vscode' | ''."""
    pref = (preference or "auto").lower().strip()
    if pref == "cursor" and _app_installed("Cursor"):
        return "cursor"
    if pref in ("vscode", "code") and _app_installed("Visual Studio Code"):
        return "vscode"
    if pref == "auto":
        if _app_installed("Visual Studio Code"):
            return "vscode"
        if _app_installed("Cursor"):
            return "cursor"
    return ""


def write_agent_context(project_dir: Path, prompt: str) -> None:
    """Cursor/CLI read AGENTS.md at project root."""
    path = Path(project_dir) / "AGENTS.md"
    body = f"""# ARIA build handoff

ARIA researched this project. Implement the full v1 in this workspace.

---

{prompt.strip()}
"""
    path.write_text(body, encoding="utf-8")


def _try_vscode_chat_command(project_dir: Path, prompt: str) -> bool:
    code = _code_cli()
    if not code:
        return False
    try:
        r = subprocess.run(
            [code, "--command", "workbench.action.chat.open"],
            capture_output=True,
            timeout=10,
            cwd=str(project_dir),
        )
        if r.returncode != 0:
            return False
        time.sleep(1.2)
        return _ui_paste_and_submit(prompt, editor="vscode")
    except Exception as e:
        print(f"[EditorAILaunch] VS Code command path failed: {e}")
        return False


def _try_cursor_cli_agent(project_dir: Path, prompt: str) -> bool:
    """Background Cursor Agent CLI (fallback when UI automation fails)."""
    cli = _cursor_cli()
    if not cli:
        return False
    kickoff = (
        "Read AGENTS.md and VSCODE_AI_PROMPT.md in this workspace. "
        "Implement the complete working v1 now. Use agent mode with full file edits."
    )
    cmd = [
        cli,
        "agent",
        "--force",
        "--workspace",
        str(Path(project_dir).resolve()),
        kickoff,
    ]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(project_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print("[EditorAILaunch] Started Cursor CLI agent in background")
        return True
    except Exception as e:
        print(f"[EditorAILaunch] Cursor CLI agent failed: {e}")
        return False


def _ui_paste_and_submit(prompt: str, editor: str) -> bool:
    try:
        import pyautogui
    except ImportError:
        print("[EditorAILaunch] pyautogui not installed")
        return False

    if not copy_text_to_clipboard(prompt):
        return False

    app = "Cursor" if editor == "cursor" else "Visual Studio Code"
    _activate_app(app)
    time.sleep(1.2)

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.06

    if editor == "cursor":
        pyautogui.hotkey(mod_key(),"i")
        time.sleep(1.0)
    else:
        # Copilot Chat — try default shortcut, then command palette
        pyautogui.hotkey(mod_key(),"shift", "i")
        time.sleep(0.8)
        # If chat did not open, palette fallback
        pyautogui.hotkey(mod_key(),"shift", "p")
        time.sleep(0.5)
        pyautogui.write("Copilot: Open Chat", interval=0.02)
        time.sleep(0.4)
        pyautogui.press("enter")
        time.sleep(0.9)

    pyautogui.hotkey(mod_key(),"v")
    time.sleep(0.35)
    pyautogui.press("enter")
    print(f"[EditorAILaunch] Submitted prompt via {editor} UI")
    return True


def launch_editor_ai_build(
    project_dir: Path,
    prompt: str,
    *,
    editor: str = "auto",
    allow_cli_fallback: bool = True,
) -> dict:
    """
    Open editor, inject prompt into AI chat/composer, and submit.
    Returns {ok, method, editor, detail}.
    """
    project_dir = Path(project_dir).resolve()
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "method": "none", "editor": "", "detail": "empty prompt"}

    write_agent_context(project_dir, prompt)
    which = detect_editor(editor)
    if not which:
        return {
            "ok": False,
            "method": "none",
            "editor": "",
            "detail": "No VS Code or Cursor found in /Applications",
        }

    copy_text_to_clipboard(prompt)

    # Editor should already be open with folder; ensure focus
    _activate_app("Cursor" if which == "cursor" else "Visual Studio Code")
    time.sleep(1.5)

    if which == "vscode" and _try_vscode_chat_command(project_dir, prompt):
        return {
            "ok": True,
            "method": "vscode_chat_ui",
            "editor": "vscode",
            "detail": "Opened Copilot Chat and submitted your build prompt.",
        }

    if _ui_paste_and_submit(prompt, editor=which):
        label = "Cursor Composer" if which == "cursor" else "Copilot Chat"
        return {
            "ok": True,
            "method": f"{which}_composer_ui",
            "editor": which,
            "detail": f"Opened {label} and started the build.",
        }

    if allow_cli_fallback and which == "cursor" and _try_cursor_cli_agent(project_dir, prompt):
        return {
            "ok": True,
            "method": "cursor_cli_agent",
            "editor": "cursor",
            "detail": "Started Cursor Agent in the background from your prompt.",
        }

    return {
        "ok": False,
        "method": "clipboard_only",
        "editor": which,
        "detail": "Prompt is on clipboard — paste into Copilot or Composer if the window did not auto-start.",
    }
