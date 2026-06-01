"""Open projects in VS Code / Cursor and bring them to the front (macOS-friendly)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def _run(cmd: list[str], timeout: int = 8) -> bool:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode == 0
    except Exception as e:
        print(f"[EditorOpen] {cmd[0]} failed: {e}")
        return False


def _activate_app(app_name: str) -> None:
    if sys.platform != "darwin":
        return
    script = f'tell application "{app_name}" to activate'
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)


def open_project_folder(project_dir: Path) -> tuple[bool, str]:
    """Open folder in VS Code/Cursor. Returns (success, editor_id: vscode|cursor|'')."""
    project_dir = Path(project_dir).resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    path = str(project_dir)

    if sys.platform == "darwin":
        # Finder first so user sees the folder even if editor fails
        _run(["open", path])

        for app, eid in (
            ("Visual Studio Code", "vscode"),
            ("Cursor", "cursor"),
            ("Code", "vscode"),
        ):
            if _run(["open", "-a", app, path]):
                time.sleep(0.8)
                _activate_app(app)
                print(f"[EditorOpen] Opened in {app}: {path}")
                return True, eid

        # VS Code CLI common locations
        cli_paths = [
            "/usr/local/bin/code",
            str(Path.home() / "Library/Application Support/Code/bin/code"),
        ]
        for cli in cli_paths:
            if Path(cli).exists() and _run([cli, path]):
                time.sleep(0.8)
                _activate_app("Visual Studio Code")
                print(f"[EditorOpen] Opened via {cli}")
                return True, "vscode"

        print(f"[EditorOpen] ⚠️ No editor found — opened Finder: {path}")
        return False, ""

    if sys.platform == "win32":
        for cmd in (
            ["code", path],
            [rf"C:\Users\{Path.home().name}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd", path],
        ):
            if _run(cmd):
                return True, "vscode"
        return False, ""

    if _run(["code", path]):
        return True, "vscode"
    return False, ""


def open_terminal_run(project_dir: Path, command: str) -> None:
    """Open Terminal (macOS) in project folder with optional run command."""
    if sys.platform != "darwin" or not command:
        return
    path = str(Path(project_dir).resolve())
    safe_cmd = command.replace('"', '\\"')
    script = (
        f'tell application "Terminal"\n'
        f'  activate\n'
        f'  do script "cd \\"{path}\\" && {safe_cmd}"\n'
        f'end tell'
    )
    try:
        subprocess.run(["osascript", "-e", script], timeout=8)
        print(f"[EditorOpen] Terminal: cd {path} && {command}")
    except Exception as e:
        print(f"[EditorOpen] Terminal open failed: {e}")


def copy_text_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard (macOS pbcopy)."""
    if not (text or "").strip():
        return False
    if sys.platform == "darwin":
        try:
            p = subprocess.run(
                ["pbcopy"],
                input=text,
                text=True,
                capture_output=True,
                timeout=5,
            )
            if p.returncode == 0:
                print("[EditorOpen] Copied VS Code AI prompt to clipboard")
                return True
        except Exception as e:
            print(f"[EditorOpen] pbcopy failed: {e}")
    elif sys.platform == "win32":
        try:
            subprocess.run(
                ["clip"],
                input=text,
                text=True,
                capture_output=True,
                timeout=5,
                check=True,
            )
            return True
        except Exception:
            pass
    return False


def open_prompt_in_editor(project_dir: Path, filename: str) -> bool:
    """Open a file inside the project in VS Code/Cursor."""
    path = Path(project_dir) / filename
    if not path.exists():
        return False
    full = str(path.resolve())
    if sys.platform == "darwin":
        for app in ("Visual Studio Code", "Cursor", "Code"):
            if _run(["open", "-a", app, full]):
                time.sleep(0.5)
                _activate_app(app)
                return True
        for cli in (
            "/usr/local/bin/code",
            str(Path.home() / "Library/Application Support/Code/bin/code"),
        ):
            if Path(cli).exists() and _run([cli, full]):
                return True
    return _run(["code", full])


def open_static_preview(project_dir: Path, entry: str = "index.html") -> None:
    """Open HTML file in default browser."""
    p = Path(project_dir) / entry
    if not p.exists():
        return
    if sys.platform == "darwin":
        _run(["open", str(p)])
    elif sys.platform == "win32":
        _run(["start", "", str(p)])
    else:
        _run(["xdg-open", str(p)])

