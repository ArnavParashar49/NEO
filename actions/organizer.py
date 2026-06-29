"""Folder and file organizer — sort by type/date, bulk rename, preview."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from actions.file_controller import (
    _is_safe_path,
    _resolve_path,
    _unique_dest,
)

TYPE_MAP: dict[str, set[str]] = {
    "Images":      {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
    "Documents":   {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                    ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp", ".md", ".rtf"},
    "Videos":      {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "Music":       {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
    "Archives":    {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Code":        {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                    ".cpp", ".java", ".cs", ".go", ".rs", ".sh", ".php"},
}

_SKIP_DIR_NAMES = {
    "Images", "Documents", "Videos", "Music", "Archives", "Code",
    "Others", "Executables",
}


def _folder_label(item: Path, mode: str) -> str:
    if mode == "by_date":
        mtime = datetime.fromtimestamp(item.stat().st_mtime)
        return mtime.strftime("%Y-%m")
    ext = item.suffix.lower()
    for folder, exts in TYPE_MAP.items():
        if ext in exts:
            return folder
    return "Others"


def _plan_organize(folder: Path, mode: str) -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    if not folder.is_dir():
        return moves

    for item in sorted(folder.iterdir()):
        if item.name.startswith(".") or item.is_dir():
            continue
        label = _folder_label(item, mode)
        target_dir = folder / label
        target = target_dir / item.name
        if item.parent.resolve() == target_dir.resolve():
            continue
        if target.exists():
            continue
        moves.append((item, target))
    return moves


def _preview_organize(path: str, mode: str = "by_type") -> str:
    folder = _resolve_path(path)
    if not _is_safe_path(folder):
        return f"Access denied: {folder}"
    if not folder.is_dir():
        return f"Not a folder: {folder}"

    moves = _plan_organize(folder, mode)
    if not moves:
        return f"Nothing to organize in {folder.name} ({mode})."

    lines = [f"Would move {len(moves)} file(s) in {folder.name} ({mode}):"]
    for src, dst in moves[:12]:
        lines.append(f"  {src.name} -> {dst.parent.name}/")
    if len(moves) > 12:
        lines.append(f"  ... and {len(moves) - 12} more")
    return "\n".join(lines)


def _run_organize(path: str, mode: str = "by_type") -> str:
    folder = _resolve_path(path)
    if not _is_safe_path(folder):
        return f"Access denied: {folder}"
    if not folder.is_dir():
        return f"Not a folder: {folder}"

    moves = _plan_organize(folder, mode)
    if not moves:
        return f"Nothing to organize in {folder.name}."

    moved, skipped = [], []
    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        final = dst
        if final.exists():
            final = _unique_dest(dst.parent, src.name)
            skipped.append(src.name)
        try:
            shutil.move(str(src), str(final))
            moved.append(f"{src.name} -> {final.parent.name}/")
        except Exception as e:
            skipped.append(f"{src.name} ({e})")

    result = f"Organized {folder.name} ({mode}): {len(moved)} file(s) moved."
    if moved:
        result += "\n" + "\n".join(moved[:8])
        if len(moved) > 8:
            result += f"\n... and {len(moved) - 8} more."
    if skipped:
        result += f"\n{len(skipped)} skipped."
    return result


def _plan_bulk_rename(
    folder: Path,
    *,
    mode: str,
    prefix: str = "",
    suffix: str = "",
    find: str = "",
    replace: str = "",
    start: int = 1,
    filter_ext: str = "",
) -> list[tuple[Path, str]]:
    renames: list[tuple[Path, str]] = []
    if not folder.is_dir():
        return renames

    ext_filter = filter_ext.lower().strip()
    if ext_filter and not ext_filter.startswith("."):
        ext_filter = f".{ext_filter}"

    files = sorted(
        [f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")],
        key=lambda p: p.name.lower(),
    )
    if ext_filter:
        files = [f for f in files if f.suffix.lower() == ext_filter]

    counter = start
    for item in files:
        stem, ext = item.stem, item.suffix
        if mode == "prefix":
            new_name = f"{prefix}{item.name}"
        elif mode == "suffix":
            new_name = f"{stem}{suffix}{ext}"
        elif mode == "replace":
            if not find:
                continue
            new_name = item.name.replace(find, replace)
        elif mode == "numbered":
            new_name = f"{prefix}{counter:03d}{ext}"
            counter += 1
        else:
            continue

        if new_name != item.name:
            renames.append((item, new_name))
    return renames


def _preview_rename(path: str, params: dict) -> str:
    folder = _resolve_path(path)
    if not _is_safe_path(folder):
        return f"Access denied: {folder}"

    mode = (params.get("rename_mode") or params.get("mode") or "replace").lower()
    renames = _plan_bulk_rename(
        folder,
        mode=mode,
        prefix=params.get("prefix", ""),
        suffix=params.get("suffix", ""),
        find=params.get("find", "") or params.get("old", ""),
        replace=params.get("replace", "") or params.get("new", ""),
        start=int(params.get("start", 1)),
        filter_ext=params.get("extension", "") or params.get("filter_ext", ""),
    )
    if not renames:
        return f"No renames planned in {folder.name} for mode '{mode}'."

    lines = [f"Would rename {len(renames)} file(s) in {folder.name}:"]
    for src, new_name in renames[:12]:
        lines.append(f"  {src.name} -> {new_name}")
    if len(renames) > 12:
        lines.append(f"  ... and {len(renames) - 12} more")
    return "\n".join(lines)


def _run_bulk_rename(path: str, params: dict) -> str:
    folder = _resolve_path(path)
    if not _is_safe_path(folder):
        return f"Access denied: {folder}"

    mode = (params.get("rename_mode") or params.get("mode") or "replace").lower()
    renames = _plan_bulk_rename(
        folder,
        mode=mode,
        prefix=params.get("prefix", ""),
        suffix=params.get("suffix", ""),
        find=params.get("find", "") or params.get("old", ""),
        replace=params.get("replace", "") or params.get("new", ""),
        start=int(params.get("start", 1)),
        filter_ext=params.get("extension", "") or params.get("filter_ext", ""),
    )
    if not renames:
        return f"No files to rename in {folder.name}."

    done, skipped = [], []
    for src, new_name in renames:
        target = folder / new_name
        if target.exists() and target.resolve() != src.resolve():
            skipped.append(new_name)
            continue
        try:
            src.rename(target)
            done.append(f"{src.name} -> {new_name}")
        except Exception as e:
            skipped.append(f"{src.name} ({e})")

    result = f"Renamed {len(done)} file(s) in {folder.name}."
    if done:
        result += "\n" + "\n".join(done[:10])
        if len(done) > 10:
            result += f"\n... and {len(done) - 10} more."
    if skipped:
        result += f"\n{len(skipped)} skipped (name conflict or error)."
    return result


def _handle_with_confirm(
    action_id: str,
    summary: str,
    ask: str,
    params: dict,
    run_fn,
) -> str:
    from actions import confirm_gate as cg

    proceed, stored, err = cg.consume_confirmed(params, action_id)
    if err:
        return err
    if proceed:
        merged = cg.merge_params(params, stored) if stored else params
        return run_fn(merged)

    return cg.needs_confirm(action_id, summary, dict(params), ask=ask)


def organizer_control(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "preview").lower().strip()
    path = (params.get("path") or "desktop").strip()
    mode = (params.get("organize_mode") or params.get("sort") or "by_type").lower()

    if player:
        player.write_log(f"[organizer] {action} {path}")

    try:
        if action in ("preview", "plan"):
            if params.get("rename_mode") or params.get("find") or params.get("prefix"):
                return _preview_rename(path, params)
            return _preview_organize(path, mode)

        if action in ("organize", "sort"):
            folder = _resolve_path(path)
            moves = _plan_organize(folder, mode)
            if not moves:
                return _preview_organize(path, mode)

            summary = (
                f"Organize {len(moves)} file(s) in '{folder.name}' "
                f"by {mode.replace('_', ' ')}."
            )
            return _handle_with_confirm(
                "organizer_sort",
                summary,
                f"Should I organize {len(moves)} files in {folder.name}?",
                {**params, "action": "organize", "path": path, "organize_mode": mode},
                lambda p: _run_organize(p.get("path", path), p.get("organize_mode", mode)),
            )

        if action in ("bulk_rename", "rename"):
            folder = _resolve_path(path)
            rename_mode = (params.get("rename_mode") or params.get("mode") or "replace").lower()
            if rename_mode in ("by_type", "by_date"):
                return "Use action organize for sorting. bulk_rename modes: prefix | suffix | replace | numbered."

            renames = _plan_bulk_rename(
                folder,
                mode=rename_mode,
                prefix=params.get("prefix", ""),
                suffix=params.get("suffix", ""),
                find=params.get("find", "") or params.get("old", ""),
                replace=params.get("replace", "") or params.get("new", ""),
                start=int(params.get("start", 1)),
                filter_ext=params.get("extension", "") or params.get("filter_ext", ""),
            )
            if not renames:
                return _preview_rename(path, params)

            summary = f"Rename {len(renames)} file(s) in '{folder.name}' ({rename_mode})."
            return _handle_with_confirm(
                "organizer_rename",
                summary,
                f"Should I rename {len(renames)} files in {folder.name}?",
                {**params, "action": "bulk_rename", "path": path, "rename_mode": rename_mode},
                lambda p: _run_bulk_rename(p.get("path", path), p),
            )

        if action == "list_types":
            lines = ["File type folders:"]
            for name, exts in TYPE_MAP.items():
                sample = ", ".join(sorted(exts)[:6])
                lines.append(f"  {name}: {sample}...")
            return "\n".join(lines)

        return (
            "Unknown action. Use: preview | organize | bulk_rename | list_types. "
            "Organize modes: by_type | by_date. Rename modes: prefix | suffix | replace | numbered."
        )

    except Exception as e:
        return f"Organizer error: {e}"
