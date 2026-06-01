"""Format any multi-item ARIA reply as a numbered list for the activity log."""
from __future__ import annotations

import re

_NUMBERED_LINE = re.compile(r"^\s*\d+[\.\)]\s*(.+)$")
_NUMBERED_INLINE = re.compile(
    r"(?<!\d)\d+[\.\)]\s*([^;\n]+?)(?=\s*\d+[\.\)]|\s*$)",
)
_BULLET_LINE = re.compile(r"^\s*[-•*]\s+(.+)$")
_INTRO_PREFIX = re.compile(
    r"^(?:here(?:'s| are| is)|the following|top|some|a few|"
    r"these are|i found|options|results|headlines|stories|items)[^:]*:\s*",
    re.IGNORECASE,
)


def _clean_item(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip())
    s = re.sub(r"^and\s+", "", s, flags=re.IGNORECASE)
    s = s.strip(" ,.;:-—")
    return s


def _numbered_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        inline = [m.group(1) for m in _NUMBERED_INLINE.finditer(line)]
        if inline:
            items.extend(inline)
            continue
        m = _NUMBERED_LINE.match(line.strip())
        if m:
            items.append(m.group(1))
    if not items:
        items = [m.group(1) for m in _NUMBERED_INLINE.finditer(text)]
    return [_clean_item(i) for i in items if _clean_item(i)]


def _bullet_items(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        m = _BULLET_LINE.match(line.strip())
        if m:
            items.append(_clean_item(m.group(1)))
    return [i for i in items if i]


def _split_comma_list(text: str) -> list[str]:
    text = _INTRO_PREFIX.sub("", text.strip())
    if not text:
        return []

    parts = re.split(r",\s*|\s+and\s+", text)
    items = [_clean_item(p) for p in parts if _clean_item(p)]
    if len(items) < 2:
        return []

    valid = [i for i in items if 4 <= len(i) <= 220]
    if len(valid) >= 2:
        return valid
    return []


def _semicolon_items(text: str) -> list[str]:
    if ";" not in text:
        return []
    parts = [_clean_item(p) for p in text.split(";")]
    parts = [p for p in parts if p]
    if len(parts) >= 2 and all(8 <= len(p) <= 220 for p in parts):
        return parts
    return []


def _colon_tail_items(text: str) -> list[str]:
    m = re.search(r":\s*(.+)$", text, re.DOTALL)
    if not m:
        return []
    tail = m.group(1).strip()
    for fn in (_numbered_items, _bullet_items, _semicolon_items, _split_comma_list):
        items = fn(tail)
        if len(items) >= 2:
            return items
    return []


def _sentence_items(text: str) -> list[str]:
    """Treat multiple short sentences as list items (news, facts, etc.)."""
    text = _INTRO_PREFIX.sub("", text.strip())
    if len(text.split()) < 28:
        return []
    sents = re.split(r"(?<=[.!?])\s+", text)
    sents = [_clean_item(s) for s in sents if _clean_item(s)]
    if len(sents) < 3 or len(sents) > 12:
        return []
    if any(len(s) < 15 for s in sents):
        return []
    if any(len(s) > 200 for s in sents):
        return []
    if sum(len(s) for s in sents) > 900 and len(sents) <= 3:
        return []
    return sents


_NO_LIST_PHRASES = frozenset({
    "hey! what can i do for you?",
    "one moment.",
})

_PROSE_MARKERS = re.compile(
    r"\b(i'm sorry|i am sorry|i cannot|i can't|unfortunately|however,|"
    r"not able to|don't have the ability|can't display|cannot display|"
    r"how can i help|how may i help|what can i do|thank you|you're welcome|"
    r"i'm doing well|nice to meet|hello|hi there|good morning|good evening)\b",
    re.IGNORECASE,
)


def _is_prose_not_list(text: str) -> bool:
    if _PROSE_MARKERS.search(text):
        return True
    if "**" not in text and not re.search(r"\n\s*\d+[\.\)]\s", text):
        if not re.search(r"^\s*\d+[\.\)]\s+\*\*", text, re.M):
            sents = re.split(r"(?<=[.!?])\s+", text.strip())
            if 2 <= len(sents) <= 6 and any(len(s) < 20 for s in sents):
                return True
    return False


def _newline_items(text: str) -> list[str]:
    lines = [_clean_item(ln) for ln in text.splitlines() if _clean_item(ln)]
    lines = [ln for ln in lines if not _NUMBERED_LINE.match(ln) and not _BULLET_LINE.match(ln)]
    if len(lines) >= 2 and all(len(ln) <= 220 for ln in lines):
        return lines
    return []


def extract_list_items(text: str) -> list[str]:
    """Pull discrete items from any multi-item reply."""
    text = text.strip()
    if not text:
        return []

    for fn in (
        _numbered_items,
        _bullet_items,
        _colon_tail_items,
        _semicolon_items,
        _split_comma_list,
        _newline_items,
        _sentence_items,
    ):
        items = fn(text)
        if len(items) >= 2:
            seen: set[str] = set()
            out: list[str] = []
            for item in items:
                key = item.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(item)
            if len(out) >= 2:
                return out[:12]
    return []


def format_list_for_log(text: str) -> str:
    """Return numbered list when text contains multiple items; else tidy plain text."""
    text = text.strip()
    if not text:
        return text

    if text.lower().strip().rstrip(".") in _NO_LIST_PHRASES:
        return text

    if _is_prose_not_list(text):
        lines = []
        for ln in text.splitlines():
            m = _NUMBERED_LINE.match(ln.strip())
            lines.append(m.group(1) if m else ln.strip())
        joined = " ".join(lines) if len(lines) <= 4 else "\n".join(lines)
        return joined.strip() or text

    if re.search(r"\n\s*\d+[\.\)]\s", text):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        normalized = []
        for ln in lines:
            m = _NUMBERED_LINE.match(ln)
            normalized.append(ln if m else ln)
        return "\n".join(normalized)

    items = extract_list_items(text)
    if len(items) >= 2:
        return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))

    text = re.sub(r"\s+(?=(\d{1,2})[.)]\s+)", "\n", text)
    text = re.sub(r";\s+", ";\n", text)
    text = re.sub(r"\s+[-•]\s+", "\n• ", text)
    return text.strip()
