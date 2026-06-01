"""Apple Notes on macOS — create, list, search, append, read."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

_OS = platform.system()
_OSASCRIPT = "/usr/bin/osascript"
_NOTES_DIR = Path.home() / ".aria" / "notes"


def _run_applescript(script: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [_OSASCRIPT, "-e", script],
            capture_output=True,
            text=True,
            timeout=25,
        )
        out = (r.stdout or "").strip()
        if r.returncode == 0:
            return True, out
        return False, (r.stderr or out or f"exit {r.returncode}").strip()
    except Exception as e:
        return False, str(e)


def _esc(text: str, limit: int = 8000) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")[:limit]


def _local_list() -> str:
    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(_NOTES_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return "No notes saved yet."
    lines = [f"{i}. {f.stem}" for i, f in enumerate(files[:20], 1)]
    return f"Notes ({len(lines)}):\n" + "\n".join(lines)


def _local_create(title: str, body: str) -> str:
    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in title if c.isalnum() or c in " -_")[:80].strip() or "note"
    path = _NOTES_DIR / f"{safe}.txt"
    if path.exists():
        n = 2
        while (_NOTES_DIR / f"{safe} ({n}).txt").exists():
            n += 1
        path = _NOTES_DIR / f"{safe} ({n}).txt"
    path.write_text(f"# {title}\n\n{body}".strip() + "\n", encoding="utf-8")
    return f"Note saved: {path.stem}"


def _local_read(title: str) -> str:
    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    matches = [p for p in _NOTES_DIR.glob("*.txt") if title.lower() in p.stem.lower()]
    if not matches:
        return f"No note matching '{title}'."
    return matches[0].read_text(encoding="utf-8", errors="ignore")[:4000]


def _local_search(query: str) -> str:
    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    q = query.lower()
    hits: list[str] = []
    for p in _NOTES_DIR.glob("*.txt"):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if q in p.stem.lower() or q in text.lower():
            hits.append(p.stem)
    if not hits:
        return f"No notes matching '{query}'."
    return f"Found {len(hits)} note(s):\n" + "\n".join(f"{i}. {h}" for i, h in enumerate(hits[:15], 1))


def _list_notes(folder: str = "Notes", limit: int = 15) -> str:
    folder_safe = _esc(folder or "Notes", 80)
    script = f"""
set output to ""
set n to 0
tell application "Notes"
  repeat with nt in notes
    if n ≥ {int(limit)} then exit repeat
    set output to output & (name of nt) & linefeed
    set n to n + 1
  end repeat
end tell
return output
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: Could not list notes — {out}"
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return "No notes found."
    return f"Notes ({len(lines)}):\n" + "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, 1))


def _create_note(title: str, body: str, folder: str = "Notes") -> str:
    title_safe = _esc(title or "Untitled", 200)
    body_safe = _esc(body or "", 6000)
    script = f"""
tell application "Notes"
  set newNote to make new note with properties {{name:"{title_safe}", body:"{body_safe}"}}
  return name of newNote
end tell
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: Could not create note — {out}"
    return f"Note created: {out or title}"


def _read_note(title: str) -> str:
    q = _esc(title, 200)
    script = f"""
tell application "Notes"
  repeat with nt in notes
    if (name of nt) contains "{q}" then
      return body of nt
    end if
  end repeat
end tell
return ""
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: {out}"
    if not out:
        return f"No note matching '{title}'."
    if len(out) > 4000:
        out = out[:4000] + "\n\n[Truncated]"
    return out


def _append_note(title: str, text: str) -> str:
    q = _esc(title, 200)
    add = _esc(text, 4000)
    script = f"""
set found to false
tell application "Notes"
  repeat with nt in notes
    if (name of nt) contains "{q}" then
      set body of nt to (body of nt) & return & return & "{add}"
      set found to true
      exit repeat
    end if
  end repeat
end tell
return found
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: {out}"
    if out.lower() not in ("true", "yes"):
        return f"No note matching '{title}' to append to."
    return f"Appended to note: {title}"


def _search_notes(query: str, limit: int = 10) -> str:
    q = _esc(query, 120)
    script = f"""
set output to ""
set n to 0
tell application "Notes"
  repeat with nt in notes
    if n ≥ {int(limit)} then exit repeat
    set t to (name of nt) & " " & (body of nt)
    if t contains "{q}" then
      set output to output & (name of nt) & linefeed
      set n to n + 1
    end if
  end repeat
end tell
return output
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: {out}"
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return f"No notes matching '{query}'."
    return f"Found {len(lines)} note(s):\n" + "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, 1))


def _open_notes_app() -> str:
    try:
        subprocess.run(["open", "-a", "Notes"], check=False, timeout=8)
        return "Opened Notes app."
    except Exception as e:
        return f"FAILED: {e}"


def notes_control(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "list").lower().strip()
    title = (params.get("title") or params.get("name") or "").strip()
    body = (params.get("body") or params.get("content") or params.get("text") or "").strip()
    query = (params.get("query") or params.get("search") or title or "").strip()

    if player:
        player.write_log(f"[notes] {action} {title or query or ''}")

    use_local = _OS != "Darwin"

    if action in ("list", "list_all"):
        if use_local:
            return _local_list()
        out = _list_notes()
        if out.startswith("FAILED:"):
            return _local_list()
        return out

    if action in ("create", "add", "new"):
        if not body and not title:
            return "NEEDS_USER: What should the note say?"
        if use_local:
            return _local_create(title or body[:40], body or title)
        out = _create_note(title or body[:40], body or title)
        if out.startswith("FAILED:"):
            return _local_create(title or body[:40], body or title)
        return out

    if action in ("read", "get"):
        if not query:
            return "NEEDS_USER: Which note should I read?"
        if use_local:
            return _local_read(query)
        out = _read_note(query)
        if out.startswith("FAILED:"):
            return _local_read(query)
        return out

    if action in ("append", "add_to"):
        if not title or not body:
            return "NEEDS_USER: Need note title and text to append."
        if use_local:
            path = next((p for p in _NOTES_DIR.glob("*.txt") if title.lower() in p.stem.lower()), None)
            if not path:
                return f"No note matching '{title}'."
            path.write_text(path.read_text(encoding="utf-8") + "\n\n" + body, encoding="utf-8")
            return f"Appended to {path.stem}."
        out = _append_note(title, body)
        if out.startswith("FAILED:"):
            path = next((p for p in _NOTES_DIR.glob("*.txt") if title.lower() in p.stem.lower()), None)
            if not path:
                return out
            path.write_text(path.read_text(encoding="utf-8") + "\n\n" + body, encoding="utf-8")
            return f"Appended to {path.stem} (local fallback)."
        return out

    if action in ("search", "find"):
        if not query:
            return "NEEDS_USER: What should I search for in your notes?"
        if use_local:
            return _local_search(query)
        out = _search_notes(query)
        if out.startswith("FAILED:") or out.startswith("No notes matching"):
            local = _local_search(query)
            if not local.startswith("No notes"):
                return local
        return out

    if action == "open":
        if use_local:
            return _local_list()
        try:
            subprocess.run(["open", "-a", "Notes"], check=False, timeout=8)
            return "Opened Notes app."
        except Exception:
            return _local_list()

    return (
        f"Unknown action '{action}'. Use: list | create | read | append | search | open"
    )
