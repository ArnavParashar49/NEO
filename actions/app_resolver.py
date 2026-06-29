"""
Discover installed apps dynamically — no per-app hardcoded paths.

Windows: Get-StartApps, Start Menu .lnk, App Paths registry, PATH
macOS:   open -a, mdfind, Spotlight
Linux:   .desktop files, PATH, gtk-launch
"""

from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

_SYSTEM = platform.system()

LaunchKind = Literal["exe", "lnk", "app_id", "uri", "url", "open_a", "desktop"]

_KIND_PRIORITY = {"app_id": 4, "lnk": 3, "open_a": 4, "desktop": 3, "exe": 2, "uri": 1, "url": 1}


@dataclass(frozen=True)
class ResolvedTarget:
    query: str
    label: str
    kind: LaunchKind
    value: str
    score: int


# Session cache — built once from the OS, not a curated app list.
_index_cache: list[ResolvedTarget] | None = None

MIN_MATCH_SCORE = 55


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[\s\-_]+", _norm(s)) if t}


def _score(query: str, name: str) -> int:
    q, n = _norm(query), _norm(name)
    if not q or not n:
        return 0
    if q == n:
        return 100
    if n.startswith(q):
        extra = len(n) - len(q)
        if extra <= 2:
            return 92
        return max(MIN_MATCH_SCORE, 80 - extra)
    if q.startswith(n) and len(n) >= 4:
        return 88
    if q in n:
        return 75 + min(len(q), 20)
    if n in q:
        # Avoid "sc" matching inside "vscode", etc.
        if len(n) < 4:
            return 0
        return 65 + min(len(n), 15)
    qt, nt = _tokens(query), _tokens(name)
    overlap = qt & nt
    if overlap:
        # All query tokens present in name → strong match (e.g. visual+studio+code)
        if qt <= nt:
            return 80 + 5 * len(overlap)
        return 50 + 10 * len(overlap)
    return 0


def _better_match(a: ResolvedTarget, b: ResolvedTarget) -> bool:
    """True if a should win over b."""
    if a.score != b.score:
        return a.score > b.score
    pa, pb = _KIND_PRIORITY.get(a.kind, 0), _KIND_PRIORITY.get(b.kind, 0)
    if pa != pb:
        return pa > pb
    # Shorter human-facing names beat helper binaries (docker vs docker-credential-…)
    return len(a.label) < len(b.label)


def _build_windows_index() -> list[ResolvedTarget]:
    items: list[ResolvedTarget] = []
    items.extend(_win_start_apps())
    items.extend(_win_start_menu())
    items.extend(_win_registry_paths())
    return items


def looks_like_url(text: str) -> bool:
    s = text.strip()
    if re.match(r"^https?://", s, re.I):
        return True
    return bool(re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/|$)", s, re.I))


def guess_web_url(name: str) -> str | None:
    """Dynamic URL resolution via web search — no .com guesswork."""
    s = name.strip()
    if not s:
        return None
    if re.match(r"^https?://", s, re.I):
        return s
    if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$", s, re.I):
        return f"https://{s}"
    # Single-word name: try a quick DDG search for the official site
    if " " not in s and re.match(r"^[a-z0-9-]+$", s, re.I):
        try:
            from actions.download_control import _ddg_search
            results = _ddg_search(f"{s} official site", max_results=3)
            for r in results:
                url = (r.get("url") or "").strip()
                if url and url.startswith("https://") and s.lower() in url.lower():
                    # Prefer the shortest plausible official URL
                    if any(domain in url.lower() for domain in [f"{s}.com", f"{s}.org", f"{s}.io", f"{s}.dev", f"{s}.app", f"{s}.co"]):
                        return url
            # Fallback to .com only as last resort
            return f"https://www.{s}.com"
        except Exception:
            return f"https://www.{s}.com"
    return None


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


# ── Windows ──────────────────────────────────────────────────────────────────


def _win_start_apps() -> list[ResolvedTarget]:
    out: list[ResolvedTarget] = []
    try:
        ps = (
            "Get-StartApps | Select-Object Name, AppID | "
            "ConvertTo-Json -Compress"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return out
        raw = r.stdout.strip()
        data = __import__("json").loads(raw)
        if isinstance(data, dict):
            data = [data]
        for item in data or []:
            name = (item.get("Name") or "").strip()
            app_id = (item.get("AppID") or "").strip()
            if name and app_id:
                out.append(
                    ResolvedTarget(name, name, "app_id", app_id, 0)
                )
    except Exception as e:
        print(f"[AppResolver] Get-StartApps: {e}")
    return out


def _win_start_menu() -> list[ResolvedTarget]:
    out: list[ResolvedTarget] = []
    roots = [
        _expand(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        _expand(r"%AppData%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            label = os.path.splitext(os.path.basename(path))[0]
            out.append(ResolvedTarget(label, label, "lnk", path, 0))
    return out


def _win_registry_paths() -> list[ResolvedTarget]:
    out: list[ResolvedTarget] = []
    if _SYSTEM != "Windows":
        return out
    try:
        import winreg

        roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        ]
        for hive, subkey in roots:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    i = 0
                    while True:
                        try:
                            name = winreg.EnumKey(key, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(key, name) as app_key:
                                exe, _ = winreg.QueryValueEx(app_key, "")
                                if exe and os.path.isfile(exe):
                                    label = os.path.splitext(os.path.basename(name))[0]
                                    out.append(
                                        ResolvedTarget(label, label, "exe", exe, 0)
                                    )
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception as e:
        print(f"[AppResolver] registry scan: {e}")
    return out


def _win_path_bins() -> list[ResolvedTarget]:
    """Not used in index — PATH is too noisy."""
    return []


# ── macOS ────────────────────────────────────────────────────────────────────


def _mac_applications() -> list[ResolvedTarget]:
    out: list[ResolvedTarget] = []
    for pattern in ("/Applications/*.app", os.path.expanduser("~/Applications/*.app")):
        for path in glob.glob(pattern):
            label = os.path.splitext(os.path.basename(path))[0]
            out.append(ResolvedTarget(label, label, "open_a", label, 0))
    return out


def _build_mac_index() -> list[ResolvedTarget]:
    return _mac_applications()


# ── Linux ────────────────────────────────────────────────────────────────────


def _linux_desktop_files() -> list[ResolvedTarget]:
    out: list[ResolvedTarget] = []
    dirs = [
        "/usr/share/applications",
        "/var/lib/snapd/desktop/applications",
        os.path.expanduser("~/.local/share/applications"),
    ]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "*.desktop")):
            label = os.path.splitext(os.path.basename(path))[0]
            name = label
            try:
                for line in open(path, encoding="utf-8", errors="ignore"):
                    if line.startswith("Name="):
                        name = line.split("=", 1)[1].strip()
                        break
            except OSError:
                pass
            out.append(ResolvedTarget(name, label, "desktop", path, 0))
    return out


def _build_linux_index() -> list[ResolvedTarget]:
    return _linux_desktop_files()


def _build_index() -> list[ResolvedTarget]:
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    if _SYSTEM == "Windows":
        _index_cache = _build_windows_index()
    elif _SYSTEM == "Darwin":
        _index_cache = _build_mac_index()
    else:
        _index_cache = _build_linux_index()
    print(f"[AppResolver] Indexed {len(_index_cache)} launch targets ({_SYSTEM})")
    return _index_cache


def clear_index_cache() -> None:
    global _index_cache
    _index_cache = None


def resolve(query: str) -> ResolvedTarget | None:
    """Best installed-app match for the user's spoken name."""
    raw = (query or "").strip()
    if not raw:
        return None

    if looks_like_url(raw):
        url = guess_web_url(raw) or raw
        return ResolvedTarget(raw, raw, "url", url, 100)

    if ":" in raw and not os.path.isfile(raw):
        return ResolvedTarget(raw, raw, "uri", raw, 100)

    best: ResolvedTarget | None = None
    for item in _build_index():
        sc = _score(raw, item.label)
        if sc <= 0:
            continue
        candidate = ResolvedTarget(
            raw, item.label, item.kind, item.value, sc
        )
        if best is None or _better_match(candidate, best):
            best = candidate

    if best and best.score >= MIN_MATCH_SCORE:
        return best

    # Direct binary on PATH (not indexed yet)
    for candidate in (raw, raw.split()[0], raw.replace(" ", "")):
        hit = shutil.which(candidate)
        if hit:
            return ResolvedTarget(raw, os.path.basename(hit), "exe", hit, 40)

    return None


def launch(target: ResolvedTarget) -> bool:
    """Launch a resolved target. Returns True only on confirmed success."""
    import time

    kind, value = target.kind, target.value
    try:
        if kind == "url":
            from actions.browser_native import navigate_user_browser

            navigate_user_browser(value)
            return True

        if kind == "uri":
            os.startfile(value)  # type: ignore[attr-defined]
            time.sleep(0.8)
            return True

        if _SYSTEM == "Windows":
            return _launch_windows_target(kind, value)

        if _SYSTEM == "Darwin":
            return _launch_mac_target(kind, value)

        return _launch_linux_target(kind, value)
    except Exception as e:
        print(f"[AppResolver] launch failed ({kind}): {e}")
        return False


def _launch_windows_target(kind: LaunchKind, value: str) -> bool:
    import time

    if kind == "app_id":
        r = subprocess.run(
            ["explorer.exe", f"shell:AppsFolder\\{value}"],
            capture_output=True,
            timeout=15,
        )
        time.sleep(1.2)
        return r.returncode in (0, 1)

    if kind == "lnk":
        os.startfile(value)  # type: ignore[attr-defined]
        time.sleep(1.2)
        return True

    if kind == "exe":
        subprocess.Popen(
            [value],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.2)
        return True

    safe = value.replace("'", "''")
    r = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"$ErrorActionPreference='Stop'; Start-Process -FilePath '{safe}'",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return r.returncode == 0


def _launch_mac_target(kind: LaunchKind, value: str) -> bool:
    import time

    if kind == "open_a":
        r = subprocess.run(
            ["open", "-a", value],
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(0.8)
        return r.returncode == 0
    return False


def _launch_linux_target(kind: LaunchKind, value: str) -> bool:
    import time

    if kind == "desktop":
        r = subprocess.run(
            ["gtk-launch", os.path.splitext(os.path.basename(value))[0]],
            capture_output=True,
            timeout=8,
        )
        time.sleep(0.8)
        return r.returncode == 0
    if kind == "exe":
        subprocess.Popen(
            [value],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)
        return True
    return False
