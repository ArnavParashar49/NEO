"""Claude-style content blocks — code, commands, sources."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui_theme import C, ghost_action_button_stylesheet, mono_font


def _mono(size: int = 10) -> QFont:
    from ui_theme import _MONO_FONT
    f = QFont(_MONO_FONT, size)
    return f


def _ui(size: int = 10, bold: bool = False) -> QFont:
    from ui_theme import _UI_FONT
    f = QFont(_UI_FONT, size)
    f.setBold(bold)
    return f


_BLOCK_STYLE = f"""
    QFrame#contentBlock {{
        background: {C.SURFACE2};
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
    }}
"""


class _CopyIconButton(QPushButton):
    def __init__(self, text_provider, parent=None):
        super().__init__("⎘", parent)
        self._text_provider = text_provider
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Copy")
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C.TEXT_MED};
                border: none;
                border-radius: 6px;
                font-size: 13pt;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.06);
                color: {C.TEXT};
            }}
        """)
        self.clicked.connect(self._copy)

    def _copy(self) -> None:
        text = self._text_provider() if callable(self._text_provider) else self._text_provider
        if text:
            QApplication.clipboard().setText(text)


class CodeBlock(QFrame):
    def __init__(self, code: str, language: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("contentBlock")
        self.setStyleSheet(_BLOCK_STYLE)
        self._code = code

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(12, 8, 8, 4)
        lang = (language or "text").lower()
        lang_lbl = QLabel(lang)
        lang_lbl.setFont(_mono(9))
        lang_lbl.setStyleSheet(f"""
            color: {C.TEXT_DIM};
            background: rgba(255, 255, 255, 0.06);
            border-radius: 6px;
            padding: 2px 8px;
        """)
        header.addWidget(lang_lbl)
        header.addStretch(1)
        header.addWidget(_CopyIconButton(lambda: self._code))
        root.addLayout(header)

        body = QLabel(code.rstrip())
        body.setFont(_mono(11))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(
            f"color: {C.TEXT}; background: transparent; padding: 4px 14px 12px 14px;"
        )
        root.addWidget(body)


class CommandBlock(QFrame):
    """Shell command or tool invocation."""

    def __init__(self, command: str, *, label: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("contentBlock")
        self.setStyleSheet(_BLOCK_STYLE)
        self._command = command

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.setContentsMargins(12, 8, 8, 4)
        tag = QLabel(label or "command")
        tag.setFont(_mono(9))
        tag.setStyleSheet(f"""
            color: {C.TEXT_DIM};
            background: rgba(255, 255, 255, 0.06);
            border-radius: 6px;
            padding: 2px 8px;
        """)
        header.addWidget(tag)
        header.addStretch(1)
        header.addWidget(_CopyIconButton(lambda: self._command))
        root.addLayout(header)

        body = QLabel(command.rstrip())
        body.setFont(_mono(11))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(
            f"color: {C.GREEN}; background: transparent; padding: 4px 14px 12px 14px;"
        )
        root.addWidget(body)


class SourcesBlock(QFrame):
    def __init__(self, sources: list[dict], parent=None):
        super().__init__(parent)
        self.setObjectName("contentBlock")
        self.setStyleSheet(_BLOCK_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        title = QLabel(f"{len(sources)} source{'s' if len(sources) != 1 else ''} found")
        title.setFont(_ui(10, bold=True))
        title.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        root.addWidget(title)

        for i, src in enumerate(sources):
            row = QHBoxLayout()
            row.setSpacing(8)
            icon = src.get("icon") or _favicon_emoji(src.get("url", ""), i)
            icon_lbl = QLabel(icon)
            icon_lbl.setFixedWidth(20)
            icon_lbl.setStyleSheet("background: transparent;")
            row.addWidget(icon_lbl)

            name = src.get("title") or src.get("url", "Source")
            name_lbl = QLabel(name)
            name_lbl.setFont(_ui(10))
            name_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            name_lbl.setWordWrap(True)
            row.addWidget(name_lbl, stretch=1)

            if src.get("url"):
                link = QLabel("↗")
                link.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
                row.addWidget(link)
            root.addLayout(row)


def _favicon_emoji(url: str, index: int) -> str:
    domain = urlparse(url).netloc.lower() if url else ""
    if "stackoverflow" in domain:
        return "🟧"
    if "github" in domain:
        return "⬛"
    if "google" in domain:
        return "🔵"
    if "wikipedia" in domain:
        return "📖"
    return ("🔵", "🟠", "🟢", "🟣")[index % 4]


def _domain_title(url: str) -> str:
    try:
        host = urlparse(url).netloc or url
        return host.removeprefix("www.")
    except Exception:
        return url[:40]


def extract_urls(text: str) -> tuple[str, list[dict]]:
    """Pull URLs into source dicts; return cleaned prose."""
    urls = re.findall(r"https?://[^\s<>\"')\]]+", text)
    seen: set[str] = set()
    sources: list[dict] = []
    for u in urls:
        u = u.rstrip(".,;)")
        if u in seen:
            continue
        seen.add(u)
        sources.append({"url": u, "title": _domain_title(u)})
    clean = text
    for u in seen:
        clean = clean.replace(u, "").strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, sources


_FENCE_RE = re.compile(r"```([a-zA-Z0-9_\-\+]*)\r?\n(.*?)```", re.DOTALL)

# Shell / PowerShell cues for inline extraction
_SHELL_CMD = re.compile(
    r"(?:^\s*[$>]|\$irm\b|\biex\b|\binvoke-\w|\bcurl\b|\bwget\b|\bpip3?\b|\bnpm\b|\bnpx\b|"
    r"\bdocker\b|\bgit\s+\w|\bpython\b|\bpy\s+-|\bpowershell\b|\bchmod\b|\bsudo\b)",
    re.IGNORECASE,
)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_FULL_LINE_CMD = re.compile(r"^\s*(?:\d+[\.\)]\s*)?([$>].+)$", re.MULTILINE)
_CMD_INTRO_LINE = re.compile(
    r"(?im)^\s*\d+[\.\)]\s*run the following command:\s*$|^\s*run the following command:\s*$"
)
_CMD_INTRO_INLINE = re.compile(
    r"(?i)\d+[\.\)]\s*run the following command:\s*"
)


_SINGLE_TOOL_WORD = re.compile(
    r"^(?:curl|wget|npm|npx|yarn|pnpm|pip3?|brew|docker|git|python|py|iex)$",
    re.IGNORECASE,
)


def _looks_like_command(text: str) -> bool:
    t = text.strip()
    if len(t) < 3:
        return False
    if _SINGLE_TOOL_WORD.match(t):
        return False
    if t.startswith("$") or t.startswith(">"):
        return True
    if _SHELL_CMD.search(t):
        return True
    if re.match(
        r"(?:npm|npx|yarn|pnpm|curl|wget|brew|pip3?|pip|git\s+clone|docker\s+run)\b",
        t,
        re.I,
    ):
        return True
    return False


def _cmd_label(command: str) -> str:
    c = command.lower()
    if "$irm" in c or "| iex" in c or "invoke-" in c or "powershell" in c:
        return "powershell"
    if c.startswith("npm") or c.startswith("npx") or c.startswith("yarn") or c.startswith("pnpm"):
        return "npm"
    if c.startswith("curl") or c.startswith("wget"):
        return "curl"
    if c.startswith(">"):
        return "shell"
    return "bash"


def _extract_commands_from_text(chunk: str) -> tuple[str, list[str]]:
    """Pull inline / backtick / bare $ commands out of prose."""
    commands: list[str] = []
    seen: set[str] = set()

    def _add(cmd: str) -> None:
        cmd = cmd.strip().strip("`").strip()
        if not _looks_like_command(cmd):
            return
        key = cmd.lower()
        if key in seen:
            return
        seen.add(key)
        commands.append(cmd)

    def _backtick_repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        if _looks_like_command(inner):
            _add(inner)
            return ""
        return m.group(0)

    work = _BACKTICK_RE.sub(_backtick_repl, chunk)

    # npm / curl / pip commands not always in backticks
    for pat in (
        r"(?:npm|npx|yarn|pnpm)\s+(?:install|i|run|create)\s+[^\n.]+",
        r"curl\s+(?:-[A-Za-z]+\s+)*[^\n.]+",
        r"pip3?\s+install\s+[^\n.]+",
        r"brew\s+install\s+[^\n.]+",
    ):
        for m in re.finditer(pat, work, re.IGNORECASE):
            _add(m.group(0).strip().rstrip(".,;"))

    for m in _FULL_LINE_CMD.finditer(work):
        _add(m.group(1))

    remaining_lines: list[str] = []
    for line in work.split("\n"):
        stripped = line.strip()
        if _CMD_INTRO_LINE.match(stripped):
            continue
        cleaned = _CMD_INTRO_INLINE.sub("", stripped).strip()
        if not cleaned:
            continue
        if re.match(r"^[$>]", cleaned):
            _add(cleaned)
            continue
        remaining_lines.append(line)

    work = "\n".join(remaining_lines)
    work = _CMD_INTRO_LINE.sub("", work)
    for cmd in commands:
        work = work.replace(f"`{cmd}`", "")
        work = work.replace(cmd, "")
    work = re.sub(r"\bAlternatively,?\s*", "", work, flags=re.I)
    work = re.sub(r"\s+with\s*\.", ".", work)
    work = re.sub(r"\.\s+\.", ".", work)
    work = re.sub(r"\s{2,}", " ", work)
    work = re.sub(r"\n{3,}", "\n\n", work).strip()
    return work, commands


def parse_ai_blocks(text: str) -> list[tuple[str, object]]:
    """Split AI text into ('prose', str) | ('code', (lang, code)) | ('cmd', str) segments."""
    segments: list[tuple[str, object]] = []
    pos = 0
    for m in _FENCE_RE.finditer(text):
        before = text[pos : m.start()].strip()
        if before:
            segments.extend(_parse_prose_and_cmds(before))
        lang = m.group(1) or "text"
        code = m.group(2).strip()
        if _looks_like_command(code):
            segments.append(("cmd", code))
        else:
            segments.append(("code", (lang, code)))
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        segments.extend(_parse_prose_and_cmds(tail))
    if not segments:
        segments.append(("prose", text))
    return segments


def _parse_prose_and_cmds(chunk: str) -> list[tuple[str, object]]:
    cleaned, commands = _extract_commands_from_text(chunk)
    out: list[tuple[str, object]] = []
    if cleaned.strip():
        out.append(("prose", cleaned.strip()))
    for cmd in commands:
        out.append(("cmd", cmd))
    return out if out else [("prose", chunk)]
