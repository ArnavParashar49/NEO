"""Compose email via native mailto: URL scheme (cross-platform)."""

from urllib.parse import quote
import webbrowser
from actions.contacts import resolve_email

def send_email(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    to = (params.get("to") or params.get("recipient") or "").strip()
    subject = (params.get("subject") or "").strip()
    body = (params.get("body") or params.get("message") or "").strip()

    if to and "@" not in to:
        resolved, msg = resolve_email(to)
        if resolved:
            to = resolved

    if not to:
        return "NEEDS_USER: I need the recipient's email address. Who should I send this to?"
    if not subject and not body:
        return "NEEDS_USER: What should the subject and body of the email say?"

    mailto = f"mailto:{to}"
    qs = []
    if subject:
        qs.append(f"subject={quote(subject)}")
    if body:
        qs.append(f"body={quote(body)}")
    if qs:
        mailto += "?" + "&".join(qs)

    if player:
        player.write_log(f"[Email] Opening draft to {to}")

    webbrowser.open(mailto)
    return f"Opened default email client with draft to {to}."
