"""Compose and send email via Gmail in the user's real Chrome (confirm before send)."""

import time
import traceback
from urllib.parse import quote

from actions.browser_native import (
    activate_app,
    get_front_browser_url,
    gmail_click_send,
    is_gmail_compose,
    is_login_url,
    open_in_user_browser,
    wait_for_url,
)

_USE_NATIVE = True
_pending_draft: dict | None = None


def send_email(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    # speak is ignored — calling Live session from a worker thread crashes ARIA
    try:
        return _send_email_impl(parameters or {}, player)
    except Exception as e:
        traceback.print_exc()
        return (
            f"SEND_FAILED: Something went wrong ({e}). "
            "The email was probably not sent — check Gmail."
        )


def _send_email_impl(params: dict, player) -> str:
    global _pending_draft

    action = (params.get("action") or "compose").lower().strip()
    browser = (params.get("browser") or "").strip() or None

    if action in ("read", "inbox", "list"):
        from actions.browser_native import gmail_read_inbox

        count = int(params.get("count") or 5)
        query = (params.get("query") or params.get("from") or "").strip()
        return gmail_read_inbox(count=count, query=query, browser=browser)

    if action in ("search", "find"):
        from actions.browser_native import gmail_read_inbox

        query = (params.get("query") or params.get("subject") or "").strip()
        if not query:
            return "NEEDS_USER: What should I search for in Gmail?"
        return gmail_read_inbox(count=int(params.get("count") or 5), query=query, browser=browser)

    to = (params.get("to") or params.get("recipient") or "").strip()
    subject = (params.get("subject") or "").strip()
    body = (params.get("body") or params.get("message") or "").strip()

    confirm_send = _as_bool(params.get("confirm_send"), default=False)
    cancel = _as_bool(params.get("cancel"), default=False)

    if cancel:
        _pending_draft = None
        return "CANCELLED: Email send cancelled. Draft may still be open in Chrome."

    if confirm_send:
        draft = _pending_draft or {}
        to = to or draft.get("to", "")
        if not to:
            return (
                "NEEDS_USER: No draft waiting. Tell me who to email and what to say first."
            )
        return _send_confirmed(to, browser, player)

    # Resolve name → email via contacts
    if to and "@" not in to:
        from actions.contacts import resolve_email

        resolved, msg = resolve_email(to)
        if resolved:
            print(f"[Email] {msg}")
            to = resolved
        elif msg.startswith("NEEDS_USER"):
            return msg

    if not to:
        return (
            "NEEDS_USER: I need the recipient's email address. "
            "Who should I send this to?"
        )
    if not subject and not body:
        return (
            "NEEDS_USER: What should the subject and body of the email say?"
        )

    qs = f"view=cm&fs=1&to={quote(to)}"
    if subject:
        qs += f"&su={quote(subject)}"
    if body:
        qs += f"&body={quote(body)}"
    url = f"https://mail.google.com/mail/?{qs}"

    _log(player, f"Gmail compose (draft) → {to}")
    _pending_draft = {"to": to, "subject": subject, "body": body}

    if _USE_NATIVE:
        return _compose_native(url, to, subject, browser, player)

    from actions.browser_control import browser_control

    base = {"browser": browser} if browser else {}
    go = browser_control(parameters={**base, "action": "go_to", "url": url}, player=player)
    if "error" in go.lower() or "could not" in go.lower():
        return go
    return _confirmation_message(to, subject)


def _compose_native(
    url: str,
    to: str,
    subject: str,
    browser: str | None,
    player,
) -> str:
    app = "Google Chrome"
    if browser and browser.lower() in ("safari",):
        app = "Safari"

    open_in_user_browser(url, browser)
    activate_app(app)
    time.sleep(1.0)

    current = wait_for_url(timeout=14.0, browser=browser)
    if not current:
        current = get_front_browser_url(browser)

    if is_login_url(current):
        return (
            "NEEDS_LOGIN: Sign in to Google in Chrome, then ask me to compose the email again."
        )

    return _confirmation_message(to, subject)


def _confirmation_message(to: str, subject: str) -> str:
    subj_part = f' Subject: "{subject}".' if subject else ""
    return (
        f"NEEDS_CONFIRM: Draft is ready in Chrome — NOT sent yet. "
        f"To: {to}.{subj_part} "
        "Ask the user: 'Should I send it?' Only if they clearly say yes, "
        "call send_email again with confirm_send true. If they say no, call with cancel true. "
        "Never tell the user the email was sent until you see SENT: in the tool result."
    )


def _send_confirmed(to: str, browser: str | None, player) -> str:
    global _pending_draft

    app = "Safari" if browser and browser.lower() in ("safari",) else "Google Chrome"
    activate_app(app)
    time.sleep(0.5)

    current = get_front_browser_url(browser)
    if is_login_url(current):
        return "NEEDS_LOGIN: Sign in to Gmail before I can send."

    if not _compose_is_open(browser):
        return (
            "SEND_FAILED: Gmail compose is not open — nothing was sent. "
            "Ask me to compose the email again, then confirm send."
        )

    detail = _press_send_safe(browser)
    time.sleep(1.2)

    if not _compose_is_open(browser):
        _pending_draft = None
        _log(player, f"verified sent → {to}")
        return f"SENT: Email was sent to {to}."

    _log(player, f"send not verified: {detail}")
    return (
        f"SEND_FAILED: Email was NOT sent to {to} ({detail}). "
        "Compose is still open — click Send in Chrome or say try again. "
        "Do not tell the user it was sent."
    )


def _compose_is_open(browser: str | None) -> bool:
    url = get_front_browser_url(browser)
    if not url or is_login_url(url):
        return False
    u = url.lower()
    return is_gmail_compose(url) or "view=cm" in u or "compose" in u


def _press_send_safe(browser: str | None) -> str:
    """Send without screen AI (avoids crash). Chrome JS first, then keyboard."""
    result = gmail_click_send(browser)
    if result == "clicked":
        return "gmail_send_button"

    if result not in ("unsupported_os", "safari_not_supported"):
        # JS failed — try keyboard shortcut
        try:
            import pyautogui

            pyautogui.hotkey("command", "enter")
            return f"hotkey_cmd_enter (js was {result})"
        except Exception as e:
            return f"js={result}; hotkey failed: {e}"

    try:
        import pyautogui

        pyautogui.hotkey("command", "enter")
        return "hotkey_cmd_enter"
    except Exception as e:
        return f"send_failed: {e}"


def _as_bool(val, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).lower() not in ("false", "0", "no", "")


def _log(player, text: str):
    print(f"[Email] {text}")
