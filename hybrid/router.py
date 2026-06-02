"""Adaptive routing — fast path (no planner) vs planned execution. No extra LLM calls."""

from __future__ import annotations

import re

from hybrid.registry import ToolRegistry
from hybrid.types import AgentTask, ExecutionMode, RouteDecision

# Multi-step / reasoning cues → planned path
_COMPLEX_RE = re.compile(
    r"\b("
    r"and then|after that|then email|email (?:it |them )?to|schedule|notify|"
    r"research .+ and|summarize .+ and|compare .+ and|create a (?:report|table|summary)|"
    r"find .+ (?:nearby|near me).+ and|multiple steps|step by step"
    r")\b",
    re.I,
)

_AGENT_TASK_RE = re.compile(
    r"\b(build|create|develop|scaffold|full project|multi.?step task)\b",
    re.I,
)

# Fast-path utterance → tool (regex only, sub-second)
_FAST_RULES: list[tuple[re.Pattern, str, callable]] = [
    (
        re.compile(
            r"^(?:hey\s+aria[,]?\s+)?(?:please\s+)?(?:open|launch|start|run)\s+(.+?)[\.\!?]*$",
            re.I,
        ),
        "open_app",
        lambda m: {"app_name": m.group(1).strip()},
    ),
    (
        re.compile(
            r"^(?:please\s+)?(?:play|pause|resume|skip)\s+(?:music|spotify)?[\.\!?]*$",
            re.I,
        ),
        "open_app",
        lambda m: {"app_name": "Spotify"},
    ),
    (
        re.compile(r"(?:turn on|enable)\s+bluetooth[\.\!?]*$", re.I),
        "system_control",
        lambda m: {"action": "bluetooth", "state": "on"},
    ),
    (
        re.compile(r"(?:turn off|disable)\s+bluetooth[\.\!?]*$", re.I),
        "system_control",
        lambda m: {"action": "bluetooth", "state": "off"},
    ),
    (
        re.compile(
            r"(?:increase|turn up|raise)\s+(?:the\s+)?volume|volume up|louder",
            re.I,
        ),
        "system_control",
        lambda m: {"action": "volume", "direction": "up"},
    ),
    (
        re.compile(
            r"(?:decrease|turn down|lower)\s+(?:the\s+)?volume|volume down|quieter",
            re.I,
        ),
        "system_control",
        lambda m: {"action": "volume", "direction": "down"},
    ),
    (
        re.compile(r"(?:mute|silence)\s+(?:the\s+)?(?:volume|sound)|\bmute\b", re.I),
        "system_control",
        lambda m: {"action": "volume", "direction": "mute"},
    ),
    (
        re.compile(
            r"(?:increase|turn up|raise)\s+(?:the\s+)?brightness|brighter",
            re.I,
        ),
        "system_control",
        lambda m: {"action": "brightness", "direction": "up"},
    ),
    (
        re.compile(
            r"(?:decrease|turn down|lower|dim)\s+(?:the\s+)?brightness|dimmer",
            re.I,
        ),
        "system_control",
        lambda m: {"action": "brightness", "direction": "down"},
    ),
    (
        re.compile(
            r"^(?:open|launch)\s+(?:the\s+)?calculator[\.\!?]*$",
            re.I,
        ),
        "open_app",
        lambda m: {"app_name": "Calculator"},
    ),
    (
        re.compile(
            r"^(?:please\s+)?(?:download|get)\s+(?:me\s+)?(?:the\s+)?(.+?)(?:\s+from\s+(?:google|the\s+(?:web|internet)))?[\.\!?]*$",
            re.I,
        ),
        "download_control",
        lambda m: {"action": "google", "query": m.group(1).strip()},
    ),
    (
        re.compile(
            r"^(?:please\s+)?download\s+(https?://\S+)[\.\!?]*$",
            re.I,
        ),
        "download_control",
        lambda m: {"action": "url", "url": m.group(1).strip().rstrip(".,)")},
    ),
]


class AdaptiveRouter:
    """Classifies requests without calling an LLM."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry.instance()

    def route(self, user_text: str, *, tool_hint: str | None = None) -> RouteDecision:
        text = (user_text or "").strip()
        if not text:
            return RouteDecision(mode=ExecutionMode.DIRECT, reason="empty")

        if tool_hint and self.registry.lookup(tool_hint):
            return RouteDecision(
                mode=ExecutionMode.DIRECT,
                tool_name=tool_hint,
                reason="explicit_tool_hint",
            )

        if self._is_complex(text):
            task = AgentTask.new(text, ExecutionMode.PLANNED, user_text=text)
            return RouteDecision(
                mode=ExecutionMode.PLANNED,
                reason="complex_goal",
                agent_task=task,
            )

        fast = self._match_fast_path(text)
        if fast:
            return fast

        return RouteDecision(mode=ExecutionMode.DIRECT, reason="defer_to_live_model")

    def _is_complex(self, text: str) -> bool:
        if _AGENT_TASK_RE.search(text):
            return True
        if text.count(" and ") >= 2 and len(text.split()) >= 8:
            return True
        return bool(_COMPLEX_RE.search(text))

    def _match_fast_path(self, text: str) -> RouteDecision | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        for pattern, tool_name, arg_fn in _FAST_RULES:
            m = pattern.search(normalized)
            if not m:
                continue
            tool = self.registry.lookup(tool_name)
            if not tool or not tool.fast_eligible:
                continue
            return RouteDecision(
                mode=ExecutionMode.DIRECT,
                tool_name=tool_name,
                tool_args=arg_fn(m),
                reason="fast_path_regex",
                confidence=0.92,
            )
        return None
