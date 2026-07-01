"""Confirmed terminal-first downloads and installs with browser fallback."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse

try:
    import requests

    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

_OS = platform.system()
_MAX_BYTES = 500 * 1024 * 1024  # 500 MB
_MIN_BYTES = 50 * 1024  # skip HTML/error pages saved as tiny files
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_URL_RE = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+", re.I)
_FILE_EXT_RE = re.compile(
    r"\.(pdf|zip|rar|7z|tar|gz|bz2|xz|dmg|pkg|exe|msi|deb|rpm|apk|mp4|mp3|m4a|wav|"
    r"mkv|avi|mov|webm|doc|docx|xls|xlsx|ppt|pptx|csv|json|png|jpe?g|gif|webp|iso)"
    r"(?:\?|$)",
    re.I,
)
_YT_RE = re.compile(
    r"(?:youtube\.com/watch|youtu\.be/|youtube\.com/shorts/)",
    re.I,
)
_TERMINAL_ACTION_ID = "download_terminal_command"


def _serialize_terminal_plan(plan, *, query: str, kind: str) -> dict:
    return {
        "executable": plan.executable,
        "args": list(plan.args),
        "display": plan.display,
        "cwd": str(plan.cwd) if plan.cwd else "",
        "browser_fallback": plan.browser_fallback,
        "query": query,
        "kind": kind,
        "source": plan.source,
    }


def _stage_terminal_plan(plan, *, query: str, kind: str) -> str:
    from actions import confirm_gate as cg

    staged = _serialize_terminal_plan(plan, query=query, kind=kind)
    confirmation = cg.needs_confirm(
        _TERMINAL_ACTION_ID,
        f"Verified {kind} command is ready.",
        staged,
        ask=f"Install {query}?",
    )
    card = json.dumps(
        {
            "title": query,
            "source": plan.source,
            "command": plan.display,
        },
        ensure_ascii=False,
    )
    return f"{confirmation}\nINSTALL_CONFIRMATION_JSON:{card}"


def _handle_terminal_confirmation(params: dict) -> str | None:
    """Handle yes/no for a staged command; return None for an initial request."""
    from actions import confirm_gate as cg
    from actions.terminal_download import TerminalPlan, run_plan

    if not (cg.as_bool(params.get("confirm")) or cg.as_bool(params.get("cancel"))):
        return None

    staged = cg.peek_pending(_TERMINAL_ACTION_ID)
    proceed, stored, error = cg.consume_confirmed(params, _TERMINAL_ACTION_ID)
    if error:
        if staged and cg.as_bool(params.get("cancel")):
            command = staged.get("display", "")
            return (
                "CANCELLED_COMMAND: Nothing was run. Show this command on screen "
                f"without reading it aloud:\n```shell\n{command}\n```"
            )
        return error
    if not proceed or not stored:
        return "FAILED: No matching terminal command is awaiting confirmation."

    plan = TerminalPlan(
        executable=str(stored.get("executable", "")),
        args=tuple(str(arg) for arg in stored.get("args", [])),
        display=str(stored.get("display", "")),
        cwd=Path(stored["cwd"]) if stored.get("cwd") else None,
        browser_fallback=bool(stored.get("browser_fallback")),
        source=str(stored.get("source") or "Terminal"),
    )
    ok, output = run_plan(plan)
    if ok:
        detail = f" {output}" if output else ""
        return f"SUCCESS: Ran `{plan.display}`.{detail}"
    if plan.browser_fallback and stored.get("query"):
        fallback = _browser_app_download(str(stored["query"]))
        return (
            f"Terminal command failed: {output} Browser fallback: {fallback}"
        )
    return f"FAILED: `{plan.display}` — {output}"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _downloads_dir(raw: str = "downloads") -> Path:
    from actions.file_controller import _resolve_path

    dest = _resolve_path(raw or "downloads")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _which(name: str) -> str | None:
    return shutil.which(name)


def _safe_filename(name: str) -> str:
    name = unquote(name).strip().replace("\x00", "")
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name[:200] or "download"


def _is_safe_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "0.0.0.0") or host.endswith(".local"):
        return False
    return True


def _filename_from_url(url: str, headers: dict | None = None) -> str:
    path = urlparse(url).path
    name = Path(path).name if path else ""
    if headers:
        cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\n]+)"?', cd, re.I)
        if m:
            name = m.group(1).strip()
    return _safe_filename(name) if name and "." in name else ""


def _score_url(url: str, title: str = "") -> int:
    u = url.lower()
    score = 0
    if _FILE_EXT_RE.search(u):
        score += 40
    if any(x in u for x in ("github.com/releases", "download", "/dl/", "get/download")):
        score += 25
    if "drive.google.com" in u or "docs.google.com" in u:
        score += 15
    if _YT_RE.search(u):
        score += 10
    if any(x in u for x in ("google.com/search", "bing.com/search", "duckduckgo.com")):
        score -= 50
    if title and "download" in title.lower():
        score += 10
    return score


def _extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,);]")
        if u not in seen and _is_safe_url(u):
            seen.add(u)
            out.append(u)
    return out


def _ddg_search(query: str, max_results: int = 8) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        return []

    results: list[dict] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", r.get("snippet", "")),
                    "url": r.get("href", r.get("url", "")),
                })
    except Exception as e:
        print(f"[Download] DDG search failed: {e}")
    return results


def _gemini_find_links(query: str) -> list[str]:
    try:
        from actions.web_search import _gemini_search_with_retry

        prompt = (
            f"User wants to download: {query}\n"
            "Reply with 1–3 direct HTTPS download URLs only (no search pages). "
            "Prefer official sites, GitHub releases, or direct file links. "
            "If only YouTube exists, include the YouTube watch URL."
        )
        text = _gemini_search_with_retry(prompt, attempts=2)
        return _extract_urls(text)
    except Exception as e:
        print(f"[Download] Gemini link lookup failed: {e}")
        return []


def _google_search_urls(query: str) -> list[str]:
    """Web search (DDG) + optional Gemini — same sources users get from Google."""
    q = query.strip()
    if not q:
        return []

    candidates: list[tuple[int, str]] = []
    for search_q in (f"{q} download direct link", f"{q} official download", q):
        for r in _ddg_search(search_q, max_results=6):
            url = (r.get("url") or "").strip()
            if url and _is_safe_url(url):
                candidates.append((_score_url(url, r.get("title", "")), url))

    for url in _gemini_find_links(q):
        candidates.append((_score_url(url) + 5, url))

    seen: set[str] = set()
    ordered: list[str] = []
    for _, url in sorted(candidates, key=lambda x: -x[0]):
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def _download_http(url: str, dest_dir: Path) -> str:
    if not _REQUESTS_OK:
        return "FAILED: requests package not available."

    headers = {"User-Agent": _USER_AGENT}
    try:
        with requests.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as r:
            r.raise_for_status()
            name = _filename_from_url(url, dict(r.headers))
            if not name:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].lower()
                ext = {
                    "application/pdf": ".pdf",
                    "application/zip": ".zip",
                    "application/octet-stream": ".bin",
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                }.get(ctype, "")
                name = _safe_filename(Path(urlparse(url).path).name or f"download{ext}")
                if not name.endswith(ext) and ext and ext not in name:
                    name += ext

            dest = dest_dir / name
            if dest.exists():
                dest = dest_dir / _unique_name(dest_dir, name)

            total = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        f.close()
                        dest.unlink(missing_ok=True)
                        return f"FAILED: File exceeds {_MAX_BYTES // (1024*1024)} MB limit."
                    f.write(chunk)

            if total < _MIN_BYTES:
                dest.unlink(missing_ok=True)
                return (
                    f"FAILED: Download too small ({total} bytes) — likely a web page, "
                    "not an installer. Use action google/browser for apps."
                )

            size_mb = total / (1024 * 1024)
            return f"Downloaded to {dest} ({size_mb:.1f} MB)."
    except Exception as e:
        return f"FAILED: HTTP download — {e}"


def _unique_name(folder: Path, name: str) -> str:
    stem = Path(name).stem
    suffix = Path(name).suffix
    for i in range(1, 100):
        candidate = folder / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate.name
    return f"{stem}_{os.getpid()}{suffix}"


def _download_curl(url: str, dest_dir: Path) -> str:
    curl = _which("curl")
    if not curl:
        return "FAILED: curl not found."

    out_tpl = str(dest_dir / "neo_download")
    cmd = [
        curl, "-fL", "--retry", "2", "--connect-timeout", "20",
        "-A", _USER_AGENT, "-o", out_tpl, url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()[:200]
            return f"FAILED: curl — {err}"

        path = Path(out_tpl)
        if not path.exists() or path.stat().st_size < _MIN_BYTES:
            path.unlink(missing_ok=True)
            return (
                "FAILED: curl saved a tiny/empty file — use action google for app installs."
            )

        name = _filename_from_url(url)
        if name and name != "neo_download":
            final = dest_dir / name
            if final.exists():
                final = dest_dir / _unique_name(dest_dir, name)
            path.rename(final)
            path = final

        size_mb = path.stat().st_size / (1024 * 1024)
        return f"Downloaded via curl to {path} ({size_mb:.1f} MB)."
    except subprocess.TimeoutExpired:
        return "FAILED: curl timed out."
    except Exception as e:
        return f"FAILED: curl — {e}"


def _download_wget(url: str, dest_dir: Path) -> str:
    wget = _which("wget")
    if not wget:
        return "FAILED: wget not found."

    cmd = [
        wget, "-q", "--show-progress", "-O", "-",
        "--user-agent", _USER_AGENT, url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode != 0:
            return f"FAILED: wget exit {r.returncode}"

        data = r.stdout
        if not data or len(data) > _MAX_BYTES:
            return "FAILED: wget empty or file too large."

        name = _filename_from_url(url) or "download.bin"
        dest = dest_dir / name
        if dest.exists():
            dest = dest_dir / _unique_name(dest_dir, name)
        dest.write_bytes(data)
        size_mb = len(data) / (1024 * 1024)
        return f"Downloaded via wget to {dest} ({size_mb:.1f} MB)."
    except Exception as e:
        return f"FAILED: wget — {e}"


def _download_ytdlp(url_or_query: str, dest_dir: Path, *, is_search: bool = False) -> str:
    ytdlp = _which("yt-dlp") or _which("youtube-dl")
    if not ytdlp:
        return (
            "FAILED: yt-dlp not installed. Install with: "
            "brew install yt-dlp  OR  pip install yt-dlp"
        )

    out_tpl = str(dest_dir / "%(title).180s [%(id)s].%(ext)s")
    cmd = [ytdlp, "--no-playlist", "-f", "best[height<=1080]/best", "-o", out_tpl]
    if is_search and not _YT_RE.search(url_or_query):
        cmd.append(f"ytsearch1:{url_or_query}")
    else:
        cmd.append(url_or_query)

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()[:300]
            return f"FAILED: yt-dlp — {err}"

        recent = sorted(dest_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if recent and recent[0].is_file():
            f = recent[0]
            size_mb = f.stat().st_size / (1024 * 1024)
            return f"Downloaded via yt-dlp to {f} ({size_mb:.1f} MB)."
        return "Downloaded via yt-dlp (check Downloads folder)."
    except subprocess.TimeoutExpired:
        return "FAILED: yt-dlp timed out."
    except Exception as e:
        return f"FAILED: yt-dlp — {e}"


def _download_url(url: str, dest: str = "downloads") -> str:
    url = url.strip()
    if not _is_safe_url(url):
        return f"FAILED: Unsafe or invalid URL: {url}"

    dest_dir = _downloads_dir(dest)

    if _YT_RE.search(url):
        msg = _download_ytdlp(url, dest_dir)
        if not msg.startswith("FAILED"):
            return msg

    errors: list[str] = []
    for fn in (_download_http, _download_curl, _download_wget):
        msg = fn(url, dest_dir)
        if not msg.startswith("FAILED"):
            return msg
        errors.append(msg)

    return errors[0] if errors else "FAILED: Could not download."


def _normalize_app_query(query: str) -> str:
    q = re.sub(
        r"\b(download|install|get|from\s+google|the\s+internet|the\s+web|please)\b",
        "",
        (query or ""),
        flags=re.I,
    )
    return q.strip()


def _looks_like_app_install(query: str) -> bool:
    q = query.strip()
    if not q or _extract_urls(q):
        return False
    if _FILE_EXT_RE.search(q) or _YT_RE.search(q):
        return False
    if re.search(r"\b(youtube|video|song|mp3|music|pdf|zip|image|photo)\b", q, re.I):
        return False
    return True


def _native_app_download(query: str) -> str:
    """Fallback: open search + official page in the user's normal browser."""
    from actions.browser_native import navigate_user_browser
    from config import search_engine_url

    app = _normalize_app_query(query)
    if not app:
        return "FAILED: Which app should I download?"

    # Dynamic search first — no hardcoded truth table
    official = None
    try:
        from actions.browser_control import _app_download_hint
        official = _app_download_hint(app)
    except ImportError:
        pass

    if not official:
        app_c = app.lower().replace(" ", "")
        for r in _ddg_search(f"download {app} official", max_results=8):
            url = (r.get("url") or "").strip()
            if not url or not _is_safe_url(url):
                continue
            h = url.lower()
            if app_c in h.replace("-", "").replace(".", "") or f"{app_c}.com" in h:
                official = url
                break

    search_url = search_engine_url(f"download {app}")
    navigate_user_browser(search_url)
    if official:
        navigate_user_browser(official)
        return (
            f"Opened search and {official} in your browser. "
            "Click the Download button — the installer will go to Downloads."
        )
    return (
        f"Opened search for 'download {app}'. "
        "Click the official site link, then Download."
    )


def _browser_app_download(query: str, browser: str | None = None) -> str:
    """User's Chrome: Google → official site → Download (no Playwright by default)."""
    app = _normalize_app_query(query)
    if not app:
        return "FAILED: Which app should I download?"

    if _OS == "Darwin":
        from actions.browser_native import native_app_download_from_google

        try:
            return native_app_download_from_google(app, browser)
        except Exception as e:
            print(f"[Download] Native Chrome failed ({e}), trying Playwright")

    try:
        from actions.browser_control import _registry

        sess = _registry.get(browser)
        print(f"[Download] Playwright fallback for: {app}")
        return sess.run(sess.app_download_from_google(app), timeout=180)
    except Exception as e:
        return _native_app_download(query)


def _download_from_search(query: str, dest: str = "downloads") -> str:
    query = query.strip()
    if not query:
        return "FAILED: What should I download? Give a name, URL, or search terms."

    if _looks_like_app_install(query):
        return _browser_app_download(query)

    dest_dir = _downloads_dir(dest)

    urls = _extract_urls(query)
    if urls:
        return _download_url(urls[0], dest)

    if _YT_RE.search(query) or re.search(r"\b(youtube|video|mp3|song|music)\b", query, re.I):
        msg = _download_ytdlp(query, dest_dir, is_search=True)
        if not msg.startswith("FAILED"):
            return msg

    for url in _google_search_urls(query):
        if _YT_RE.search(url):
            msg = _download_ytdlp(url, dest_dir)
            if not msg.startswith("FAILED"):
                return msg
            continue

        if _score_url(url) < 5:
            continue

        msg = _download_url(url, dest)
        if not msg.startswith("FAILED"):
            return msg

    ytdlp_msg = _download_ytdlp(query, dest_dir, is_search=True)
    if not ytdlp_msg.startswith("FAILED"):
        return ytdlp_msg

    return _browser_app_download(query)


def download_control(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "auto").lower().strip()
    query = (params.get("query") or params.get("name") or "").strip()
    url = (params.get("url") or "").strip()
    dest = (params.get("destination") or params.get("path") or "downloads").strip()

    if player:
        player.write_log(f"[download] {action} {query or url}")

    try:
        confirmation_result = _handle_terminal_confirmation(params)
        if confirmation_result is not None:
            return confirmation_result

        if action in ("install", "package", "terminal"):
            from actions.terminal_download import resolve_install_plan

            if not query:
                return "FAILED: What should I install?"
            plan = resolve_install_plan(query)
            if plan:
                return _stage_terminal_plan(plan, query=query, kind="installation")
            return _browser_app_download(query)

        if action in ("url", "link"):
            target = url or query
            if not _is_safe_url(target):
                return "FAILED: Provide a safe HTTP or HTTPS download URL."
            from actions.terminal_download import resolve_url_download_plan

            dest_dir = _downloads_dir(dest)
            filename = _filename_from_url(target) or "download.bin"
            plan = resolve_url_download_plan(target, dest_dir, filename)
            if plan:
                return _stage_terminal_plan(plan, query=target, kind="download")
            return _download_url(target, dest)

        if action in ("search", "google", "find", "browser", "app"):
            return _browser_app_download(query or url)

        if action in ("youtube", "video", "audio", "music"):
            dest_dir = _downloads_dir(dest)
            target = url or query
            if not target:
                return "FAILED: Provide a YouTube URL or search query."
            is_search = not _YT_RE.search(target)
            return _download_ytdlp(target, dest_dir, is_search=is_search)

        if action == "cli":
            from actions.terminal_download import resolve_cli_plan

            raw_args = params.get("args") or params.get("arguments") or []
            if isinstance(raw_args, str):
                import shlex

                raw_args = shlex.split(raw_args, posix=_OS != "Windows")
            tool = params.get("tool") or params.get("cli") or "curl"
            plan = resolve_cli_plan(tool, list(raw_args), _downloads_dir(dest))
            if not plan:
                return "FAILED: Use an installed allowlisted CLI: curl, wget, or yt-dlp."
            return _stage_terminal_plan(plan, query=query or url, kind="download")

        if action == "auto":
            if url:
                return _download_url(url, dest)
            if _looks_like_app_install(query):
                return _browser_app_download(query)
            return _download_from_search(query, dest)

        return (
            f"Unknown action '{action}'. "
            "Use auto | google | url | youtube | cli."
        )

    except Exception as e:
        return f"Download error: {e}"
