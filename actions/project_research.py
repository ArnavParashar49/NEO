"""AI-driven project analysis — infers stack, architecture, and intake from any idea."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
MODEL = "gemini-2.5-flash"


def _api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _model():
    import google.generativeai as genai

    genai.configure(api_key=_api_key())
    return genai.GenerativeModel(MODEL)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _parse_json(text: str) -> dict[str, Any]:
    raw = _strip_fences(text)
    for attempt in (raw,):
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        blob = match.group()
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            # Common LLM mistake: unescaped newlines in strings
            blob = re.sub(r"\n(?=[^\"]*\"(?:[^\"\\]|\\.)*\"[^\"]*$)", " ", blob)
            try:
                return json.loads(blob)
            except json.JSONDecodeError:
                pass
    raise ValueError("Could not parse JSON from model response")


def _fallback_brief(session: dict[str, Any]) -> dict[str, Any]:
    desc = session.get("description", "project")
    plan = session.get("plan") or {}
    return {
        "project_name": session.get("project_name") or "new_project",
        "language": session.get("language") or "python",
        "stack": session.get("stack") or [],
        "user_brief": (
            f"Build: {desc}. "
            f"Architecture: {session.get('architecture', '')}. "
            f"Features: {', '.join(plan.get('features') or [])}. "
            f"Use stack: {', '.join(session.get('stack') or [])}."
        )[:2000],
        "vscode_prompt": "",
        "missing_critical": [],
        "follow_up_questions": [],
        "ready_to_build": True,
    }


def _quick_search(query: str, max_chars: int = 2200) -> str:
    try:
        from actions.web_search import web_search

        return (web_search(parameters={"query": query, "mode": "search"}, player=None) or "")[
            :max_chars
        ]
    except Exception as e:
        print(f"[ProjectResearch] Search skipped ({query[:40]}…): {e}")
        return ""


def _deep_web_research(description: str) -> str:
    """Several focused searches — stack, patterns, and editor-AI workflow."""
    queries = [
        f"best tech stack and architecture for {description} 2024 2025",
        f"how to build {description} step by step tutorial project structure",
        f"VS Code Copilot Cursor AI agent build project from scratch prompt tips",
    ]
    chunks: list[str] = []
    for q in queries:
        hit = _quick_search(q, max_chars=1800)
        if hit:
            chunks.append(f"### Query: {q}\n{hit}")
    return "\n\n".join(chunks)[:5500]


def research_project(description: str, memory_hint: str = "") -> dict[str, Any]:
    """
    Analyze ANY project idea and return a smart build plan.
    No hardcoded project types — the model decides everything.
    """
    search_notes = _deep_web_research(description)

    prompt = f"""You are a senior tech lead. A user wants a NEW project built efficiently.

IMPORTANT: ARIA will NOT write all the code. Strategy:
1. Research deeply (stack, architecture, pitfalls, v1 scope).
2. Open VS Code with an empty/scaffold folder.
3. Hand off to the VS Code AI (Copilot Chat / Cursor / Codeium) via one excellent master prompt.

Project idea (user's words):
{description}

User context:
{memory_hint or "None"}

Research notes (web — may be partial):
{search_notes or "None"}

Think step by step:
- Fastest path to a working v1 on a Mac (prefer letting VS Code AI generate code).
- Best stack and folder layout for the editor AI to succeed.
- What to put in the master prompt so the in-editor AI builds exactly what the user wants.
- Only ask 1-3 questions if answers would completely change stack or product type.

Return ONLY valid JSON:
{{
  "project_name": "snake_case_name",
  "project_kind": "short label",
  "language": "primary language",
  "stack": ["technologies"],
  "architecture": "2-3 sentences",
  "research_summary": "3-5 sentences: research findings and recommended approach",
  "delivery_strategy": "why VS Code AI is faster than coding from scratch here",
  "plan": {{
    "components": [{{"name": "id", "purpose": "role"}}],
    "suggested_files": ["paths for scaffold only — README, config, not full app"],
    "features": ["v1 features"],
    "run_mode": "executable | static_web | dev_server | gui | library",
    "run_command": "command after AI builds, or none"
  }},
  "vscode_workflow": "1-2 sentences: Copilot Chat vs Cursor Composer, which to use",
  "questions": [],
  "ready_to_build": true,
  "ready_reason": "why ready or not",
  "intro_message": "conversational summary for the user — mention research + VS Code handoff"
}}

Rules:
- questions: max 3, only critical; empty if idea is clear.
- suggested_files: minimal scaffold (5-12 paths), NOT a full generated app.
- Never default to portfolio unless asked.
- project_name: lowercase underscores, max 30 chars.

JSON:"""

    response = _model().generate_content(prompt)
    data = _parse_json(response.text)

    data.setdefault("project_name", "new_project")
    data.setdefault("project_kind", "software project")
    data.setdefault("language", "python")
    data.setdefault("stack", [])
    data.setdefault("plan", {})
    data.setdefault("questions", [])
    data.setdefault("ready_to_build", not data.get("questions"))
    data.setdefault("intro_message", data.get("research_summary", ""))
    data.setdefault("delivery_strategy", "Use VS Code AI with a detailed master prompt.")

    plan = data["plan"]
    plan.setdefault("components", [])
    plan.setdefault("suggested_files", [])
    plan.setdefault("features", [])
    plan.setdefault("run_mode", "executable")
    plan.setdefault("run_command", f"python main.py")

    return data


def synthesize_brief(session: dict[str, Any], memory_hint: str = "") -> dict[str, Any]:
    """Research wrap-up + VS Code AI master prompt (no full code generation)."""
    plan = session.get("plan") or session.get("template") or {}
    answers = session.get("answers", {}).get("combined") or session.get("answers", {}).get("latest", "")

    prompt = f"""You prepare a VS Code / Cursor AI agent handoff for this project.

Original idea: {session.get("description", "")}
Kind: {session.get("project_kind", "")}
Stack: {", ".join(session.get("stack") or [])}
Architecture: {session.get("architecture", "")}
Research: {session.get("research_summary", "")}
Delivery strategy: {session.get("delivery_strategy", "")}
VS Code workflow: {session.get("vscode_workflow", "Use Copilot Chat or Cursor Composer in the open folder.")}
Features: {json.dumps(plan.get("features", []))}
Components: {json.dumps(plan.get("components", []))}
Run: {plan.get("run_mode", "")} — {plan.get("run_command", "")}

User clarifications:
{answers or "None — use smart defaults."}

Memory:
{memory_hint or "None"}

Return ONLY valid JSON:
{{
  "project_name": "snake_case_name",
  "language": "language",
  "stack": ["final stack"],
  "user_brief": "Short human summary under 400 chars for ARIA to speak",
  "vscode_prompt": "FULL markdown prompt pasted into VS Code AI — see rules below",
  "missing_critical": [],
  "follow_up_questions": [],
  "ready_to_build": true
}}

Rules for vscode_prompt (plain text inside JSON, use \\n for newlines):
- Start with: "You are building this project in THIS workspace. Implement a complete working v1."
- Sections: ## Goal, ## Stack & constraints, ## Folder structure to create, ## Features (numbered), ## UX/UI notes, ## Implementation order (step-by-step for the AI), ## Quality bar (tests, error handling), ## How to run locally ({plan.get("run_command", "see README")})
- Be SPECIFIC to this project — file names, libraries, game rules, API routes, etc.
- Tell the editor AI to create ALL application code; ARIA only scaffolded docs.
- Under 3500 characters but dense and actionable.
- End with: "When done, list files created and the exact run command."

Rules for user_brief: spoken summary, no code.

JSON:"""

    try:
        response = _model().generate_content(prompt)
        data = _parse_json(response.text)
    except Exception as e:
        print(f"[ProjectResearch] synthesize_brief fallback: {e}")
        data = _fallback_brief(session)
    data.setdefault("missing_critical", [])
    data.setdefault("follow_up_questions", [])
    data.setdefault("ready_to_build", True)
    data.setdefault("vscode_prompt", "")
    if not data.get("vscode_prompt"):
        data["vscode_prompt"] = _fallback_vscode_prompt(session, data.get("user_brief", ""))
    return data


def _fallback_vscode_prompt(session: dict[str, Any], summary: str) -> str:
    plan = session.get("plan") or {}
    features = "\n".join(f"- {f}" for f in (plan.get("features") or [])[:12])
    files = "\n".join(f"- {p}" for p in (plan.get("suggested_files") or [])[:15])
    return f"""You are building this project in THIS workspace. Implement a complete working v1.

## Goal
{session.get("description", "")}

## Stack
{", ".join(session.get("stack") or [])} — {session.get("architecture", "")}

## Features
{features or "- See project brief"}

## Suggested layout
{files or "- Choose sensible structure for the stack"}

## Instructions
Build all application code. Add README with run steps.
Run command: {plan.get("run_command", "document in README")}

{summary}
"""
