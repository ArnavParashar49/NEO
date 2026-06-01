"""Shopping lists and todo lists — local persistent storage."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

_LISTS_PATH = Path.home() / ".aria" / "lists.json"
_VALID_LISTS = {"shopping", "todos", "todo", "grocery", "groceries"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _normalize_list(name: str) -> str:
    n = (name or "shopping").lower().strip()
    if n in ("todo", "todos", "task", "tasks"):
        return "todos"
    if n in ("grocery", "groceries", "shop", "shopping_list"):
        return "shopping"
    return n if n in ("shopping", "todos") else "shopping"


def _load() -> dict:
    if not _LISTS_PATH.exists():
        return {"shopping": [], "todos": []}
    try:
        data = json.loads(_LISTS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"shopping": [], "todos": []}
        data.setdefault("shopping", [])
        data.setdefault("todos", [])
        return data
    except Exception:
        return {"shopping": [], "todos": []}


def _save(data: dict) -> None:
    _LISTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LISTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_items(raw) -> list[str]:
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if str(x).strip()]
    else:
        text = str(raw or "").strip()
        if not text:
            return []
        for sep in ("\n", ";", "|"):
            text = text.replace(sep, ",")
        parts = [p.strip() for p in text.split(",") if p.strip()]
    return parts


def _find_item(items: list[dict], query: str) -> dict | None:
    q = query.lower().strip()
    if not q:
        return None
    for item in items:
        if item.get("id") == query:
            return item
    for item in items:
        if q in (item.get("item") or "").lower():
            return item
    return None


def _format_list(name: str, items: list[dict], *, show_done: bool = True) -> str:
    label = "Shopping list" if name == "shopping" else "Todo list"
    visible = items if show_done else [i for i in items if not i.get("done")]
    if not visible:
        return f"{label} is empty."

    lines = [f"{label} ({len(visible)} item(s)):"]
    for i, entry in enumerate(visible[:25], 1):
        mark = "✓" if entry.get("done") else "○"
        text = entry.get("item", "")
        if entry.get("done"):
            lines.append(f"  {i}. [{mark}] {text}")
        else:
            lines.append(f"  {i}. {text}")
    if len(visible) > 25:
        lines.append(f"  ... and {len(visible) - 25} more")
    pending = sum(1 for x in items if not x.get("done"))
    if pending != len(items):
        lines.append(f"  ({pending} remaining)")
    return "\n".join(lines)


def list_manager(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "list").lower().strip()
    list_name = _normalize_list(params.get("list") or params.get("list_name") or "shopping")
    item_text = (params.get("item") or params.get("text") or params.get("message") or "").strip()
    items_raw = params.get("items") or item_text

    if player:
        player.write_log(f"[lists] {action} {list_name}")

    data = _load()
    bucket = data.setdefault(list_name, [])

    if action in ("list", "show", "read"):
        show_done = not params.get("pending_only") and not params.get("active_only")
        if params.get("pending_only") or params.get("active_only"):
            return _format_list(list_name, bucket, show_done=False)
        return _format_list(list_name, bucket)

    if action in ("add", "append", "create"):
        new_items = _parse_items(items_raw)
        if not new_items:
            return "NEEDS_USER: What should I add to the list?"
        added = []
        for text in new_items:
            entry = {
                "id": uuid.uuid4().hex[:8],
                "item": text,
                "done": False,
                "added": _now(),
            }
            bucket.append(entry)
            added.append(text)
        _save(data)
        label = "shopping list" if list_name == "shopping" else "todo list"
        if len(added) == 1:
            return f"Added to {label}: {added[0]}"
        return f"Added {len(added)} items to {label}: " + ", ".join(added[:5])

    if action in ("remove", "delete"):
        query = item_text or params.get("query") or params.get("id") or ""
        if not query:
            return "NEEDS_USER: Which item should I remove?"
        target = _find_item(bucket, query)
        if not target:
            return f"No item matching '{query}' on {list_name} list."
        bucket.remove(target)
        _save(data)
        return f"Removed: {target.get('item')}"

    if action in ("check", "complete", "done", "mark_done"):
        query = item_text or params.get("query") or params.get("id") or ""
        if not query:
            return "NEEDS_USER: Which item should I mark done?"
        target = _find_item(bucket, query)
        if not target:
            return f"No item matching '{query}'."
        target["done"] = True
        target["completed"] = _now()
        _save(data)
        return f"Done: {target.get('item')}"

    if action in ("uncheck", "undo", "mark_undone"):
        query = item_text or params.get("query") or ""
        if not query:
            return "NEEDS_USER: Which item should I uncheck?"
        target = _find_item(bucket, query)
        if not target:
            return f"No item matching '{query}'."
        target["done"] = False
        target.pop("completed", None)
        _save(data)
        return f"Unchecked: {target.get('item')}"

    if action == "clear":
        if not bucket:
            return f"{list_name.capitalize()} list is already empty."
        count = len(bucket)
        bucket.clear()
        _save(data)
        return f"Cleared {count} item(s) from {list_name} list."

    if action == "clear_done":
        before = len(bucket)
        data[list_name] = [i for i in bucket if not i.get("done")]
        removed = before - len(data[list_name])
        _save(data)
        return f"Removed {removed} completed item(s)."

    return (
        "Unknown action. Use: list | add | remove | check | uncheck | clear | clear_done. "
        "Lists: shopping | todos."
    )
