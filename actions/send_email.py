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

    # Preferred UX: reuse the main window (the one shown on siri-bar double-click)
    # to ask ONLY for the recipient address — typed, so no STT typos — then let
    # the AI write the subject + body. Falls back to the voice flow when headless.
    ask_addr = getattr(player, "ask_email_address", None)
    if callable(ask_addr):
        addr = to if "@" in to else ""
        if not addr and to:
            try:
                from actions.contacts import resolve_email

                resolved, _msg = resolve_email(to)
                if resolved:
                    addr = resolved
            except Exception:
                pass
        who = to or addr
        typed = ask_addr(
            prompt=(
                f"Who should I email{f' — {who}' if who else ''}? "
                "Type the email address and press Enter."
            ),
            prefill=addr,
        )
        notify = getattr(player, "notify", None)
        if not typed or typed.strip().lower() in ("cancel", "stop", "no", "nevermind"):
            _pending_draft = None
            if notify:
                notify("Okay — cancelled the email.")
            return "CANCELLED: Email cancelled."
        addr = typed.strip()
        if "@" not in addr:
            if notify:
                notify("That doesn't look like an email address — cancelled.")
            return "CANCELLED: That doesn't look like an email address."
        if notify:
            notify(f"Writing your email to {addr}…")
        subject, body = _ai_compose(to or addr, subject, body)
        result = _compose_and_send(addr, subject, body, browser, player)
        if notify:
            if result.startswith("SENT"):
                notify(f"✅ Sent your email to {addr}.")
            elif result.startswith(("SEND_FAILED", "NEEDS_LOGIN")):
                notify(f"⚠️ Couldn't send — {result.split(':', 1)[-1].strip()[:90]}")
        return result

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


def _sender_name() -> str:
    """Best-effort: the user's name from long-term memory (for the sign-off)."""
    try:
        from memory.memory_manager import load_memory

        entry = (load_memory().get("identity") or {}).get("name")
        if isinstance(entry, dict):
            return (entry.get("value") or "").strip()
        return entry.strip() if isinstance(entry, str) else ""
    except Exception:
        return ""


def _ai_compose(to_label: str, subject: str, body: str) -> tuple[str, str]:
    """Always write a complete, professional email from the user's gist — proper
    greeting, context, courteous closing, and a sign-off with the user's name
    (pulled from memory). The incoming subject/body are treated as the intent,
    not the final text."""
    gist = " — ".join(p for p in (subject.strip(), body.strip()) if p) or "a brief, friendly message"
    sender = _sender_name()
    recipient = (to_label or "the recipient").strip()

    try:
        from core.llm import ask_json

        data = ask_json(
            "Write a complete, polished, PROFESSIONAL email on the user's behalf. "
            "Expand the gist into a real email — do not just repeat it.\n\n"
            f"Recipient name: {recipient}\n"
            f"What the user wants to say (gist): {gist}\n"
            f"Sender's name for the sign-off: {sender or '(unknown)'}\n\n"
            'Return ONLY JSON: {"subject": "...", "body": "..."}\n\n'
            "The body MUST include, on their own lines:\n"
            "1. A greeting — 'Dear <recipient first name>,' (or 'Hi <first name>,').\n"
            "2. 2-4 polite, clear sentences that fully express the message with context.\n"
            "3. A courteous closing line (e.g. 'Thank you for your time and consideration.').\n"
            "4. A sign-off: 'Best regards,' then the sender's name on the next line.\n\n"
            "Rules: warm and professional; correct grammar and punctuation; NEVER use "
            "bracket placeholders like [Your Name] — if the sender's name is unknown, "
            "end at 'Best regards,'. Plain text only, no markdown.",
            model="gemini-2.5-flash",
        )
        s = (data.get("subject") or subject or "Hello").strip()
        b = (data.get("body") or body).strip()
        return (s or "Hello"), (b or body)
    except Exception as e:
        print(f"[Email] AI compose failed ({e}); using original draft.")
        return (subject or "Hello"), body


def _compose_and_send(
    to: str,
    subject: str,
    body: str,
    browser: str | None,
    player,
) -> str:
    """Open a Gmail draft and send it — used after the typed-compose form,
    where the user's Send click is the confirmation."""
    global _pending_draft

    qs = f"view=cm&fs=1&to={quote(to)}"
    if subject:
        qs += f"&su={quote(subject)}"
    if body:
        qs += f"&body={quote(body)}"
    url = f"https://mail.google.com/mail/?{qs}"
    _pending_draft = {"to": to, "subject": subject, "body": body}
    _log(player, f"Gmail compose (typed form) → {to}")

    app = "Safari" if browser and browser.lower() in ("safari",) else "Google Chrome"
    open_in_user_browser(url, browser)
    activate_app(app)
    time.sleep(1.0)

    current = wait_for_url(timeout=14.0, browser=browser) or get_front_browser_url(browser)
    if is_login_url(current):
        return "NEEDS_LOGIN: Sign in to Google in Chrome, then ask me to send it again."

    # Give the compose overlay time to render, then click Send directly in the
    # DOM. (URL detection is unreliable: ?view=cm redirects to the inbox with
    # compose as an overlay, so the tab URL usually has no compose marker.)
    time.sleep(2.2)

    detail = ""
    for _ in range(6):
        detail = gmail_click_send(browser)
        if detail == "clicked":
            _pending_draft = None
            _log(player, f"sent (Send button) → {to}")
            return f"SENT: Email sent to {to}."
        if detail in ("unsupported_os", "safari_not_supported"):
            break
        time.sleep(0.7)

    # DOM click unavailable (e.g. Chrome's "Allow JavaScript from Apple Events"
    # is off) — fall back to Gmail's keyboard send shortcut.
    try:
        import pyautogui

        activate_app(app)
        time.sleep(0.4)
        pyautogui.hotkey("command", "enter")
        time.sleep(1.0)
        _pending_draft = None
        _log(player, f"sent (cmd+enter; js={detail}) → {to}")
        return f"SENT: Email sent to {to}."
    except Exception as e:
        return (
            f"SEND_FAILED: Couldn't send to {to} ({detail}; {e}). "
            "The draft is open in Chrome — click Send."
        )


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
