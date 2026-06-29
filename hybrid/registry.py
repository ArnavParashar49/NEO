"""Dynamic tool registry — add tools via metadata without touching the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hybrid.types import ExecutionContext, ToolGuard, ToolHandler, ToolResult


@dataclass
class RegisteredTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    category: str = "general"
    agent: str = "tool"
    fast_eligible: bool = True
    slow: bool = False
    internal: bool = False  # save_memory, shutdown — not for fast-path routing
    guard: ToolGuard | None = None
    tags: list[str] = field(default_factory=list)
    # Declarative fast-path regex patterns: list of (regex_str, arg_map_dict)
    fast_path_patterns: list[tuple[str, dict]] = field(default_factory=list)


class ToolRegistry:
    _instance: ToolRegistry | None = None

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    @classmethod
    def instance(cls) -> ToolRegistry:
        if cls._instance is None:
            cls._instance = ToolRegistry()
        return cls._instance

    def register(
        self,
        *,
        name: str,
        description: str,
        parameters: dict,
        handler: ToolHandler,
        category: str = "general",
        agent: str = "tool",
        fast_eligible: bool = True,
        slow: bool = False,
        internal: bool = False,
        guard: ToolGuard | None = None,
        tags: list[str] | None = None,
        fast_path_patterns: list[tuple[str, dict]] | None = None,
    ) -> RegisteredTool:
        if name in self._tools:
            print(f"[ToolRegistry] Replacing tool: {name}")
        tool = RegisteredTool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            category=category,
            agent=agent,
            fast_eligible=fast_eligible,
            slow=slow,
            internal=internal,
            guard=guard,
            tags=tags or [],
            fast_path_patterns=fast_path_patterns or [],
        )
        self._tools[name] = tool
        return tool

    def lookup(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def list_tools(self, *, agent: str | None = None, category: str | None = None) -> list[RegisteredTool]:
        out = list(self._tools.values())
        if agent:
            out = [t for t in out if t.agent == agent]
        if category:
            out = [t for t in out if t.category == category]
        return out

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def slow_tools(self) -> frozenset[str]:
        return frozenset(t.name for t in self._tools.values() if t.slow)

    def to_gemini_declarations(self, include_custom_skills: bool = False, include_mcp_tools: bool = True) -> list[dict]:
        """Schema for Gemini function_declarations."""
        decls = []
        for tool in self._tools.values():
            if not include_custom_skills and tool.category == "custom_skills":
                continue
            # Exclude MCP tools if explicitly requested (e.g. for Live voice connection)
            if not include_mcp_tools and (tool.category == "mcp" or tool.name.startswith("mcp__")):
                continue
            if tool.internal and tool.name == "save_memory":
                pass  # still expose save_memory to model
            decls.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            })
        return decls

    def invoke(self, name: str, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        tool = self.lookup(name)
        if not tool:
            return ToolResult(ok=False, text=f"Unknown tool: {name}", tool_name=name)

        if tool.guard:
            block = tool.guard(args, ctx)
            if block:
                return ToolResult(ok=False, text=block, tool_name=name)

        try:
            text = tool.handler(args, ctx)
            ok = not (text or "").strip().lower().startswith(("failed", "error", "unknown tool"))
            return ToolResult(ok=ok, text=text or "Done.", tool_name=name)
        except Exception as e:
            return ToolResult(ok=False, text=f"Tool '{name}' failed: {e}", tool_name=name)

    def metadata_for_planner(self) -> str:
        """Compact tool list for planner prompts."""
        lines = []
        for t in self._tools.values():
            if t.internal and t.name == "shutdown_neo":
                continue
            lines.append(f"- {t.name}: {t.description[:120]}")
        return "\n".join(lines[:80])
