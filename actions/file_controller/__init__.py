"""File operations tool. Split into _paths (resolution + safety) and _ops
(the operations); this package re-exports both and defines the dispatcher.

Public entry: file_controller(...). Several private helpers (_resolve_path,
_is_safe_path, _resolve_target, _unique_dest) are imported by sibling action
modules, so they are re-exported here for backward compatibility.
"""

from actions.file_controller._paths import (
    _OS, _SAFE_ROOTS, _SEND2TRASH,
    _is_safe_path, _get_desktop, _get_downloads, _get_documents,
    _get_pictures, _get_music, _get_videos, _shortcuts, _resolve_path,
    _resolve_target, _resolve_file_ref, _find_in_dir, _resolve_file_ref_or_find,
    _unique_dest, _format_size, _safe_trash,
)
from actions.file_controller._ops import (
    list_files, create_file, create_folder,
    _handle_delete_with_confirm, _handle_merge_with_confirm,
    delete_file, merge_folders, _parse_destinations, _list_moveable_files,
    move_all_files, distribute_files, move_file, copy_file, rename_file,
    read_file, write_file, find_files, _spotlight_find, _pick_best_match,
    open_file, open_recent_screenshot, get_largest_files, get_disk_usage,
    organize_desktop, get_file_info,
)


def file_controller(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    path   = params.get("path", "desktop")
    name   = params.get("name", "")

    if player:
        player.write_log(f"[file] {action} {name or path}")

    try:
        if action == "list":
            return list_files(path)

        elif action == "create_file":
            return create_file(path, name=name, content=params.get("content", ""))

        elif action == "create_folder":
            return create_folder(path, name=name)

        elif action == "delete":
            return _handle_delete_with_confirm(params, path, name)

        elif action == "merge_folders":
            return _handle_merge_with_confirm(params)

        elif action == "distribute_files":
            dests = _parse_destinations(params)
            raw_count = params.get("count")
            if len(dests) == 1 and raw_count is None:
                count = 0
            else:
                count = int(raw_count if raw_count is not None else 3)
            return distribute_files(
                source=params.get("source") or path,
                destinations=dests,
                count=count,
            )

        elif action == "move_all":
            dest = (params.get("destination") or "").strip()
            if not dest:
                return "FAILED: destination required for move_all."
            return move_all_files(
                source=params.get("source") or path,
                destination=dest,
                include_folders=bool(params.get("include_folders", False)),
            )

        elif action == "move":
            return move_file(
                destination=params.get("destination", ""),
                params=params,
            )

        elif action == "copy":
            return copy_file(
                destination=params.get("destination", ""),
                params=params,
            )

        elif action == "rename":
            return rename_file(path, name=name, new_name=params.get("new_name", ""))

        elif action == "read":
            return read_file(path, name=name)

        elif action == "write":
            return write_file(
                path, name=name,
                content=params.get("content", ""),
                append=params.get("append", False)
            )

        elif action == "find":
            return find_files(
                name=name or params.get("name", ""),
                extension=params.get("extension", ""),
                path=path,
                max_results=min(int(params.get("max_results", 20)), 50),
            )

        elif action == "open":
            if params.get("recent") or params.get("kind") == "screenshot":
                return open_recent_screenshot(
                    search_path=params.get("search_path") or path or "desktop"
                )
            raw_path = (params.get("path") or path or "home").lower().strip()
            shortcuts = {
                "desktop", "downloads", "documents", "home",
                "pictures", "music", "videos",
            }
            fname = name or params.get("name", "")
            if raw_path in shortcuts and fname:
                return open_file(
                    name=fname,
                    app=params.get("app", ""),
                    search_path=raw_path,
                )
            return open_file(
                path=params.get("path") or path,
                name=fname,
                app=params.get("app", ""),
                search_path="home",
            )

        elif action == "largest":
            return get_largest_files(
                path=path,
                count=int(params.get("count", 10)),
            )

        elif action == "disk_usage":
            return get_disk_usage(path)

        elif action == "organize_desktop":
            return organize_desktop()

        elif action == "info":
            return get_file_info(path, name=name)

        else:
            return f"Unknown action: '{action}'"

    except Exception as e:
        return f"File controller error ({action}): {e}"