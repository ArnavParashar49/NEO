"""Shared product / phone name extraction for logs and visual panels."""
from __future__ import annotations

import re

# Laptops + phones + common product lines
_BRAND_MODEL_RE = re.compile(
    r"\b("
    r"(?:Google\s+Pixel(?:\s*\d+)?[\w\s\-]*|"
    r"OnePlus\s+\d+[\w\s\-]*|"
    r"Samsung\s+Galaxy\s+[\w\s\-]+|"
    r"Vivo\s+[\w]+\s*[\d\w\s\-]*|"
    r"iQOO\s+\d+[\w\s\-]*|"
    r"Motorola\s+Edge[\w\s\-]*|"
    r"Realme\s+\d+[\w\s\-]*|"
    r"Xiaomi\s+\d+[\w\s\-]*|"
    r"Redmi\s+[\w\s\-]+|"
    r"Poco\s+[\w\s\-]+|"
    r"Apple\s+iPhone\s+\d+[\w\s\-]*|"
    r"Nothing\s+Phone[\w\s\-]*|"
    r"Acer\s+Nitro[\w\s\-]*|"
    r"HP\s+Victus[\w\s\-]*|"
    r"Lenovo\s+(?:LOQ|IdeaPad|Legion|ThinkPad)[\w\s\-]*|"
    r"ASUS\s+(?:ROG|TUF|VivoBook|Zenbook)[\w\s\-]*|"
    r"Dell\s+(?:Inspiron|XPS|Alienware)[\w\s\-]*|"
    r"MSI\s+[\w\s\-]+|"
    r"MacBook\s+[\w\s\-]+)"
    r")",
    re.IGNORECASE,
)

_NUMBERED_LINE = re.compile(r"^\s*\d+[\.\)]\s*(.+)$")
_NUMBERED_INLINE = re.compile(
    r"(?<!\d)\d+[\.\)]\s*([^;\n]+?)(?=\s*\d+[\.\)]|\s*$)",
)


def _clean_item(raw: str) -> str:
    s = raw.strip()
    for sep in (" — ", " – ", " - ", ":", "|"):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    s = re.sub(r"\s+", " ", s).strip(" ,.;:")
    return s


def _numbered_items(line: str) -> list[str]:
    inline = [m.group(1) for m in _NUMBERED_INLINE.finditer(line)]
    if inline:
        return inline
    m = _NUMBERED_LINE.match(line.strip())
    if m:
        return [m.group(1)]
    return []


def extract_product_names(text: str, n: int = 8) -> list[str]:
    """Pull product names from numbered lists or brand/model patterns."""
    names: list[str] = []
    seen: set[str] = set()
    numbered: list[str] = []

    def _add(raw: str):
        name = _clean_item(raw)
        key = name.lower()
        if len(name) > 3 and key not in seen:
            seen.add(key)
            names.append(name)

    for line in text.splitlines():
        numbered.extend(_numbered_items(line))

    if not numbered:
        numbered.extend(m.group(1) for m in _NUMBERED_INLINE.finditer(text))

    for raw in numbered:
        _add(raw)

    if len(names) >= 2:
        return names[:n]

    for hit in _BRAND_MODEL_RE.finditer(text):
        _add(hit.group(1))
        if len(names) >= n:
            break

    return names[:n]
