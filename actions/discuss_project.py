"""discuss_project — NEO discusses architecture, suggests approaches, 
evaluates trade-offs for projects. NEO is the strategic partner, not the code generator.

Use when the user wants to:
- Discuss how to build something
- Evaluate technology choices
- Get architecture recommendations
- Plan a project without writing code
"""

from __future__ import annotations

from core.llm import ask
from core.models import DEEP_ANALYSIS


_SYSTEM_PROMPT = """You are NEO — a strategic technical advisor and architect.
You do NOT write code. You help the user think through their project.

Your role:
- Discuss architecture choices with trade-offs
- Recommend technologies based on the user's goals
- Suggest project structure and patterns
- Identify risks and edge cases early
- Point out what's easy vs what's hard

Guidelines:
- Be opinionated when it helps — recommend a specific stack with reasons
- Always explain WHY, not just WHAT
- When there are multiple valid approaches, present the top 2-3 with trade-offs
- Keep it conversational and direct — this is a discussion, not a spec document
- If the user's idea has a fatal flaw, say so kindly and suggest alternatives
- NEVER write code. You can describe what code would do, but don't write it.

If the user asks for something general (no specific details), ask 1-2 clarifying questions
before diving into recommendations."""


def discuss_project(parameters: dict | None = None, **kwargs) -> str:
    """Discuss a project idea with the user — architecture, stack, trade-offs.
    
    Args:
        parameters: dict with 'topic' (the project idea/question) and optional
                   'context' (what's already been discussed)
    """
    p = parameters or {}
    topic = (p.get("topic") or p.get("question") or p.get("project_idea") or "").strip()
    if not topic:
        return "What would you like to discuss? Tell me about the project or idea you're thinking about."

    context = (p.get("context") or "").strip()
    prompt = f"Project discussion topic: {topic}"
    if context:
        prompt += f"\n\nPrevious discussion context: {context}"

    try:
        response = ask(prompt, model=DEEP_ANALYSIS, system=_SYSTEM_PROMPT, temperature=0.6)
        return response
    except Exception as e:
        return f"I had trouble thinking through that. Error: {e}"
