"""Thin compatibility shim — re-exports agent specs from core.agent_swarm.

This file exists so that modules (goal_dispatcher, bootstrap) that need
simple sub-agent specs + isolated execution don't need to know about the
full conversation protocol. The canonical agent definitions live in
core.agent_swarm.SWARM_AGENTS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from core.agent_loop import (
    DEFAULT_MAX_STEPS,
    AgentResult,
    GeminiToolSession,
    Step,
    run_agent,
)
from core.agent_swarm import SWARM_AGENTS
from hybrid.registry import ToolRegistry
from hybrid.types import ExecutionContext


# --------------------------------------------------------------------------- #
# Re-export agent specs as BUILT_IN_AGENTS (canonical source = SWARM_AGENTS)   #
# --------------------------------------------------------------------------- #

@dataclass
class SubAgentSpec:
    """Simple spec for isolated sub-agent execution (no conversation)."""

    name: str
    system_prompt: str
    allowed_tools: list[str]
    max_steps: int = DEFAULT_MAX_STEPS


def _swarm_to_sub(spec) -> SubAgentSpec:
    return SubAgentSpec(
        name=spec.name,
        system_prompt=spec.system_prompt,
        allowed_tools=spec.allowed_tools,
        max_steps=spec.max_steps,
    )


BUILT_IN_AGENTS: dict[str, SubAgentSpec] = {
    name: _swarm_to_sub(spec)
    for name, spec in SWARM_AGENTS.items()
}


# --------------------------------------------------------------------------- #
# Scoped registry + isolated execution                                         #
# --------------------------------------------------------------------------- #

def _build_scoped_registry(spec: SubAgentSpec) -> ToolRegistry:
    """Create a ToolRegistry containing only the tools this sub-agent needs, plus all MCP tools."""
    global_reg = ToolRegistry.instance()
    scoped = ToolRegistry()
    for tool_name in spec.allowed_tools:
        tool = global_reg.lookup(tool_name)
        if tool is not None:
            scoped._tools[tool_name] = tool
        else:
            print(f"[SubAgent:{spec.name}] ⚠️ Tool '{tool_name}' not found in global registry")
            
    # Dynamically inject all available MCP tools into every sub-agent so they can access external APIs
    for tool_name, tool in global_reg._tools.items():
        if tool.category == "mcp" or tool_name.startswith("mcp__"):
            scoped._tools[tool_name] = tool
            
    return scoped


def run_sub_agent(
    spec: SubAgentSpec,
    goal: str,
    ctx: ExecutionContext | None = None,
    *,
    on_step: Callable[[Step], None] | None = None,
) -> AgentResult:
    """Run a single sub-agent in its own ReAct loop with a scoped tool set."""
    registry = _build_scoped_registry(spec)
    ctx = ctx or ExecutionContext()

    session = GeminiToolSession(
        goal,
        system=spec.system_prompt,
        tools=registry.to_gemini_declarations(),
    )

    print(f"[SubAgent:{spec.name}] 🚀 Starting with {len(registry._tools)} tools | Goal: {goal[:80]}")

    result = run_agent(
        goal, ctx,
        registry=registry,
        session=session,
        max_steps=spec.max_steps,
        on_step=on_step,
    )

    print(f"[SubAgent:{spec.name}] ✅ Done ({len(result.steps)} steps, reason: {result.stopped_reason})")
    return result


# --------------------------------------------------------------------------- #
# Parallel dispatch (no conversation, no dependency ordering)                   #
# --------------------------------------------------------------------------- #

@dataclass
class SubAgentTask:
    """A unit of work for a sub-agent."""
    agent_type: str
    goal: str


@dataclass
class MultiAgentResult:
    """Aggregated results from multiple sub-agents."""
    results: dict[str, AgentResult] = field(default_factory=dict)
    summary: str = ""


def run_multi_agent(
    sub_tasks: list[SubAgentTask],
    ctx: ExecutionContext | None = None,
    *,
    on_progress: Callable[[str, str], None] | None = None,
) -> MultiAgentResult:
    """Run multiple sub-agents in parallel and merge results."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ctx = ctx or ExecutionContext()
    result = MultiAgentResult()

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(sub_tasks)))) as pool:
        futures = {}
        for task in sub_tasks:
            spec = BUILT_IN_AGENTS.get(task.agent_type)
            if spec is None:
                result.results[task.agent_type] = AgentResult(
                    answer=f"Unknown agent: {task.agent_type}",
                    stopped_reason="error",
                )
                continue
            fut = pool.submit(run_sub_agent, spec, task.goal, ctx)
            futures[fut] = task

        for fut in as_completed(futures):
            task = futures[fut]
            try:
                result.results[task.agent_type] = fut.result()
                if on_progress:
                    on_progress(task.agent_type, "Done")
            except Exception as exc:
                result.results[task.agent_type] = AgentResult(
                    answer=f"Agent crashed: {exc}",
                    stopped_reason="error",
                )

    parts = []
    for name, r in result.results.items():
        status = "✅" if r.stopped_reason == "done" else "⚠️"
        parts.append(f"{status} **{name}**: {r.answer[:200]}")
    result.summary = "\n".join(parts)
    return result
