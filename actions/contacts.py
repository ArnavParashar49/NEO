"""Contacts — saved in ARIA memory + macOS Contacts lookup."""

from __future__ import annotations

import json
import platform
import re
import subprocess
from pathlib import Path

from memory.memory_manager import load_memory, update_memory

_OS = platform.system()
_OSASCRIPT = "/usr/bin/osascript"
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:60]


def _run_applescript(script: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [_OSASCRIPT, "-e", script],
            capture_output=True,
            text=True,
            timeout=12,
        )
        out = (r.stdout or "").strip()
        if r.returncode == 0:
            return True, out
        return False, (r.stderr or out).strip()
    except Exception as e:
        return False, str(e)


def _mac_contacts_search(name: str) -> dict | None:
    if _OS != "Darwin":
        return None
    safe = name.replace('"', '\\"')[:80]
    script = f"""
set results to {{}}
tell application "Contacts"
  repeat with p in (every person whose name contains "{safe}")
    set em to ""
    try
      set em to value of first email of p
    end try
    set ph to ""
    try
      set ph to value of first phone of p
    end try
    set end of results to (name of p & tab & em & tab & ph)
    if (count of results) >= 3 then exit repeat
  end repeat
end tell
return results as string
"""
    ok, out = _run_applescript(script)
    if not ok or not out:
        return None
    parts = out.split("\t")
    if len(parts) >= 2 and parts[1]:
        return {"name": parts[0], "email": parts[1], "phone": parts[2] if len(parts) > 2 else ""}
    return None


def _memory_contacts() -> dict:
    mem = load_memory()
    return mem.get("contacts", {})


def lookup_contact(name: str) -> dict | None:
    """Return {name, email, phone, source} or None."""
    name = name.strip()
    if not name:
        return None

    if _EMAIL_RE.fullmatch(name):
        return {"name": name, "email": name, "phone": "", "source": "email"}

    slug = _slug(name)
    stored = _memory_contacts()
    for key, entry in stored.items():
        val = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        if slug in key or name.lower() in key.replace("_", " "):
            email = _EMAIL_RE.search(val)
            if email:
                return {"name": name, "email": email.group(0), "phone": "", "source": "memory"}

    # Partial match on display names in value
    for key, entry in stored.items():
        val = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        if name.lower() in val.lower():
            email = _EMAIL_RE.search(val)
            if email:
                return {"name": key.replace("_", " "), "email": email.group(0), "phone": "", "source": "memory"}

    mac = _mac_contacts_search(name)
    if mac and mac.get("email"):
        return {**mac, "source": "mac_contacts"}

    return None


def resolve_email(recipient: str) -> tuple[str | None, str]:
    """Resolve name → email. Returns (email, message)."""
    recipient = recipient.strip()
    if not recipient:
        return None, "NEEDS_USER: Who should I email?"
    if _EMAIL_RE.search(recipient) and "@" in recipient:
        m = _EMAIL_RE.search(recipient)
        return m.group(0) if m else recipient, "ok"

    hit = lookup_contact(recipient)
    if hit and hit.get("email"):
        return hit["email"], f"Resolved {recipient} → {hit['email']} ({hit['source']})."
    return None, (
        f"NEEDS_USER: I don't have an email for {recipient}. "
        "What is their email address? I can remember it for next time."
    )


def contact_manager(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "lookup").lower().strip()
    name = (params.get("name") or params.get("contact") or "").strip()

    print(f"[Contacts] {action} {name!r}")

    if action in ("lookup", "find", "get"):
        if not name:
            return "NEEDS_USER: Which contact name?"
        hit = lookup_contact(name)
        if not hit:
            return f"NOT_FOUND: No contact or email saved for {name}."
        parts = [f"Name: {hit.get('name', name)}"]
        if hit.get("email"):
            parts.append(f"Email: {hit['email']}")
        if hit.get("phone"):
            parts.append(f"Phone: {hit['phone']}")
        parts.append(f"Source: {hit.get('source', 'unknown')}")
        return " | ".join(parts)

    if action in ("save", "add", "remember"):
        if not name:
            return "NEEDS_USER: Contact name to save?"
        email = (params.get("email") or "").strip()
        phone = (params.get("phone") or "").strip()
        notes = (params.get("notes") or "").strip()
        if not email and not phone:
            return "NEEDS_USER: Provide at least an email or phone number."
        val_parts = []
        if email:
            val_parts.append(email)
        if phone:
            val_parts.append(f"phone:{phone}")
        if notes:
            val_parts.append(notes)
        value = " | ".join(val_parts)
        update_memory({"contacts": {_slug(name): {"value": f"{name} — {value}"}}})
        return f"Saved contact {name}."

    if action == "list":
        stored = _memory_contacts()
        if not stored:
            return "No contacts saved in ARIA memory yet."
        lines = []
        for key, entry in list(stored.items())[:20]:
            val = entry.get("value") if isinstance(entry, dict) else str(entry)
            lines.append(f"• {val}")
        return "Saved contacts:\n" + "\n".join(lines)

    return "Unknown action. Use: lookup | save | list"
