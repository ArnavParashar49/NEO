
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)
_OS = platform.system()   # "Windows" | "Darwin" | "Linux"

def _normalize_url(url: str) -> str:
    """
    Bare words like "instagram" → "https://instagram.com"
    Domains like "instagram.com" → "https://instagram.com"
    Full URLs pass through unchanged.
    """
    url = (url or "").strip()
    if not url:
        return ""

    _shortcuts = {
        "google": "https://www.google.com",
        "youtube": "https://www.youtube.com",
        "youtube.com": "https://www.youtube.com",
        "www.youtube.com": "https://www.youtube.com",
        "gmail": "https://mail.google.com",
        "maps": "https://maps.google.com",
        "drive": "https://drive.google.com",
        "amazon": "https://www.amazon.in",
        "flipkart": "https://www.flipkart.com",
        "reddit": "https://www.reddit.com",
        "twitter": "https://x.com",
        "x": "https://x.com",
        "facebook": "https://www.facebook.com",
        "instagram": "https://www.instagram.com",
        "linkedin": "https://www.linkedin.com",
        "github": "https://github.com",
    }
    key = url.lower().strip().rstrip("/")
    if key in _shortcuts:
        return _shortcuts[key]

    if "://" in url:
        return url
    # No dot at all → assume .com  (e.g. "instagram" → "instagram.com")
    if "." not in url:
        url = url + ".com"
    return "https://" + url


# Simple navigation in the user's real browser (avoids Playwright about:blank tabs).
_USE_NATIVE_NAV = True

# Direct official download pages (skip bad Google results).
_OFFICIAL_APP_URLS: dict[str, str] = {
    "spotify": "https://www.spotify.com/download/",
    "google chrome": "https://www.google.com/chrome/",
    "chrome": "https://www.google.com/chrome/",
    "firefox": "https://www.mozilla.org/firefox/download/",
    "vlc": "https://www.videolan.org/vlc/",
    "discord": "https://discord.com/download",
    "zoom": "https://zoom.us/download",
    "slack": "https://slack.com/downloads/mac",
    "vscode": "https://code.visualstudio.com/download",
    "visual studio code": "https://code.visualstudio.com/download",
    "telegram": "https://desktop.telegram.org/",
    "whatsapp": "https://www.whatsapp.com/download",
    "obs": "https://obsproject.com/download",
    "steam": "https://store.steampowered.com/about/",
    "epic games": "https://store.epicgames.com/download",
    "notion": "https://www.notion.com/desktop",
    "cursor": "https://cursor.com/download",
}

_BAD_RESULT_DOMAINS = (
    "google.com", "gstatic.com", "webcache", "accounts.google",
    "support.google", "policies.google", "youtube.com", "reddit.com",
    "wikipedia.org", "quora.com", "stackoverflow.com", "softonic.",
    "filehorse.", "majorgeeks.", "download.cnet.", "pinterest.",
)


def _native_navigate(url: str, browser: str | None = None) -> str:
    from actions.browser_native import navigate_user_browser

    normalized = _normalize_url(url)
    if not normalized:
        return "NEEDS_USER: Which URL should I open?"
    return navigate_user_browser(normalized, browser)


def _user_agent() -> str:
    if _OS == "Windows":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    if _OS == "Darwin":
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    return (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def _real_profile_dir(browser: str) -> str:
    home  = Path.home()
    local = os.environ.get("LOCALAPPDATA", "")
    roam  = os.environ.get("APPDATA", "")

    candidates: list[Path] = []

    if _OS == "Windows":
        m = {
            "chrome":   [Path(local) / "Google"          / "Chrome"          / "User Data"],
            "edge":     [Path(local) / "Microsoft"        / "Edge"            / "User Data"],
            "brave":    [Path(local) / "BraveSoftware"    / "Brave-Browser"   / "User Data"],
            "vivaldi":  [Path(local) / "Vivaldi"          / "User Data"],
            "opera":    [Path(roam)  / "Opera Software"   / "Opera Stable",
                         Path(local) / "Opera Software"   / "Opera Stable"],
            "operagx":  [Path(roam)  / "Opera Software"   / "Opera GX Stable",
                         Path(local) / "Opera Software"   / "Opera GX Stable"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Darwin":
        lib = home / "Library" / "Application Support"
        m = {
            "chrome":   [lib / "Google"             / "Chrome"],
            "edge":     [lib / "Microsoft Edge"],
            "brave":    [lib / "BraveSoftware"       / "Brave-Browser"],
            "vivaldi":  [lib / "Vivaldi"],
            "opera":    [lib / "com.operasoftware.Opera"],
            "operagx":  [lib / "com.operasoftware.OperaGX"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Linux":
        cfg = home / ".config"
        m = {
            "chrome":   [cfg / "google-chrome", cfg / "chromium"],
            "edge":     [cfg / "microsoft-edge"],
            "brave":    [cfg / "BraveSoftware" / "Brave-Browser"],
            "vivaldi":  [cfg / "vivaldi"],
            "opera":    [cfg / "opera"],
            "operagx":  [cfg / "opera-gx"],
        }
        candidates = m.get(browser, [])

    for p in candidates:
        if p.exists():
            print(f"[Browser] ✅ Real profile found for {browser}: {p}")
            return str(p)

    fallback = home / ".aria_profiles" / browser
    fallback.mkdir(parents=True, exist_ok=True)
    print(f"[Browser] ⚠️  Real profile not found for {browser}, using: {fallback}")
    return str(fallback)

def _firefox_profile_dir() -> Optional[str]:
    home = Path.home()

    if _OS == "Windows":
        base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    elif _OS == "Darwin":
        base = home / "Library" / "Application Support" / "Firefox"
    else:
        base = home / ".mozilla" / "firefox"

    ini = base / "profiles.ini"
    if not ini.exists():
        return None

    current: dict[str, str] = {}
    default_path: Optional[str] = None

    for line in ini.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("["):
            p = current.get("Path", "")
            if p and current.get("Default") == "1":
                is_rel = current.get("IsRelative", "1") == "1"
                default_path = str(base / p) if is_rel else p
            current = {}
        elif "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()

    p = current.get("Path", "")
    if p and current.get("Default") == "1":
        is_rel = current.get("IsRelative", "1") == "1"
        default_path = str(base / p) if is_rel else p

    if default_path and Path(default_path).exists():
        print(f"[Browser] Firefox real profile: {default_path}")
        return default_path
    return None

def _find_opera_windows() -> Optional[str]:
    local  = os.environ.get("LOCALAPPDATA", "")
    prog   = os.environ.get("PROGRAMFILES", "")
    prog86 = os.environ.get("PROGRAMFILES(X86)", "")

    candidates = [
        Path(local)  / "Programs" / "Opera"    / "opera.exe",
        Path(local)  / "Programs" / "Opera GX" / "opera.exe",
        Path(prog)   / "Opera"    / "opera.exe",
        Path(prog86) / "Opera"    / "opera.exe",
    ]
    for p in candidates:
        if p.exists():
            print(f"[Browser] Opera found at: {p}")
            return str(p)

    try:
        import winreg
        keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\opera\shell\open\command",
        ]
        for key_path in keys:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] Opera found via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass

    return shutil.which("opera") or None

def _find_exe_windows(prog_name: str) -> Optional[str]:
    try:
        import winreg
        paths_to_try = [
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{prog_name}.exe",
            rf"SOFTWARE\Clients\StartMenuInternet\{prog_name}\shell\open\command",
        ]
        for key_path in paths_to_try:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None

_BROWSER_SPECS: dict[str, dict] = {
    "Windows": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": []},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox.exe"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera.exe"],  "special": "opera_windows"},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": [],             "special": "opera_windows"},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave.exe"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi.exe"]},
        "safari":   None,
    },
    "Darwin": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": ["microsoft-edge"]},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi"]},
        "safari":   {"engine": "webkit",   "channel": None,      "bins": []},
    },
    "Linux": {
        "chrome":   {"engine": "chromium", "channel": None,
                     "bins": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]},
        "edge":     {"engine": "chromium", "channel": None,
                     "bins": ["microsoft-edge", "microsoft-edge-stable"]},
        "firefox":  {"engine": "firefox",  "channel": None, "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "operagx":  {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "brave":    {"engine": "chromium", "channel": None, "bins": ["brave-browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None, "bins": ["vivaldi-stable", "vivaldi"]},
        "safari":   None,
    },
}

_ALIASES: dict[str, str] = {
    "google chrome":   "chrome",
    "google-chrome":   "chrome",
    "microsoft edge":  "edge",
    "ms edge":         "edge",
    "msedge":          "edge",
    "mozilla firefox": "firefox",
    "opera gx":        "operagx",
    "opera_gx":        "operagx",
}


def _resolve_browser(name: str) -> dict | None:
    name   = _ALIASES.get(name.lower().strip(), name.lower().strip())
    os_map = _BROWSER_SPECS.get(_OS, {})
    spec   = os_map.get(name)
    if spec is None:
        return None

    engine  = spec["engine"]
    channel = spec.get("channel")
    bins    = spec.get("bins", [])
    exe     = None

    if spec.get("special") == "opera_windows":
        exe = _find_opera_windows()
        if not exe:
            print(f"[Browser] ⚠️  Opera executable not found on Windows.")
        return {"engine": engine, "exe": exe, "channel": channel}

    for b in bins:
        found = shutil.which(b)
        if found:
            exe = found
            break

    if not exe and _OS == "Darwin":
        app_names = {
            "chrome":  ["Google Chrome.app"],
            "edge":    ["Microsoft Edge.app"],
            "firefox": ["Firefox.app"],
            "opera":   ["Opera.app", "Opera GX.app"],
            "brave":   ["Brave Browser.app"],
            "vivaldi": ["Vivaldi.app"],
        }
        for app in app_names.get(name, []):
            app_dir = Path("/Applications") / app / "Contents" / "MacOS"
            if app_dir.exists():
                found_bins = list(app_dir.iterdir())
                if found_bins:
                    exe = str(found_bins[0])
                    break

    if not exe and _OS == "Windows" and not channel:
        exe = _find_exe_windows(name)

    return {"engine": engine, "exe": exe, "channel": channel}


def _detect_default_browser() -> str:
    try:
        if _OS == "Windows":
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations"
                r"\UrlAssociations\http\UserChoice",
            )
            prog_id = winreg.QueryValueEx(k, "ProgId")[0].lower()
            winreg.CloseKey(k)
            for kw in ("edge", "firefox", "opera", "brave", "vivaldi", "chrome"):
                if kw in prog_id:
                    return kw
        elif _OS == "Darwin":
            out = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "safari", "chrome", "edge"):
                if kw in out:
                    return kw
        elif _OS == "Linux":
            out = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "chrome", "edge"):
                if kw in out:
                    return kw
    except Exception:
        pass
    return "chrome"


class _BrowserSession:
    """
    Bir tarayıcı örneği için tam oturum.
    Tüm tarayıcılar launch_persistent_context ile gerçek profil üzerinde açılır.
    """

    def __init__(self, browser_name: str):
        self.browser_name = browser_name
        self._spec        = _resolve_browser(browser_name)

        self._loop:    asyncio.AbstractEventLoop | None = None
        self._thread:  threading.Thread | None          = None
        self._ready    = threading.Event()

        self._pw:      Playwright     | None = None
        self._context: BrowserContext | None = None
        self._page:    Page           | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"BrowserThread-{self.browser_name}",
        )
        self._thread.start()
        self._ready.wait(timeout=20)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_init())
        self._ready.set()
        self._loop.run_forever()

    async def _async_init(self):
        self._pw = await async_playwright().start()

    def run(self, coro, timeout: int = 60) -> str:
        if not self._loop:
            raise RuntimeError(f"Session for '{self.browser_name}' not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self):
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_close(), self._loop).result(10)

    async def _async_close(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = self._page = None

    async def _launch(self):
        """
        Tarayıcıyı gerçek kullanıcı profiliyle başlatır.
        Context zaten açıksa hiçbir şey yapmaz.
        """
        if self._context is not None:
            return

        if self._spec is None:
            raise RuntimeError(
                f"'{self.browser_name}' bu platformda ({_OS}) desteklenmiyor."
            )

        engine_name = self._spec["engine"]
        exe         = self._spec["exe"]
        channel     = self._spec["channel"]
        engine_obj  = getattr(self._pw, engine_name)

        if engine_name == "firefox":
            profile = _firefox_profile_dir() or str(
                Path.home() / ".aria_profiles" / "firefox"
            )
            kwargs: dict = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
            }
            if exe:
                kwargs["executable_path"] = exe
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            except Exception as e:
                print(f"[Browser] Firefox real profile failed ({e}), using ARIA profile")
                aria_profile = str(Path.home() / ".aria_profiles" / "firefox_aria")
                Path(aria_profile).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(aria_profile, **kwargs)

            await asyncio.sleep(0.5)
            self._page = await self._pick_startup_page()
            print(f"[Browser] ✅ Firefox launched")
            return

        if engine_name == "webkit":
            safari_profile = str(Path.home() / ".aria_profiles" / "safari")
            Path(safari_profile).mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
            }
            self._context = await engine_obj.launch_persistent_context(safari_profile, **kwargs)
            await asyncio.sleep(0.5)
            self._page = await self._pick_startup_page()
            print(f"[Browser] ✅ Safari launched")
            return

        real_profile = _real_profile_dir(self.browser_name)
        aria_profile = str(Path.home() / ".aria_profiles" / self.browser_name)
        Path(aria_profile).mkdir(parents=True, exist_ok=True)
        downloads_path = str(Path.home() / "Downloads")

        kwargs = {
            "headless":    False,
            "slow_mo":     0,
            "viewport":    None,
            "no_viewport": True,
            "accept_downloads": True,
            "downloads_path": downloads_path,
            "ignore_default_args": ["--no-sandbox", "--disable-dev-shm-usage"],
            "args": [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-default-apps",
                "--no-default-browser-check",
            ],
        }

        if exe:
            kwargs["executable_path"] = exe
        elif channel:
            kwargs["channel"] = channel

        label = (
            f"{self.browser_name}"
            + (f"/{channel}" if channel else "")
            + (f" @ {exe}" if exe else "")
        )

        # ARIA profile first — user's Chrome is often already open (profile lock).
        for profile, tag in ((aria_profile, "ARIA"), (real_profile, "real")):
            if profile == aria_profile and profile == real_profile:
                continue
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
                await asyncio.sleep(0.5)
                self._page = await self._pick_startup_page()
                self._bring_browser_front()
                print(f"[Browser] ✅ Launched [{label}] {tag} profile → Downloads: {downloads_path}")
                return
            except Exception as e:
                print(f"[Browser] ⚠️  {tag} profile failed for {label}: {e}")

        raise RuntimeError(f"Could not launch {self.browser_name} for automation.")

    async def _pick_startup_page(self) -> Page:
        """Reuse Playwright's initial tab instead of opening a second about:blank tab."""
        pages = self._context.pages if self._context else []
        if pages:
            return pages[0]
        return await self._context.new_page()

    def _bring_browser_front(self):
        if _OS != "Darwin":
            return
        app_map = {
            "chrome": "Google Chrome",
            "edge": "Microsoft Edge",
            "firefox": "Firefox",
            "brave": "Brave Browser",
            "vivaldi": "Vivaldi",
            "opera": "Opera",
            "operagx": "Opera",
        }
        app = app_map.get(self.browser_name, "Google Chrome")
        try:
            subprocess.run(
                ["osascript", "-e", f'tell application "{app}" to activate'],
                check=False,
                timeout=3,
            )
        except Exception:
            pass

    async def _get_page(self) -> Page:
        await self._launch()
        # If somehow page got closed, open a fresh one
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
            await asyncio.sleep(0.2)
        return self._page

    async def go_to(self, url: str) -> str:

        url = _normalize_url(url)
        if not url:
            return "NEEDS_USER: Which URL should I open?"

        if _USE_NATIVE_NAV:
            return _native_navigate(url, self.browser_name)

        page     = await self._get_page()
        prev_url = page.url

        async def _do_goto(p: Page) -> str:
            """Attempt navigation and return the resulting URL (may still be blank)."""
            try:
                await p.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(0.3)
            except PlaywrightTimeout:
                pass   # page may have partially loaded — check URL below
            except Exception as e:
                print(f"[Browser] goto exception (non-fatal): {e}")
            return p.url

        result_url = await _do_goto(page)

        if result_url in ("about:blank", "", None, prev_url) and prev_url in ("about:blank", "", None):
            print(f"[Browser] Still blank after goto — retrying on new tab: {url}")
            try:
                new_page   = await self._context.new_page()
                self._page = new_page
                result_url = await _do_goto(new_page)
            except Exception as e:
                print(f"[Browser] New-tab retry failed: {e}")

        self._bring_browser_front()

        if result_url and result_url not in ("about:blank", "", None):
            return f"Opened: {result_url}"

        # Playwright stuck on blank — fall back to the user's real browser.
        if _USE_NATIVE_NAV:
            print(f"[Browser] Playwright blank — native fallback: {url}")
            return _native_navigate(url, self.browser_name)
        return f"Could not open: {url}"

    async def search(self, query: str, engine: str = "google") -> str:
        _engines = {
            "google":     "https://www.google.com/search?q=",
            "bing":       "https://www.bing.com/search?q=",
            "duckduckgo": "https://duckduckgo.com/?q=",
            "yandex":     "https://yandex.com/search/?text=",
        }
        base = _engines.get(engine.lower(), _engines["google"])
        target = base + query.replace(" ", "+")
        if _USE_NATIVE_NAV:
            return _native_navigate(target, self.browser_name)
        return await self.go_to(target)

    async def click(self, selector: str = None, text: str = None) -> str:
        page = await self._get_page()
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8_000)
                return f"Clicked text: '{text}'"
            if selector:
                await page.click(selector, timeout=8_000)
                return f"Clicked selector: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            return "Element not found (timeout)."
        except Exception as e:
            return f"Click error: {e}"

    async def type_text(self, selector: str = None, text: str = "",
                        clear_first: bool = True) -> str:
        page = await self._get_page()
        try:
            el = page.locator(selector).first if selector else page.locator(":focus")
            if clear_first:
                await el.clear()
            await el.type(text, delay=50)
            return "Text typed."
        except Exception as e:
            return f"Type error: {e}"

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        page = await self._get_page()
        try:
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def press(self, key: str) -> str:
        page = await self._get_page()
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def get_text(self) -> str:
        page = await self._get_page()
        try:
            text = await page.inner_text("body")
            return text[:4_000]
        except Exception as e:
            return f"Could not get page text: {e}"

    async def get_url(self) -> str:
        page = await self._get_page()
        return page.url

    async def fill_form(self, fields: dict) -> str:
        page    = await self._get_page()
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await el.clear()
                await el.type(str(value), delay=40)
                results.append(f"✓ {selector}")
            except Exception as e:
                results.append(f"✗ {selector}: {e}")
        return "Form filled: " + ", ".join(results)

    async def smart_click(self, description: str) -> str:
        page = await self._get_page()
        for role in ("button", "link", "searchbox", "textbox", "menuitem", "tab"):
            try:
                loc = page.get_by_role(role, name=description)
                if await loc.count() > 0:
                    await loc.first.click(timeout=5_000)
                    return f"Clicked ({role}): '{description}'"
            except Exception:
                pass
        for attempt in (
            lambda: page.get_by_text(description, exact=False).first.click(timeout=5_000),
            lambda: page.get_by_placeholder(description, exact=False).first.click(timeout=5_000),
            lambda: page.locator(
                f'[alt*="{description}" i],[title*="{description}" i],'
                f'[aria-label*="{description}" i]'
            ).first.click(timeout=5_000),
        ):
            try:
                await attempt()
                return f"Clicked: '{description}'"
            except Exception:
                pass
        return f"Could not find element: '{description}'"

    async def smart_type(self, description: str, text: str) -> str:
        page = await self._get_page()
        candidates = [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox", name=description)),
            ("searchbox",   page.get_by_role("searchbox")),
            ("combobox",    page.get_by_role("combobox", name=description)),
        ]
        for method, loc in candidates:
            try:
                el = loc.first
                if await el.count() == 0:
                    continue
                await el.clear()
                await el.type(text, delay=50)
                return f"Typed into ({method}): '{description}'"
            except Exception:
                continue
        return f"Could not find input: '{description}'"

    async def new_tab(self, url: str = "") -> str:
        if url and _USE_NATIVE_NAV:
            return _native_navigate(url, self.browser_name)
        page = await self._get_page()
        ctx  = page.context
        new  = await ctx.new_page()
        self._page = new
        if url:
            return await self.go_to(url)
        return "New tab opened."

    async def close_tab(self) -> str:
        page = self._page
        if page and not page.is_closed():
            ctx   = page.context
            await page.close()
            pages = ctx.pages
            self._page = pages[-1] if pages else None
            return "Tab closed."
        return "No active tab to close."

    async def screenshot(self, path: str = None) -> str:
        page = await self._get_page()
        try:
            save_path = path or str(Path.home() / "Desktop" / "aria_screenshot.png")
            await page.screenshot(path=save_path, full_page=False)
            return f"Screenshot saved: {save_path}"
        except Exception as e:
            return f"Screenshot error: {e}"

    async def back(self) -> str:
        page = await self._get_page()
        try:
            await page.go_back(timeout=10_000)
            return f"Navigated back: {page.url}"
        except Exception as e:
            return f"Back error: {e}"

    async def forward(self) -> str:
        page = await self._get_page()
        try:
            await page.go_forward(timeout=10_000)
            return f"Navigated forward: {page.url}"
        except Exception as e:
            return f"Forward error: {e}"

    async def reload(self) -> str:
        page = await self._get_page()
        try:
            await page.reload(timeout=15_000)
            return f"Page reloaded: {page.url}"
        except Exception as e:
            return f"Reload error: {e}"

    async def playwright_goto(self, url: str) -> str:
        """Navigate with Playwright (automation), not native open."""
        url = _normalize_url(url)
        if not url:
            return "NEEDS_USER: Which URL should I open?"
        page = await self._get_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(0.6)
        except PlaywrightTimeout:
            pass
        except Exception as e:
            return f"Navigation error: {e}"
        self._bring_browser_front()
        return page.url or url

    async def _dismiss_cookie_banners(self, page: Page) -> None:
        for label in (
            "Accept all", "Accept All", "I agree", "Agree", "OK", "Got it",
            "Allow all", "Accept", "Yes, I agree",
        ):
            try:
                btn = page.get_by_role("button", name=label)
                if await btn.count() > 0:
                    await btn.first.click(timeout=2_500)
                    await asyncio.sleep(0.4)
                    return
            except Exception:
                pass

    def _official_url_for_app(self, app: str) -> str | None:
        key = app.lower().strip()
        if key in _OFFICIAL_APP_URLS:
            return _OFFICIAL_APP_URLS[key]
        compact = key.replace(" ", "")
        for name, url in _OFFICIAL_APP_URLS.items():
            if compact == name.replace(" ", "") or compact in name.replace(" ", ""):
                return url
        return None

    def _score_google_result(self, href: str, title: str, app: str) -> int:
        h = href.lower()
        t = (title or "").lower()
        app_l = app.lower().strip()
        app_compact = app_l.replace(" ", "").replace("-", "")

        if any(b in h for b in _BAD_RESULT_DOMAINS):
            return -100

        score = 0
        if app_compact and app_compact in h.replace("-", "").replace(".", ""):
            score += 70
        if f"{app_compact}.com" in h or f"www.{app_compact}.com" in h:
            score += 120
        if "/download" in h:
            score += 40
        if "official" in t or "download" in t:
            score += 20
        if app_l in t:
            score += 25
        return score

    async def _find_official_url_on_google(self, page: Page, app: str) -> str:
        await asyncio.sleep(0.8)
        candidates: list[tuple[int, str]] = []

        for sel in ('#search a[href^="http"]', 'div#rso a[href^="http"]'):
            loc = page.locator(sel)
            try:
                n = await loc.count()
            except Exception:
                continue
            for i in range(min(n, 20)):
                a = loc.nth(i)
                try:
                    href = (await a.get_attribute("href") or "").strip()
                    title = (await a.inner_text() or "").strip()
                except Exception:
                    continue
                if not href.startswith("http"):
                    continue
                sc = self._score_google_result(href, title, app)
                if sc > 0:
                    candidates.append((sc, href))

        if not candidates:
            return ""

        candidates.sort(key=lambda x: -x[0])
        best = candidates[0][1]
        print(f"[Browser] Official pick: {best} (score={candidates[0][0]})")
        return best

    async def _save_download(self, download) -> str:
        name = (download.suggested_filename or "download.bin").strip()
        safe = re.sub(r'[<>:"/\\|?*]', "_", name)[:200]
        dest = Path.home() / "Downloads" / safe
        if dest.exists():
            stem, suf = dest.stem, dest.suffix
            dest = Path.home() / "Downloads" / f"{stem}_{int(time.time())}{suf}"
        await download.save_as(dest)
        size_mb = dest.stat().st_size / (1024 * 1024)
        return f"Saved to {dest} ({size_mb:.1f} MB)"

    async def _try_download_click(self, page: Page, app_name: str) -> str | None:
        """Click a download control and wait for the browser download event."""
        app = (app_name or "").strip()

        async def _click_locator(loc) -> None:
            el = loc.first
            await el.scroll_into_view_if_needed(timeout=5_000)
            await el.click(timeout=12_000, force=True)

        click_attempts = []

        for ext in (".dmg", ".pkg", ".exe", ".msi", ".zip"):
            loc = page.locator(f'a[href*="{ext}" i]')
            click_attempts.append((f"link {ext}", loc))

        labels = [
            "Download", "Download now", "Free download", "Get the app",
            "Download for Mac", "Download for macOS", "Mac download",
            "Download for Windows", "Get download",
        ]
        if app:
            labels = [f"Download {app}", f"Get {app}"] + labels

        for label in labels:
            for role in ("link", "button"):
                loc = page.get_by_role(role, name=label)
                click_attempts.append((label, loc))

        for desc, loc in click_attempts:
            try:
                if await loc.count() == 0:
                    continue
            except Exception:
                continue
            try:
                async with page.expect_download(timeout=90_000) as dl_info:
                    await _click_locator(loc)
                download = await dl_info.value
                self._bring_browser_front()
                return await self._save_download(download)
            except Exception:
                continue
        return None

    async def _click_download_on_page(self, page: Page, app_name: str) -> str:
        await self._dismiss_cookie_banners(page)
        saved = await self._try_download_click(page, app_name)
        if saved:
            return saved

        app = (app_name or "").strip()
        for ext in (".dmg", ".pkg", ".exe", ".msi", ".deb", ".zip", ".app"):
            try:
                loc = page.locator(f'a[href*="{ext}" i]')
                if await loc.count() > 0:
                    await loc.first.click(timeout=10_000)
                    await asyncio.sleep(1.0)
                    self._bring_browser_front()
                    return f"Started download ({ext}) from {page.url}"
            except Exception:
                pass

        labels = [
            "Download", "Download now", "Free download", "Get the app",
            "Get app", "Install", "Download for Mac", "Download for macOS",
            "Download for Windows", "Get download", "Download free",
        ]
        if app:
            labels = [f"Download {app}", f"Get {app}", app] + labels

        seen: set[str] = set()
        for label in labels:
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            for role in ("link", "button"):
                try:
                    loc = page.get_by_role(role, name=label)
                    if await loc.count() == 0:
                        continue
                    await loc.first.click(timeout=8_000)
                    await asyncio.sleep(1.0)
                    self._bring_browser_front()
                    return f"Clicked '{label}' on {page.url}"
                except Exception:
                    pass

        try:
            loc = page.get_by_text(re.compile(r"download", re.I))
            if await loc.count() > 0:
                await loc.first.click(timeout=6_000)
                await asyncio.sleep(0.8)
                self._bring_browser_front()
                return f"Clicked Download on {page.url}"
        except Exception:
            pass

        self._bring_browser_front()
        return (
            f"Opened {page.url} — no Download button found automatically. "
            "Click Download on the page; the file should appear in your Downloads folder."
        )

    async def app_download_from_google(self, app_name: str) -> str:
        """
        Google → search 'download {app}' → official site → Download → ~/Downloads.
        """
        app = re.sub(
            r"\b(download|install|get|from\s+google|the\s+app|app|please)\b",
            "",
            (app_name or ""),
            flags=re.I,
        ).strip()
        if not app:
            return "FAILED: Which app should I download?"

        search_q = f"download {app}"
        page = await self._get_page()
        site_url = self._official_url_for_app(app)

        if not site_url:
            search_url = "https://www.google.com/search?q=" + quote_plus(search_q)
            print(f"[Browser] 1) Google search: {search_q}")
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(1.2)
            except PlaywrightTimeout:
                pass
            except Exception as e:
                return f"FAILED: Could not open Google — {e}"

            await self._dismiss_cookie_banners(page)
            self._bring_browser_front()
            picked = await self._find_official_url_on_google(page, app)
            if picked:
                site_url = picked
                try:
                    await page.goto(site_url, wait_until="domcontentloaded", timeout=35_000)
                    await asyncio.sleep(1.2)
                except PlaywrightTimeout:
                    pass

        if not site_url:
            return (
                f"FAILED: No official download page found for '{app}'. "
                f"Searched Google for '{search_q}'."
            )

        if site_url and page.url != site_url:
            print(f"[Browser] 2) Official site: {site_url}")
            try:
                await page.goto(site_url, wait_until="domcontentloaded", timeout=35_000)
                await asyncio.sleep(1.2)
            except PlaywrightTimeout:
                pass
            except Exception as e:
                return f"FAILED: Could not open {site_url} — {e}"
        else:
            print(f"[Browser] 2) On site: {page.url}")

        await self._dismiss_cookie_banners(page)
        self._bring_browser_front()

        print(f"[Browser] 3) Click Download on {page.url}")
        click_msg = await self._click_download_on_page(page, app)
        return (
            f"Google → '{search_q}' → {site_url} → {click_msg}"
        )

    async def close_browser(self) -> str:
        await self._async_close()
        return f"{self.browser_name} closed."

class _SessionRegistry:
    """Tüm aktif tarayıcı oturumlarını yönetir."""

    def __init__(self):
        self._sessions:       dict[str, _BrowserSession] = {}
        self._active_browser: str                        = ""
        self._lock            = threading.Lock()

    def _get_or_create(self, browser_name: str) -> _BrowserSession:
        with self._lock:
            if browser_name not in self._sessions:
                sess = _BrowserSession(browser_name)
                sess.start()
                self._sessions[browser_name] = sess
                print(f"[Registry] New session: {browser_name}")
            return self._sessions[browser_name]

    def get(self, browser_name: str | None = None) -> _BrowserSession:
        if not browser_name:
            browser_name = self._active_browser or _detect_default_browser()
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        sess = self._get_or_create(browser_name)
        self._active_browser = browser_name
        return sess

    def switch(self, browser_name: str) -> str:
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        self._get_or_create(browser_name)
        self._active_browser = browser_name
        return f"Active browser → {browser_name}"

    def close_one(self, browser_name: str) -> str:
        with self._lock:
            sess = self._sessions.pop(browser_name, None)
        if sess:
            sess.close()
            if self._active_browser == browser_name:
                self._active_browser = ""
            return f"{browser_name} closed."
        return f"No active session for: {browser_name}"

    def close_all(self) -> str:
        with self._lock:
            names    = list(self._sessions.keys())
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_browser = ""
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        return "All browsers closed: " + (", ".join(names) if names else "none")

    def list_sessions(self) -> str:
        with self._lock:
            if not self._sessions:
                return "No active browser sessions."
            lines = []
            for name in self._sessions:
                marker = " ◀ active" if name == self._active_browser else ""
                lines.append(f"  • {name}{marker}")
            return "Open browsers:\n" + "\n".join(lines)


_registry = _SessionRegistry()

def browser_control(
    parameters:    dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    action  = params.get("action", "").lower().strip()
    browser = params.get("browser", "").lower().strip() or None
    result  = "Unknown action."

    if action == "switch":
        target = browser or params.get("target", "").lower().strip()
        result = _registry.switch(target) if target else "Please specify a browser."
        _log(player, result)
        return result

    if action == "list_browsers":
        result = _registry.list_sessions()
        _log(player, result)
        return result

    if action == "close_all":
        result = _registry.close_all()
        _log(player, result)
        return result

    # Navigation-only: use real browser — never spin up Playwright (avoids about:blank window).
    if _USE_NATIVE_NAV and action == "go_to":
        result = _native_navigate(params.get("url", ""), browser)
        _log(player, result)
        return result

    if _USE_NATIVE_NAV and action == "search":
        from urllib.parse import quote_plus
        _engines = {
            "google": "https://www.google.com/search?q=",
            "bing": "https://www.bing.com/search?q=",
            "duckduckgo": "https://duckduckgo.com/?q=",
            "yandex": "https://yandex.com/search/?text=",
        }
        engine = (params.get("engine") or "google").lower()
        base = _engines.get(engine, _engines["google"])
        query = params.get("query", "")
        result = _native_navigate(base + quote_plus(query), browser)
        _log(player, result)
        return result

    if _USE_NATIVE_NAV and action == "new_tab" and params.get("url"):
        result = _native_navigate(params.get("url", ""), browser)
        _log(player, result)
        return result

    try:
        sess = _registry.get(browser)
    except Exception as e:
        result = f"Could not start browser session: {e}"
        _log(player, result)
        return result

    try:
        if action == "go_to":
            result = sess.run(sess.go_to(params.get("url", "")))
        elif action == "search":
            result = sess.run(sess.search(params.get("query", ""), params.get("engine", "google")))
        elif action == "click":
            result = sess.run(sess.click(params.get("selector"), params.get("text")))
        elif action == "type":
            result = sess.run(sess.type_text(
                params.get("selector"), params.get("text", ""), params.get("clear_first", True)))
        elif action == "scroll":
            result = sess.run(sess.scroll(params.get("direction", "down"), int(params.get("amount", 500))))
        elif action == "fill_form":
            result = sess.run(sess.fill_form(params.get("fields", {})))
        elif action == "smart_click":
            result = sess.run(sess.smart_click(params.get("description", "")))
        elif action == "smart_type":
            result = sess.run(sess.smart_type(params.get("description", ""), params.get("text", "")))
        elif action == "get_text":
            result = sess.run(sess.get_text())
        elif action == "get_url":
            result = sess.run(sess.get_url())
        elif action == "press":
            result = sess.run(sess.press(params.get("key", "Enter")))
        elif action == "new_tab":
            result = sess.run(sess.new_tab(params.get("url", "")))
        elif action == "close_tab":
            result = sess.run(sess.close_tab())
        elif action == "screenshot":
            result = sess.run(sess.screenshot(params.get("path")))
        elif action == "back":
            result = sess.run(sess.back())
        elif action == "forward":
            result = sess.run(sess.forward())
        elif action == "reload":
            result = sess.run(sess.reload())
        elif action == "close":
            target = browser or _registry._active_browser
            result = _registry.close_one(target) if target else "No browser specified."
        else:
            result = f"Unknown browser action: '{action}'"

    except concurrent.futures.TimeoutError:
        result = f"Browser action '{action}' timed out (60s)."
    except Exception as e:
        result = f"Browser error ({action}): {e}"

    _log(player, result)
    return result


def _log(player, text: str):
    short = str(text)[:80]
    print(f"[Browser] {short}")
    if player:
        player.write_log(f"[browser] {short[:60]}")