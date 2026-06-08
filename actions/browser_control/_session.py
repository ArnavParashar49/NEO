"""browser_control session layer — the stateful Playwright browser session,
the session registry, and the _registry singleton. Pure helpers live in
_helpers."""

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

from actions.browser_control._helpers import (
    _OS, _USE_NATIVE_NAV, _OFFICIAL_APP_URLS, _BAD_RESULT_DOMAINS,
    _BROWSER_SPECS, _ALIASES,
    _normalize_url, _native_navigate, _user_agent, _real_profile_dir,
    _firefox_profile_dir, _find_opera_windows, _find_exe_windows,
    _resolve_browser, _detect_default_browser,
)


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
