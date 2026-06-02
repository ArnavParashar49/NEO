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


def navigate_active_tab(url: str, browser: str | None = None) -> str:
    """Open URL in the current tab (no extra about:blank / new-tab flash)."""
    url = (url or "").strip()
    if not url:
        return "NEEDS_USER: Which URL should I open?"

    if _OS == "Darwin":
        safe = _esc_url(url)
        script = (
            'tell application "Google Chrome"\n'
            "  activate\n"
            "  if (count of windows) = 0 then\n"
            "    make new window\n"
            "  end if\n"
            f'  set URL of active tab of front window to "{safe}"\n'
            "end tell\n"
        )
        ok, err = _run_osascript(script)
        if ok:
            time.sleep(0.3)
            return f"Opened: {url}"
        print(f"[BrowserNative] active tab navigate failed: {err}")

    return navigate_user_browser(url, browser)


_GOOGLE_FIRST_RESULT_JS = (
    "(function(){"
    "var links=[].slice.call(document.querySelectorAll("
    "'#search a[href^=\"http\"], div#rso a[href^=\"http\"]'));"
    "var skip=['google.com','gstatic.com','webcache','youtube.com/results'];"
    "for(var i=0;i<links.length;i++){"
    "var h=links[i].href||'';"
    "if(!h||skip.some(function(s){return h.indexOf(s)>=0;})) continue;"
    "links[i].click(); return 'opened:'+h;}"
    "return 'none';})();"
)

_DOWNLOAD_CLICK_JS = (
    "(function(){"
    "function go(el){if(!el)return false;"
    "try{el.scrollIntoView({block:'center',inline:'center'});"
    "el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));"
    "if(typeof el.click==='function')el.click();return true;}catch(e){return false;}}"
    "var sels=['a[href*=\".dmg\"]','a[href*=\".pkg\"]','a[href*=\".exe\"]','a[href*=\".msi\"]',"
    "'a[download]','a[href*=\"download\"]','button[data-testid*=\"download\"]'];"
    "for(var i=0;i<sels.length;i++){var el=document.querySelector(sels[i]);"
    "if(go(el))return 'clicked:'+sels[i];}"
    "var nodes=[].slice.call(document.querySelectorAll('a,button,[role=button]'));"
    "for(var j=0;j<nodes.length;j++){"
    "var t=(nodes[j].textContent||nodes[j].getAttribute('aria-label')||'').trim();"
    "if(/^download\\b/i.test(t)||/\\bdownload\\s+(for\\s+)?(mac|macos|windows)/i.test(t)){"
    "if(go(nodes[j]))return 'clicked:text:'+t.slice(0,48);}}"
    "return 'none';})();"
)


def click_download_on_active_tab(browser: str | None = None) -> str:
    """Click Download on the current Chrome tab (macOS)."""
    return _chrome_run_js(_DOWNLOAD_CLICK_JS, browser)


def open_google_first_result(search_query: str, browser: str | None = None) -> str:
    """Google search in the active tab, then click the first real result."""
    from urllib.parse import quote_plus

    q = (search_query or "").strip()
    if not q:
        return "none"
    url = "https://www.google.com/search?q=" + quote_plus(q)
    navigate_active_tab(url, browser)
    activate_app("Google Chrome")
    time.sleep(2.8)
    return _chrome_run_js(_GOOGLE_FIRST_RESULT_JS, browser)


def native_app_download_from_google(app_name: str, browser: str | None = None) -> str:
    """
    User's Chrome: Google → 'download {app}' → official link → Download click.
    """
    import re
    from urllib.parse import quote_plus

    app = re.sub(
        r"\b(download|install|get|from\s+google|the\s+app|app|please)\b",
        "",
        (app_name or ""),
        flags=re.I,
    ).strip()
    if not app:
        return "FAILED: Which app should I download?"

    try:
        from actions.browser_control import _OFFICIAL_APP_URLS
    except ImportError:
        _OFFICIAL_APP_URLS = {}

    official = _OFFICIAL_APP_URLS.get(app.lower())
    if not official:
        for name, url in _OFFICIAL_APP_URLS.items():
            if app.lower().replace(" ", "") == name.replace(" ", ""):
                official = url
                break

    search_q = f"download {app}"
    google_url = "https://www.google.com/search?q=" + quote_plus(search_q)
    print(f"[Download] Native Chrome: Google '{search_q}'")

    if _OS == "Darwin":
        navigate_active_tab(google_url, browser)
        activate_app("Google Chrome")
        time.sleep(2.8)
        pick = _chrome_run_js(_GOOGLE_FIRST_RESULT_JS, browser)
        print(f"[Download] Google result: {pick}")
        if pick == "none" or pick.startswith("error"):
            if official:
                navigate_active_tab(official, browser)
                time.sleep(2.5)
            else:
                return (
                    f"Opened Google search for '{search_q}'. "
                    "Click the official site link, then Download."
                )
        else:
            time.sleep(2.5)

        click = click_download_on_active_tab(browser)
        print(f"[Download] Click result: {click}")
        page = get_front_browser_url(browser) or official or ""
        if click.startswith("clicked"):
            return (
                f"Searched for '{search_q}', opened the site, and clicked Download. "
                f"Check your Downloads folder. ({click})"
            )
        return (
            f"Opened {page or 'the download page'} but could not auto-click Download ({click}). "
            "Please click the Download button once — file goes to Downloads."
        )

    return f"FAILED: Native download flow is macOS-only for now. Open: {official or search_q}"


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
