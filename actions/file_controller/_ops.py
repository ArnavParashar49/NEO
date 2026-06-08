"""file_controller operations — split out of the original monolith.

Path resolution/safety helpers live in _paths; this holds the actual ops.
"""

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from actions.file_controller._paths import (
    _OS, _SAFE_ROOTS, _SEND2TRASH,
    _is_safe_path, _get_desktop, _get_downloads, _get_documents,
    _get_pictures, _get_music, _get_videos, _shortcuts, _resolve_path,
    _resolve_target, _resolve_file_ref, _find_in_dir, _resolve_file_ref_or_find,
    _unique_dest, _format_size, _safe_trash,
)


def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    try:
        target = _resolve_path(path)
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Path not found: {path}"
        if not target.is_dir():
            return f"Not a directory: {target}"

        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = _format_size(item.stat().st_size)
                items.append(f"📄 {item.name} ({size})")

        if not items:
            return f"Directory is empty: {target}/"

        rel = target
        try:
            rel = target.relative_to(Path.home())
            label = f"~/{rel}"
        except ValueError:
            label = str(target)

        return f"Contents of {label} ({len(items)} items):\n" + "\n".join(items)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Error listing files: {e}"


def create_file(path: str, name: str = "", content: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"File created: {target.name}"
    except Exception as e:
        return f"Could not create file: {e}"


def create_folder(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        target.mkdir(parents=True, exist_ok=True)
        return f"Folder created: {target.name}"
    except Exception as e:
        return f"Could not create folder: {e}"


def _handle_delete_with_confirm(params: dict, path: str, name: str) -> str:
    from actions import confirm_gate as cg

    proceed, stored, err = cg.consume_confirmed(params, "file_delete")
    if err:
        return err
    if proceed:
        p = cg.merge_params(params, stored) if stored else params
        return delete_file(p.get("path", path), name=p.get("name", name))

    try:
        target = _resolve_target(path, name)
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        kind = "empty folder" if target.is_dir() else "file"
        return cg.needs_confirm(
            "file_delete",
            f"Move {kind} '{target.name}' to Trash?",
            {"action": "delete", "path": path, "name": name},
            ask=f"Should I delete {target.name}? It will go to Trash.",
        )
    except Exception as e:
        return f"Could not prepare delete: {e}"


def _handle_merge_with_confirm(params: dict) -> str:
    from actions import confirm_gate as cg

    source = params.get("source") or params.get("path", "desktop")
    destination = (params.get("destination") or "").strip()
    remove_source = bool(params.get("remove_source", True))

    if not destination:
        return "NEEDS_USER: Which folder should I merge into? Set destination."

    proceed, stored, err = cg.consume_confirmed(params, "merge_folders")
    if err:
        return err
    if proceed:
        p = cg.merge_params(params, stored) if stored else params
        return merge_folders(
            source=p.get("source") or p.get("path", "desktop"),
            destination=p.get("destination", ""),
            remove_source=bool(p.get("remove_source", True)),
        )

    if not remove_source:
        return merge_folders(source=source, destination=destination, remove_source=False)

    try:
        src = _resolve_path(source)
        dst = _resolve_path(destination)
        if not src.exists():
            return f"FAILED: Source folder not found: {source}"
        item_count = len([x for x in src.iterdir() if not x.name.startswith(".")])
        dst_label = dst.name if dst.exists() else destination.split("/")[-1]
        return cg.needs_confirm(
            "merge_folders",
            (
                f"Merge {item_count} item(s) from '{src.name}' into '{dst_label}', "
                "then remove the empty source folder."
            ),
            {
                "action": "merge_folders",
                "source": source,
                "destination": destination,
                "remove_source": True,
            },
            ask=f"Should I merge {src.name} into {dst_label}?",
        )
    except Exception as e:
        return f"Could not prepare merge: {e}"


def delete_file(path: str, name: str = "", force: bool = False) -> str:
    try:
        target = _resolve_target(path, name)
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        protected = {
            _get_desktop(), _get_downloads(), _get_documents(),
            _get_pictures(), _get_music(), _get_videos(), Path.home(),
        }
        if target.resolve() in {p.resolve() for p in protected}:
            return f"Protected directory, cannot delete: {target.name}"

        if target.is_dir():
            visible = [x for x in target.iterdir() if not x.name.startswith(".")]
            if visible:
                return (
                    f"FAILED: Folder '{target.name}' has {len(visible)} item(s). "
                    "Use merge_folders to move contents first — will not delete a non-empty folder."
                )
            target.rmdir()
            return f"Removed empty folder: {target.name}"

        return _safe_trash(target)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Could not delete: {e}"


def merge_folders(
    source: str,
    destination: str,
    remove_source: bool = True,
) -> str:
    """Move all items from source folder into destination. Optionally remove empty source."""
    try:
        src = _resolve_path(source)
        dst = _resolve_path(destination)

        if not src.exists():
            return f"FAILED: Source folder not found: {source}"
        if not src.is_dir():
            return f"FAILED: Source is not a folder: {source}"
        if not _is_safe_path(src):
            return f"Access denied (source): {src}"
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"

        if src.resolve() == dst.resolve():
            return "FAILED: Source and destination are the same folder."

        dst.mkdir(parents=True, exist_ok=True)

        moved: list[str] = []
        renamed: list[str] = []

        for item in sorted(src.iterdir()):
            if item.name.startswith("."):
                continue

            target = dst / item.name
            if target.exists():
                if item.is_dir() and target.is_dir():
                    sub = merge_folders(str(item), str(target), remove_source=True)
                    if sub.startswith("FAILED"):
                        return sub
                    moved.append(f"{item.name}/ (merged into existing folder)")
                    continue
                target = _unique_dest(dst, item.name)
                renamed.append(f"{item.name} → {target.name}")

            shutil.move(str(item), str(target))
            moved.append(f"{item.name} → {dst.name}/")

        remaining = [x for x in src.iterdir() if not x.name.startswith(".")]
        if remove_source:
            if remaining:
                return (
                    f"FAILED: Merge incomplete — {len(remaining)} item(s) still in {src.name}. "
                    f"Moved {len(moved)} item(s). Source folder was NOT deleted."
                )
            try:
                src.rmdir()
            except Exception as e:
                return (
                    f"Merged {len(moved)} item(s) into {dst.name}/ but could not remove "
                    f"empty source folder: {e}"
                )

        summary = f"Merged {len(moved)} item(s) from {src.name} into {dst.name}/."
        if renamed:
            summary += f" Renamed {len(renamed)} conflict(s): " + ", ".join(renamed[:5])
        if remove_source:
            summary += f" Removed empty folder {src.name}."
        return summary

    except Exception as e:
        return f"FAILED: Could not merge folders — {e}"


def _parse_destinations(params: dict) -> list[str]:
    out: list[str] = []
    for key in ("destination", "destination2", "destination3"):
        val = (params.get(key) or "").strip()
        if val:
            out.append(val)
    raw = params.get("destinations")
    if raw:
        if isinstance(raw, list):
            out.extend(str(x).strip() for x in raw if str(x).strip())
        else:
            out.extend(x.strip() for x in str(raw).split(",") if x.strip())
    return out


def _list_moveable_files(src: Path, *, skip_under: Path | None = None) -> list[Path]:
    """Top-level files in src; optionally skip anything inside skip_under."""
    skip_res = skip_under.resolve() if skip_under else None
    out: list[Path] = []
    for item in src.iterdir():
        if not item.is_file() or item.name.startswith("."):
            continue
        if skip_res:
            try:
                if item.resolve() == skip_res:
                    continue
            except OSError:
                pass
        out.append(item)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def move_all_files(
    source: str,
    destination: str,
    *,
    include_folders: bool = False,
) -> str:
    """Move every top-level item from source into one destination folder."""
    try:
        src = _resolve_path(source)
        if not src.exists() or not src.is_dir():
            return f"FAILED: Source folder not found: {source}"
        if not _is_safe_path(src):
            return f"Access denied: {src}"

        dst = _resolve_path(destination)
        if not _is_safe_path(dst):
            return f"Access denied: {dst}"
        dst.mkdir(parents=True, exist_ok=True)
        if not dst.is_dir():
            return f"FAILED: Destination is not a folder: {destination}"

        try:
            if dst.resolve() == src.resolve():
                return "FAILED: Source and destination are the same folder."
            if dst.resolve().is_relative_to(src.resolve()):
                pass  # dest subfolder of src — allowed
            elif src.resolve().is_relative_to(dst.resolve()):
                return "FAILED: Cannot move a folder into its own subfolder."
        except (OSError, ValueError):
            pass

        skip_under = dst if dst.resolve().is_relative_to(src.resolve()) else None
        moved: list[str] = []
        skipped: list[str] = []

        for item in sorted(src.iterdir(), key=lambda p: p.name.lower()):
            if item.name.startswith("."):
                continue
            if skip_under:
                try:
                    if item.resolve() == skip_under.resolve():
                        continue
                except OSError:
                    pass
            if item.is_dir():
                if not include_folders:
                    continue
            elif not item.is_file():
                continue

            target = dst / item.name
            if target.exists():
                if item.is_dir():
                    skipped.append(f"{item.name}/ (folder already exists)")
                    continue
                target = _unique_dest(dst, item.name)
            try:
                shutil.move(str(item), str(target))
                moved.append(f"{item.name} → {dst.name}/")
            except Exception as e:
                skipped.append(f"{item.name} ({e})")

        if not moved and not skipped:
            return f"No files to move in {src.name}/ (folders are skipped unless include_folders is true)."

        summary = f"Moved {len(moved)} item(s) from {src.name}/ to {dst.name}/."
        if skipped:
            summary += f" Skipped {len(skipped)}: " + "; ".join(skipped[:5])
            if len(skipped) > 5:
                summary += f" (+{len(skipped) - 5} more)"
        left_files = _list_moveable_files(src, skip_under=skip_under)
        if left_files:
            summary += f" {len(left_files)} file(s) still in {src.name}/."
        if len(moved) <= 10:
            summary += "\n" + "\n".join(moved)
        else:
            summary += "\n" + "\n".join(moved[:10]) + f"\n... and {len(moved) - 10} more."
        return summary

    except Exception as e:
        return f"FAILED: Could not move all files — {e}"


def distribute_files(
    source: str,
    destinations: list[str],
    count: int = 3,
) -> str:
    """Move files from source into destination folder(s). One destination + count 0 = move all."""
    try:
        src = _resolve_path(source)
        if not src.exists() or not src.is_dir():
            return f"FAILED: Source folder not found: {source}"
        if not _is_safe_path(src):
            return f"Access denied: {src}"

        dest_paths: list[Path] = []
        for d in destinations:
            if not d:
                continue
            dp = _resolve_path(d)
            if not _is_safe_path(dp):
                return f"Access denied: {dp}"
            dp.mkdir(parents=True, exist_ok=True)
            dest_paths.append(dp)

        if not dest_paths:
            return "FAILED: No destination folder(s) specified."

        skip_under = None
        if len(dest_paths) == 1:
            try:
                if dest_paths[0].resolve().is_relative_to(src.resolve()):
                    skip_under = dest_paths[0]
            except (OSError, ValueError):
                pass

        files = _list_moveable_files(src, skip_under=skip_under)
        if not files:
            return f"No files to move in {src.name}/."

        raw_count = int(count) if count is not None else 3
        if len(dest_paths) == 1 and raw_count <= 0:
            return move_all_files(source, str(destinations[0]))

        per_dest = max(1, min(raw_count if raw_count > 0 else 3, 20))
        if len(dest_paths) == 1:
            per_dest = min(per_dest, len(files))

        total_needed = per_dest * len(dest_paths)
        if len(files) < total_needed:
            return (
                f"FAILED: Only {len(files)} file(s) in {src.name}, "
                f"need {total_needed} ({per_dest} per folder × {len(dest_paths)} folders)."
            )

        moved: list[str] = []
        idx = 0
        for dest in dest_paths:
            for _ in range(per_dest):
                f = files[idx]
                idx += 1
                target = dest / f.name
                if target.exists():
                    target = _unique_dest(dest, f.name)
                shutil.move(str(f), str(target))
                moved.append(f"{f.name} → {dest.name}/")

        left = _list_moveable_files(src, skip_under=skip_under)
        summary = f"Moved {len(moved)} file(s) from {src.name}/."
        if left:
            summary += f" {len(left)} file(s) remain in {src.name}/."
        if len(moved) <= 8:
            summary += "\n" + "\n".join(moved)
        else:
            summary += "\n" + "\n".join(moved[:8]) + f"\n... and {len(moved) - 8} more."
        return summary

    except Exception as e:
        return f"FAILED: Could not distribute files — {e}"


def move_file(path: str = "", name: str = "", destination: str = "", params: dict | None = None) -> str:
    try:
        if params:
            src, err = _resolve_file_ref_or_find(params)
            if err:
                return err
            assert src is not None
        else:
            src = _resolve_target(path, name)

        dst = _resolve_path(destination) if destination else None

        if not src.exists():
            return f"Source not found: {src}"
        if src.is_dir():
            return (
                f"FAILED: '{src.name}' is a folder — cannot move with move. "
                "Use distribute_files for files inside it, or merge_folders to combine folders."
            )
        if dst is None:
            return "No destination specified."
        if not _is_safe_path(src):
            return f"Access denied (source): {src}"
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"

        if dst.is_dir() or not dst.suffix:
            dst.mkdir(parents=True, exist_ok=True)
            final = dst / src.name
            if final.exists():
                final = _unique_dest(dst, src.name)
        else:
            final = dst

        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(final))
        return f"Moved: {src.name} → {final.parent.name}/"

    except Exception as e:
        return f"Could not move: {e}"


def copy_file(path: str = "", name: str = "", destination: str = "", params: dict | None = None) -> str:
    try:
        if params:
            src, err = _resolve_file_ref_or_find(params)
            if err:
                return err
            assert src is not None
        else:
            src = _resolve_target(path, name)

        dst = _resolve_path(destination) if destination else None

        if not src.exists():
            return f"Source not found: {src}"
        if dst is None:
            return "No destination specified."
        if not _is_safe_path(src):
            return f"Access denied (source): {src}"
        if not _is_safe_path(dst):
            return f"Access denied (destination): {dst}"

        if dst.is_dir() or not dst.suffix:
            dst.mkdir(parents=True, exist_ok=True)
            final = dst / src.name
            if final.exists():
                final = _unique_dest(dst, src.name)
        else:
            final = dst

        final.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            shutil.copytree(str(src), str(final))
        else:
            shutil.copy2(str(src), str(final))

        return f"Copied: {src.name} → {final.parent.name}/"

    except Exception as e:
        return f"Could not copy: {e}"


def rename_file(path: str, name: str = "", new_name: str = "") -> str:
    try:
        base     = _resolve_path(path)
        target   = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"
        if not new_name:
            return "No new name provided."

        new_path = target.parent / new_name
        if new_path.exists():
            return f"A file named '{new_name}' already exists here."

        target.rename(new_path)
        return f"Renamed: {target.name} → {new_name}"

    except Exception as e:
        return f"Could not rename: {e}"


def read_file(path: str, name: str = "", max_chars: int = 4000) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"File not found: {target.name}"
        if not target.is_file():
            return f"Not a file: {target.name}"

        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Truncated — {len(content)} total chars]"
        return content

    except Exception as e:
        return f"Could not read file: {e}"


def write_file(path: str, name: str = "", content: str = "",
               append: bool = False) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(content)
        action = "Appended to" if append else "Written to"
        return f"{action}: {target.name}"
    except Exception as e:
        return f"Could not write file: {e}"


def find_files(name: str = "", extension: str = "",
               path: str = "home", max_results: int = 20) -> str:
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Search path not found: {path}"

        if name and _OS == "Darwin" and shutil.which("mdfind"):
            spotlight = _spotlight_find(name, search_path, max_results)
            if spotlight:
                lines = []
                for p in spotlight:
                    size = _format_size(p.stat().st_size) if p.is_file() else "folder"
                    lines.append(f"📄 {p.name} ({size}) — {p.parent}")
                return f"Found {len(lines)} file(s) via Spotlight:\n" + "\n".join(lines)

        results    = []
        dir_count  = 0
        max_dirs   = 500  # performans + güvenlik limiti

        for item in search_path.rglob("*"):
            if item.is_dir():
                dir_count += 1
                if dir_count > max_dirs:
                    break
                continue
            if not item.is_file():
                continue
            if extension and item.suffix.lower() != extension.lower():
                continue
            if name and name.lower() not in item.name.lower():
                continue
            size = _format_size(item.stat().st_size)
            results.append(f"📄 {item.name} ({size}) — {item.parent}")
            if len(results) >= max_results:
                break

        if not results:
            query = name or extension or "files"
            return f"No {query} found in {search_path.name}/"

        return f"Found {len(results)} file(s):\n" + "\n".join(results)

    except Exception as e:
        return f"Search error: {e}"


def _spotlight_find(name: str, search_root: Path, max_results: int = 10) -> list[Path]:
    """Fast Spotlight search (macOS mdfind) under search_root."""
    if _OS != "Darwin" or not shutil.which("mdfind"):
        return []

    root = search_root.expanduser().resolve()
    if not root.exists():
        return []

    safe = name.replace('"', "").replace("\\", "").strip()
    if not safe:
        return []

    # Prefer exact filename, then wildcard
    queries = [
        f'kMDItemFSName == "{safe}"cd',
        f'kMDItemFSName == "*{safe}*"cd',
        safe,
    ]

    seen: set[str] = set()
    hits: list[tuple[int, Path]] = []

    for q in queries:
        try:
            r = subprocess.run(
                ["mdfind", "-onlyin", str(root), q],
                capture_output=True,
                text=True,
                timeout=12,
            )
        except Exception:
            continue

        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            p = Path(line)
            if not p.exists() or not _is_safe_path(p):
                continue
            score = 0
            if p.name.lower() == safe.lower():
                score += 100
            elif safe.lower() in p.name.lower():
                score += 50
            if p.suffix:
                score += 5
            try:
                score += min(int(p.stat().st_mtime) // 1_000_000, 50)
            except Exception:
                pass
            hits.append((score, p))
            if len(hits) >= max_results * 3:
                break
        if hits:
            break

    hits.sort(key=lambda x: (-x[0], x[1].name.lower()))
    return [p for _, p in hits[:max_results]]


def _pick_best_match(candidates: list[Path], name: str) -> Path | None:
    if not candidates:
        return None
    exact = [p for p in candidates if p.name.lower() == name.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return sorted(exact, key=lambda p: len(str(p)))[0]
    return candidates[0]


def open_file(
    path: str = "",
    name: str = "",
    app: str = "",
    search_path: str = "home",
) -> str:
    """Open a file by path or Spotlight search by name."""
    try:
        target: Path | None = None

        if path:
            target = _resolve_path(path)
            if name and target.is_dir():
                target = target / name
        elif name:
            root = _resolve_path(search_path)
            if not _is_safe_path(root):
                return f"Access denied: {root}"
            candidates = _spotlight_find(name, root, 8) if _OS == "Darwin" else []
            if not candidates:
                # Fallback: slow rglob under home shortcuts
                found = find_files(name=name, path=search_path, max_results=5)
                if found.startswith("Found"):
                    first_line = found.split("\n", 2)[1] if "\n" in found else ""
                    # Extract path from " — /path/to/parent" suffix
                    if " — " in first_line:
                        parent = Path(first_line.rsplit(" — ", 1)[-1].strip())
                        fname = first_line.split(" ", 1)[1].split(" (", 1)[0]
                        if fname.startswith("📄 "):
                            fname = fname[2:]
                        candidate = parent / fname
                        if candidate.exists():
                            candidates = [candidate]
                if not candidates:
                    return f"FAILED: No file found matching '{name}'."
            target = _pick_best_match(candidates, name)
        else:
            return "Give me a file path or name to open."

        if target is None or not target.exists():
            return f"FAILED: File not found: {name or path}"

        if not _is_safe_path(target):
            return f"Access denied: {target}"

        cmd = ["open", str(target)]
        if app:
            cmd = ["open", "-a", app, str(target)]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            return f"FAILED: Could not open {target.name} — {err}"

        via = "Spotlight" if name and not path else "path"
        app_note = f" in {app}" if app else ""
        return f"Opened {target.name}{app_note} ({via})."

    except Exception as e:
        return f"FAILED: Could not open file — {e}"


def open_recent_screenshot(search_path: str = "desktop") -> str:
    """Find and open the newest screenshot on desktop (including subfolders)."""
    try:
        root = _resolve_path(search_path)
        if not _is_safe_path(root) or not root.exists():
            return f"Path not found: {search_path}"

        patterns = ("Screenshot*.png", "Screenshot*.jpg", "Screen Shot*.png")
        candidates: list[Path] = []

        def _collect(folder: Path, depth: int = 0):
            if depth > 3:
                return
            for pat in patterns:
                candidates.extend(folder.glob(pat))
            if depth < 3:
                for sub in folder.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        _collect(sub, depth + 1)

        _collect(root)

        if not candidates:
            return open_file(name="Screenshot", search_path=search_path)

        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        cmd = ["open", str(latest)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            return f"FAILED: Could not open {latest.name} — {err}"
        return f"Opened most recent screenshot: {latest.name}"

    except Exception as e:
        return f"FAILED: Could not find screenshot — {e}"


def get_largest_files(path: str = "downloads", count: int = 10) -> str:
    count = min(count, 50)  # maksimum 50
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Path not found: {path}"

        files = []
        for item in search_path.rglob("*"):
            if item.is_file():
                try:
                    files.append((item.stat().st_size, item))
                except Exception:
                    continue

        files.sort(reverse=True)
        top = files[:count]

        if not top:
            return "No files found."

        lines = [f"Top {len(top)} largest files in {search_path.name}/:"]
        for size, f in top:
            lines.append(f"  {_format_size(size):>10}  {f.name}  ({f.parent})")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


def get_disk_usage(path: str = "home") -> str:
    try:
        target = _resolve_path(path)
        usage  = shutil.disk_usage(target)
        pct    = usage.used / usage.total * 100
        return (
            f"Disk usage ({target}):\n"
            f"  Total : {_format_size(usage.total)}\n"
            f"  Used  : {_format_size(usage.used)} ({pct:.1f}%)\n"
            f"  Free  : {_format_size(usage.free)}"
        )
    except Exception as e:
        return f"Could not get disk usage: {e}"


def organize_desktop() -> str:
    type_map = {
        "Images":    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
        "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                      ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
        "Videos":    {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
        "Music":     {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
        "Archives":  {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
        "Code":      {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                      ".cpp", ".java", ".cs", ".go", ".rs", ".sh"},
    }

    desktop = _get_desktop()
    moved, skipped = [], []

    try:
        for item in desktop.iterdir():
            # Klasörlere, gizli dosyalara ve organize klasörlerine dokunma
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.name in {k for k in type_map}:
                continue

            ext        = item.suffix.lower()
            target_dir = desktop / "Others"
            for folder, exts in type_map.items():
                if ext in exts:
                    target_dir = desktop / folder
                    break

            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name

            if new_path.exists():
                skipped.append(item.name)
                continue

            shutil.move(str(item), str(new_path))
            moved.append(f"{item.name} → {target_dir.name}/")

        result = f"Desktop organized: {len(moved)} files moved."
        if moved:
            preview = moved[:8]
            result += "\n" + "\n".join(preview)
            if len(moved) > 8:
                result += f"\n... and {len(moved) - 8} more."
        if skipped:
            result += f"\n{len(skipped)} file(s) skipped (name conflict)."
        return result

    except Exception as e:
        return f"Could not organize desktop: {e}"


def get_file_info(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        stat = target.stat()
        info = {
            "Name":      target.name,
            "Type":      "Folder" if target.is_dir() else "File",
            "Size":      _format_size(stat.st_size),
            "Location":  str(target.parent),
            "Created":   datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            "Modified":  datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "Extension": target.suffix or "—",
        }
        return "\n".join(f"  {k}: {v}" for k, v in info.items())

    except Exception as e:
        return f"Could not get file info: {e}"

