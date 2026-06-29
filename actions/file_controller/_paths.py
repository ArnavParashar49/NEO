import os
import shutil
import subprocess
import platform
from pathlib import Path
from datetime import datetime

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

_SAFE_ROOTS: list[Path] = [
    Path.home(),
]

def _is_safe_path(target: Path) -> bool:
    """Verilen path _SAFE_ROOTS içinde mi? Değilse işlemi reddet."""
    try:
        resolved = target.resolve()
        return any(
            resolved == root.resolve() or resolved.is_relative_to(root.resolve())
            for root in _SAFE_ROOTS
        )
    except Exception:
        return False

def _get_desktop() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Desktop"

def _get_downloads() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Downloads"

def _get_documents() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOCUMENTS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Documents"

def _get_pictures() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_PICTURES_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Pictures"

def _get_music() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_MUSIC_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Music"

def _get_videos() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_VIDEOS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Videos"


def _shortcuts() -> dict[str, Path]:
    return {
        "desktop":    _get_desktop(),
        "downloads":  _get_downloads(),
        "documents":  _get_documents(),
        "pictures":   _get_pictures(),
        "music":      _get_music(),
        "videos":     _get_videos(),
        "home":       Path.home(),
        "screenshots": _get_pictures() / "Screenshots",
    }


def _resolve_path(raw: str) -> Path:
    """Resolve shortcuts and nested paths like desktop/2026-05 or desktop/Images."""
    raw = (raw or "").strip()
    if not raw:
        return Path.home()

    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return expanded

    normalized = raw.strip("/\\").replace("\\", "/")
    parts = [p for p in normalized.split("/") if p and p not in (".", "..")]
    if not parts:
        return Path.home()

    shortcuts = _shortcuts()
    first = parts[0].lower()
    if first in shortcuts:
        base = shortcuts[first]
        if len(parts) == 1:
            return base
        return base.joinpath(*parts[1:])

    # Bare names like "Images" or "New Folder 1" → Desktop, never the app working directory
    if ".." not in parts:
        return _get_desktop().joinpath(*parts)

    return expanded


def _resolve_target(path: str, name: str = "") -> Path:
    """Combine path + optional name; path may already include the filename."""
    if name and ("/" in name or "\\" in name):
        return _resolve_path(name)
    if name:
        return _resolve_path(path) / name
    return _resolve_path(path)


def _resolve_file_ref(params: dict, default_base: str = "desktop") -> Path:
    """Resolve file/folder from path, source, name — model may use any combination."""
    source = (params.get("source") or "").strip()
    path = (params.get("path") or "").strip()
    name = (params.get("name") or "").strip()

    if name and ("/" in name or "\\" in name):
        return _resolve_path(name)

    base = source or path or default_base
    if name:
        resolved = _resolve_path(base)
        if resolved.is_file():
            return resolved
        return resolved / name

    return _resolve_path(base)


def _find_in_dir(directory: Path, name: str) -> Path | None:
    """Exact, case-insensitive, or stem match within a folder."""
    if not directory.is_dir():
        return None

    candidate = directory / name
    if candidate.exists():
        return candidate

    lower = name.lower()
    for item in directory.iterdir():
        if item.name.lower() == lower:
            return item

    stem = Path(name).stem.lower()
    if stem:
        matches = [
            item for item in directory.iterdir()
            if item.stem.lower() == stem or item.name.lower().startswith(stem)
        ]
        if len(matches) == 1:
            return matches[0]
        for item in matches:
            if item.name.lower() == lower or item.name.lower().startswith(stem):
                return item

    # Recursive fallback (limited to avoid hanging on massive folders)
    # We only rglob if the folder is not the absolute home root to prevent massive latency
    if directory != Path.home():
        try:
            for i, p in enumerate(directory.rglob(f"*{name}*")):
                if p.is_file() and not p.name.startswith("."):
                    return p
                if i > 2000: # break after 2000 checks
                    break
        except PermissionError:
            pass
            
    return None


def _resolve_file_ref_or_find(params: dict) -> tuple[Path | None, str]:
    """Resolve file; fuzzy-search parent folder if exact path missing."""
    src = _resolve_file_ref(params)
    if src.exists():
        return src, ""

    name = (params.get("name") or "").strip()
    parent_raw = (params.get("source") or params.get("path") or "").strip()
    if name and parent_raw and "/" not in name:
        parent = _resolve_path(parent_raw)
        found = _find_in_dir(parent, name)
        if found:
            return found, ""

    if name and not parent_raw:
        for scope in ("desktop", "downloads", "documents", "home"):
            found = _find_in_dir(_resolve_path(scope), name)
            if found:
                return found, ""

    return None, f"Source not found: {src}"


def _unique_dest(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    p = Path(name)
    stem, suffix = p.stem, p.suffix
    for i in range(1, 100):
        alt = f"{stem} ({i}){suffix}" if suffix else f"{name} ({i})"
        candidate = directory / alt
        if not candidate.exists():
            return candidate
    return directory / f"{name}_copy"

def _format_size(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def _safe_trash(target: Path) -> str:

    if not _SEND2TRASH:
        return (
            "send2trash is not installed. "
            "Run: pip install send2trash — "
            "Permanent deletion is disabled for safety."
        )
    send2trash.send2trash(str(target))
    return f"Moved to Trash: {target.name}"


