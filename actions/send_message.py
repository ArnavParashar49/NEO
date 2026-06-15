"""
Send Messages via native URL schemes (cross-platform).
Supported: WhatsApp, SMS (iMessage), Telegram
"""

import webbrowser
from urllib.parse import quote

def send_message(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    platform_name = params.get("platform", "").lower().strip()
    receiver = params.get("receiver", "").strip()
    message = params.get("message", "").strip()

    if not platform_name:
        return "NEEDS_USER: Which app should I use to send the message? (WhatsApp, Telegram, or SMS)"

    if not receiver:
        return "NEEDS_USER: Who should I send the message to?"

    if not message:
        return "NEEDS_USER: What should the message say?"

    # Resolve contact if needed
    if not receiver.replace("+", "").isdigit():
        from actions.contacts import resolve_phone
        resolved, msg = resolve_phone(receiver)
        if resolved:
            receiver = resolved
        else:
            return msg

    # Format numbers (remove non-digits, keep leading + if present)
    clean_num = "".join(c for c in receiver if c.isdigit() or c == "+")

    if platform_name in ("whatsapp", "wa"):
        # Remove leading + for WhatsApp
        wa_num = clean_num.lstrip("+")
        uri = f"whatsapp://send?phone={wa_num}&text={quote(message)}"
        webbrowser.open(uri)
        return f"Opened WhatsApp to send message to {receiver}."

    elif platform_name in ("telegram", "tg"):
        uri = f"tg://msg?to={clean_num}&text={quote(message)}"
        webbrowser.open(uri)
        return f"Opened Telegram to send message to {receiver}."

    elif platform_name in ("sms", "imessage", "messages", "text"):
        # Different OS might need different formats, but sms:// is standard
        uri = f"sms:{clean_num}?&body={quote(message)}"
        webbrowser.open(uri)
        return f"Opened default SMS app to send message to {receiver}."

    else:
        return f"Unsupported platform: {platform_name}. Use WhatsApp, Telegram, or SMS."