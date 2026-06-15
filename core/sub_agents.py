"""Multi-agent orchestration for ARIA.

Allows the manager agent to decompose complex goals and dispatch work to
specialised sub-agents that each run in their own isolated ReAct loop with
a scoped tool set and focused system prompt.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from core.agent_loop import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_MAX_STEPS,
    AgentResult,
    GeminiToolSession,
    Step,
    run_agent,
)
from hybrid.registry import ToolRegistry
from hybrid.types import ExecutionContext


# --------------------------------------------------------------------------- #
# Sub-agent specification                                                      #
# --------------------------------------------------------------------------- #

@dataclass
class SubAgentSpec:
    """Blueprint for a specialised sub-agent."""

    name: str
    system_prompt: str
    allowed_tools: list[str]
    max_steps: int = DEFAULT_MAX_STEPS


# --------------------------------------------------------------------------- #
# Built-in agent definitions                                                   #
# --------------------------------------------------------------------------- #

BUILT_IN_AGENTS: dict[str, SubAgentSpec] = {
    "researcher": SubAgentSpec(
        name="researcher",
        system_prompt=(
            "You are ARIA's Research Agent. Your job is to gather, compare, and "
            "summarise information from the web and other sources.\n\n"
            "Principles:\n"
            "- Search broadly first, then drill into specifics.\n"
            "- Always cite sources (URLs) in your summary.\n"
            "- Return a clear, concise answer — not raw search dumps.\n"
            "- If you cannot find reliable information, say so honestly.\n"
            "- Do NOT write code or modify files — only gather information."
        ),
        allowed_tools=[
            "web_search", "browser_control", "youtube_video", "flight_finder",
        ],
        max_steps=15,
    ),
    "coder": SubAgentSpec(
        name="coder",
        system_prompt=(
            "You are ARIA's Coding Agent — a senior engineer who writes real, "
            "production-quality code.\n\n"
            "Principles:\n"
            "- Write COMPLETE code. Never leave TODOs, stubs, or placeholders.\n"
            "- Handle errors and edge cases properly.\n"
            "- Use file_controller to create/edit files.\n"
            "- Use code_helper or dev_agent for building and running.\n"
            "- After writing code, verify it works by running it.\n"
            "- Stay inside the project folder. Never touch unrelated files.\n"
            "- If you need a capability you don't have, use create_action to "
            "build it."
        ),
        allowed_tools=[
            "file_controller", "code_helper", "dev_agent",
            "create_action", "web_search",
        ],
        max_steps=20,
    ),
    "system_ops": SubAgentSpec(
        name="system_ops",
        system_prompt=(
            "You are ARIA's System Operations Agent. You handle OS-level tasks: "
            "opening apps, managing files/folders, adjusting settings, and "
            "automating the desktop.\n\n"
            "Principles:\n"
            "- Execute tasks precisely — don't over-do or under-do.\n"
            "- Use the right tool for each operation.\n"
            "- Report what you did clearly so the user knows the outcome.\n"
            "- If a destructive operation needs confirmation, stop and report."
        ),
        allowed_tools=[
            "open_app", "file_controller", "desktop_control",
            "system_control", "computer_settings", "computer_control",
            "organizer_control", "document_tools",
        ],
        max_steps=12,
    ),
    "comms": SubAgentSpec(
        name="comms",
        system_prompt=(
            "You are ARIA's Communications Agent. You handle emails, messages, "
            "calendar events, reminders, and contact management.\n\n"
            "Principles:\n"
            "- Always look up contacts before sending messages or emails.\n"
            "- Draft messages clearly and confirm before sending.\n"
            "- For calendar events, include all relevant details.\n"
            "- Report what you did so the user can verify."
        ),
        allowed_tools=[
            "send_email", "send_message", "contact_manager",
            "calendar_control", "reminder", "notes_control",
        ],
        max_steps=10,
    ),
}


# --------------------------------------------------------------------------- #
# Sub-agent execution                                                          #
# --------------------------------------------------------------------------- #

def _build_scoped_registry(spec: SubAgentSpec) -> ToolRegistry:
    """Create a ToolRegistry containing only the tools this sub-agent needs."""
    global_reg = ToolRegistry.instance()
    scoped = ToolRegistry()
    for tool_name in spec.allowed_tools:
        tool = global_reg.lookup(tool_name)
        if tool is not None:
            scoped._tools[tool_name] = tool
        else:
            print(f"[SubAgent:{spec.name}] ⚠️ Tool '{tool_name}' not found in global registry")
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
# Multi-agent orchestration (parallel dispatch)                                #
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
    """Run multiple sub-agents in parallel and merge their results.

    Args:
        sub_tasks: List of (agent_type, goal) pairs to dispatch.
        ctx: Shared execution context.
        on_progress: Callback(agent_name, status_message) for progress updates.

    Returns:
        MultiAgentResult with individual results and a merged summary.
    """
    ctx = ctx or ExecutionContext()
    multi_result = MultiAgentResult()

    def _run_one(task: SubAgentTask) -> tuple[str, AgentResult]:
        spec = BUILT_IN_AGENTS.get(task.agent_type)
        if spec is None:
            return task.agent_type, AgentResult(
                answer=f"Unknown agent type: {task.agent_type}",
                stopped_reason="error",
            )

        if on_progress:
            on_progress(spec.name, f"Starting: {task.goal[:60]}")

        def _on_step(step: Step) -> None:
            if on_progress:
                on_progress(spec.name, f"{step.tool}() → {'✓' if step.ok else '✗'}")

        result = run_sub_agent(spec, task.goal, ctx, on_step=_on_step)

        if on_progress:
            on_progress(spec.name, f"Done ({result.stopped_reason})")

        return spec.name, result

    # Run sub-agents in parallel threads
    max_workers = min(4, max(1, len(sub_tasks)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_one, t): t for t in sub_tasks}
        for fut in as_completed(futures):
            try:
                name, result = fut.result()
                multi_result.results[name] = result
            except Exception as e:
                task = futures[fut]
                multi_result.results[task.agent_type] = AgentResult(
                    answer=f"Sub-agent '{task.agent_type}' crashed: {e}",
                    stopped_reason="error",
                )

    # Build summary
    parts = []
    for name, result in multi_result.results.items():
        status = "✅" if result.stopped_reason == "done" else "⚠️"
        parts.append(f"{status} **{name}** ({len(result.steps)} steps): {result.answer[:200]}")
    multi_result.summary = "\n".join(parts)

    return multi_result
