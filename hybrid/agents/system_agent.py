"""System agent — OS, apps, files, desktop automation."""

from __future__ import annotations

from hybrid.agents.base import BaseAgent
from hybrid.registry import ToolRegistry
from hybrid.types import AgentRole, AgentTask, ExecutionContext, ToolResult

_SYSTEM_TOOLS = frozenset({
    "open_app",
    "system_control",
    "computer_settings",
    "desktop_control",
    "computer_control",
    "file_controller",
    "file_processor",
    "organizer_control",
    "document_tools",
    "list_manager",
    "notes_control",
    "calendar_control",
    "reminder",
    "code_helper",
    "dev_agent",
    "project_builder",
    "browser_control",
    "game_updater",
    "download_control",
})


class SystemAgent(BaseAgent):
    role = AgentRole.SYSTEM

    def __init__(self, registry: ToolRegistry | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.registry = registry or ToolRegistry.instance()

    def can_handle(self, task: AgentTask) -> bool:
        return task.context.get("tool_name") in _SYSTEM_TOOLS

    def run(self, task: AgentTask, ctx: ExecutionContext) -> ToolResult:
        name = task.context["tool_name"]
        args = task.context.get("tool_args") or {}
        return self.registry.invoke(name, args, ctx)
