"""Open URLs in the user's real browser (not Playwright's separate instance)."""

from __future__ import annotations

import platform
import subprocess
import time
import webbrowser

_OS = platform.system()

LOGIN_URL_MARKERS = (
    "accounts.google.com",
    "servicelogin",
    "signin",
    "identifier",
    "challenge/pwd",
    "oauth",
)


def is_login_url(url: str) -> bool:
    u = (url or "").lower()
    return any(m in u for m in LOGIN_URL_MARKERS)


def is_gmail_compose(url: str) -> bool:
    u = (url or "").lower()
    return "mail.google.com" in u and ("view=cm" in u or "compose" in u)


def open_in_user_browser(url: str, browser: str | None = None) -> str:
    """Open URL in the user's normal browser and bring it to the front."""
    return navigate_user_browser(url, browser)


def _esc_url(url: str) -> str:
    return url.replace("\\", "\\\\").replace('"', '\\"')


def _run_osascript(script: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=12,
        )
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or r.stdout or f"exit {r.returncode}").strip()
        return False, err
    except Exception as e:
        return False, str(e)


def navigate_user_browser(url: str, browser: str | None = None) -> str:
    """Open a URL in the user's browser — avoids Playwright about:blank tabs."""
    url = (url or "").strip()
    if not url:
        return "NEEDS_USER: Which URL should I open?"

    name = (browser or "").strip().lower()

    if _OS == "Darwin":
        if name in ("safari",):
            script = (
                'tell application "Safari"\n'
                "  activate\n"
                "  if (count of windows) = 0 then make new document\n"
                f'  set URL of current tab of front window to "{_esc_url(url)}"\n'
                "end tell"
            )
            ok, err = _run_osascript(script)
            if ok:
                return f"Opened in Safari: {url}"
            print(f"[BrowserNative] Safari AppleScript failed: {err}")

        app = "Google Chrome"
        if name in ("firefox",):
            app = "Firefox"
        elif name in ("edge", "microsoft edge"):
            app = "Microsoft Edge"
        elif name in ("brave", "brave browser"):
            app = "Brave Browser"

        if app == "Google Chrome":
            safe = _esc_url(url)
            script = (
                'tell application "Google Chrome"\n'
                "  activate\n"
                "  if (count of windows) = 0 then\n"
                "    make new window\n"
                "  end if\n"
                "  tell front window\n"
                f'    make new tab with properties {{URL:"{safe}"}}\n'
                "  end tell\n"
                "end tell"
            )
            ok, err = _run_osascript(script)
            if ok:
                time.sleep(0.2)
                return f"Opened: {url}"
            print(f"[BrowserNative] Chrome AppleScript failed: {err}")

        # Fallback: macOS open command (reliable even when AppleScript is blocked)
        try:
            if name and app:
                r = subprocess.run(
                    ["open", "-a", app, url],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            else:
                r = subprocess.run(
                    ["open", url],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            if r.returncode == 0:
                time.sleep(0.3)
                if app and name:
                    activate_app(app)
                return f"Opened: {url}"
            err = (r.stderr or r.stdout or f"exit {r.returncode}").strip()
            return f"FAILED: Could not open {url} — {err}"
        except Exception as e:
            return f"FAILED: Could not open {url} — {e}"

    if _OS == "Windows":
        if name in ("edge", "msedge", "microsoft edge"):
            subprocess.run(["start", "msedge", url], shell=True, check=False)
        elif name == "firefox":
            subprocess.run(["start", "firefox", url], shell=True, check=False)
        else:
            subprocess.run(["start", "chrome", url], shell=True, check=False)
        return f"Opened: {url}"

    webbrowser.open(url)
    return f"Opened: {url}"


def activate_app(app_name: str) -> None:
    if _OS == "Darwin":
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            check=False,
        )
    elif _OS == "Windows":
        try:
            import pygetwindow as gw  # optional
            wins = gw.getWindowsWithTitle(app_name)
            if wins:
                wins[0].activate()
        except Exception:
            pass


def get_front_browser_url(browser: str | None = None) -> str:
    """Best-effort URL of the active tab (macOS Chrome)."""
    if _OS != "Darwin":
        return ""

    name = (browser or "chrome").strip().lower()
    if name in ("safari",):
        script = """
        tell application "Safari"
          if (count of windows) = 0 then return ""
          return URL of current tab of front window
        end tell
        """
    else:
        script = """
        tell application "Google Chrome"
          if (count of windows) = 0 then return ""
          return URL of active tab of front window
        end tell
        """
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def wait_for_url(
    timeout: float = 14.0,
    interval: float = 0.6,
    browser: str | None = None,
) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = get_front_browser_url(browser)
        if last and last not in ("about:blank", ""):
            return last
        time.sleep(interval)
    return last


_GMAIL_SEND_JS = (
    "(function(){var b=[].slice.call(document.querySelectorAll('[role=button]'));"
    "var s=b.find(function(el){var l=(el.getAttribute('aria-label')||"
    "el.getAttribute('data-tooltip')||'').toLowerCase();"
    "return l.indexOf('send')===0&&l.indexOf('feedback')<0;});"
    "if(s){s.click();return 'clicked';}return 'not_found';})();"
)


def gmail_click_send(browser: str | None = None) -> str:
    """
    Click Gmail's Send button in the front browser tab (macOS).
    Returns: clicked | not_found | no_window | error:...
    """
    if _OS != "Darwin":
        return "unsupported_os"

    name = (browser or "chrome").strip().lower()
    if name in ("safari",):
        return "safari_not_supported"

    js_escaped = _GMAIL_SEND_JS.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Google Chrome"
      activate
      if (count of windows) is 0 then return "no_window"
      try
        set r to execute active tab of front window javascript "{js_escaped}"
        return r
      on error errMsg
        return "error:" & errMsg
      end try
    end tell
    '''
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=8,
        )
        out = (r.stdout or "").strip()
        if out:
            return out
        err = (r.stderr or "").strip()
        return f"error:{err}" if err else "error:empty"
    except Exception as e:
        return f"error:{e}"


_GMAIL_LIST_JS = (
    "(function(){"
    "var rows=[].slice.call(document.querySelectorAll('tr.zA, tr[data-legacy-thread-id]'));"
    "if(!rows.length) rows=[].slice.call(document.querySelectorAll('[role=row]'));"
    "return rows.slice(0,10).map(function(r,i){"
    "var s=r.querySelector('.bog,.bA4 span,[email]');"
    "var f=r.querySelector('.yW,.yX span,[name]');"
    "var subj=s?(s.textContent||'').trim():'';"
    "var from=f?(f.textContent||'').trim():'';"
    "if(!subj&&!from) return '';"
    "return (i+1)+'. '+from+' — '+subj;"
    "}).filter(Boolean).join('\\n');"
    "})();"
)


def _chrome_run_js(js: str, browser: str | None = None) -> str:
    if _OS != "Darwin":
        return "unsupported_os"
    name = (browser or "chrome").strip().lower()
    if name in ("safari",):
        return "safari_not_supported"
    js_escaped = js.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Google Chrome"
      activate
      if (count of windows) is 0 then return "no_window"
      try
        set r to execute active tab of front window javascript "{js_escaped}"
        return r
      on error errMsg
        return "error:" & errMsg
      end try
    end tell
    '''
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=12,
        )
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"error:{e}"


def gmail_read_inbox(count: int = 5, query: str = "", browser: str | None = None) -> str:
    """Open Gmail and scrape visible inbox/search results."""
    from urllib.parse import quote

    if query:
        url = f"https://mail.google.com/mail/u/0/#search/{quote(query)}"
    else:
        url = f"https://mail.google.com/mail/u/0/#inbox"

    open_in_user_browser(url, browser)
    activate_app("Google Chrome")
    time.sleep(2.5)

    current = wait_for_url(timeout=12.0, browser=browser)
    if is_login_url(current or get_front_browser_url(browser)):
        return "NEEDS_LOGIN: Sign in to Gmail in Chrome, then ask again."

    raw = _chrome_run_js(_GMAIL_LIST_JS, browser)
    if raw.startswith("error:") or raw in ("no_window", "unsupported_os"):
        return (
            f"FAILED: Could not read Gmail ({raw}). "
            "Open inbox in Chrome and try again."
        )
    if not raw:
        return "No emails found in the current Gmail view."
    lines = raw.split("\n")[: max(1, min(count, 10))]
    header = f"Gmail {'search' if query else 'inbox'} ({len(lines)} shown):"
    return header + "\n" + "\n".join(lines)
