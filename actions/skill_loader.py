"""Load ECC-style skills from SKILL.md files into the ToolRegistry.

Skills are registered for the orchestrator / agent_task path only.
They are NOT sent to Gemini Live (that would exceed API limits) — use apply_skill instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.paths import base_dir
from hybrid.registry import ToolRegistry


def parse_skill_md(file_path: Path) -> dict[str, str] | None:
    """Parse YAML frontmatter and markdown body from a SKILL.md file."""
    try:
        content = file_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None

        parts = content.split("---", 2)
        if len(parts) < 3:
            return None

        frontmatter = parts[1].strip()
        body = parts[2].strip()

        metadata = {}
        for line in frontmatter.split("\n"):
            line = line.strip()
            if ":" in line:
                key, val = line.split(":", 1)
                metadata[key.strip()] = val.strip()

        name = metadata.get("name")
        description = metadata.get("description", "A custom ECC skill.")

        if not name:
            return None

        return {"name": name, "description": description, "body": body}

    except Exception as e:
        print(f"[SkillLoader] Failed to load {file_path}: {e}")
        return None


def create_skill_handler(skill_body: str):
    def handler(args: dict[str, Any], ctx: Any) -> str:
        query = args.get("query", "")
        if not query:
            return (
                f"=== SKILL INSTRUCTIONS ===\n{skill_body}\n"
                "==========================\n"
                "Please follow these instructions carefully for the rest of this task."
            )
            
        from core.agent_loop import run_agent, GeminiToolSession, AGENT_SYSTEM_PROMPT
        from hybrid.registry import ToolRegistry
        
        system = f"{AGENT_SYSTEM_PROMPT}\n\n=== SKILL INSTRUCTIONS ===\n{skill_body}\n==========================\nFollow these instructions to achieve the user's goal."
        
        session = GeminiToolSession(
            goal=query,
            system=system,
            tools=ToolRegistry.instance().to_gemini_declarations()
        )
        
        print(f"[SkillExecutor] Launching agent for custom skill with query: {query}")
        res = run_agent(goal=query, ctx=ctx, session=session)
        return res.answer

    return handler


def apply_skill_by_name(skill_name: str, query: str) -> str:
    """Resolve a skill from the registry (used by the apply_skill live tool)."""
    registry = ToolRegistry.instance()
    key = (skill_name or "").strip().replace("-", "_")
    tool = registry.lookup(key)
    if not tool:
        # Fuzzy: try with hyphens
        tool = registry.lookup(skill_name.strip().replace("_", "-"))
    if not tool or tool.category != "custom_skills":
        names = sorted(
            n for n in registry.names()
            if registry.lookup(n) and registry.lookup(n).category == "custom_skills"
        )
        sample = ", ".join(names[:12])
        extra = f" ... (+{len(names) - 12} more)" if len(names) > 12 else ""
        return (
            f"Unknown skill '{skill_name}'. "
            f"Examples: {sample}{extra}"
        )
    return tool.handler({"query": query or ""}, None)


_TOPIC_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "UI, design & frontend",
        re.compile(
            r"frontend|design.?system|design.?direction|\bui\b|react|angular|css|"
            r"a11y|vite|motion|liquid|tailwind|slides|vue|svelte|nextjs",
            re.I,
        ),
    ),
    (
        "Building & architecture",
        re.compile(
            r"build|architect|scaffold|mvp|hexagonal|blueprint|agentic|"
            r"project.?flow|team.?builder|coding.?standard",
            re.I,
        ),
    ),
    (
        "Backend & APIs",
        re.compile(
            r"backend|fastapi|django|spring|flask|laravel|nestjs|graphql|"
            r"postgres|redis|mysql|prisma|api.?design|golang|dotnet",
            re.I,
        ),
    ),
    (
        "Testing & quality",
        re.compile(
            r"testing|tdd|e2e|qa|verification|benchmark|regression|lint",
            re.I,
        ),
    ),
    (
        "DevOps & deployment",
        re.compile(
            r"docker|kubernetes|deploy|ci.?cd|github.?ops|terminal.?ops|git.?workflow",
            re.I,
        ),
    ),
]


def _skill_entries() -> list[tuple[str, str]]:
    registry = ToolRegistry.instance()
    out: list[tuple[str, str]] = []
    for name in sorted(registry.names()):
        tool = registry.lookup(name)
        if not tool or tool.category != "custom_skills":
            continue
        desc = (tool.description or "").strip()
        prefix = f"Applies the {name} skill:"
        if desc.startswith(prefix):
            desc = desc[len(prefix):].strip()
        out.append((name, desc[:140]))
    return out


def build_skills_catalog(*, max_chars: int = 7200, max_detail_per_topic: int = 18) -> str:
    """Compact skills index for the Live system prompt (size-capped)."""
    entries = _skill_entries()
    if not entries:
        return ""

    buckets: dict[str, list[tuple[str, str]]] = {title: [] for title, _ in _TOPIC_RULES}
    buckets["Other"] = []
    for name, desc in entries:
        placed = False
        hay = f"{name} {desc}"
        for title, pat in _TOPIC_RULES:
            if pat.search(hay):
                buckets[title].append((name, desc))
                placed = True
                break
        if not placed:
            buckets["Other"].append((name, desc))

    header = [
        f"[NEO CUSTOM SKILLS — {len(entries)} loaded from skills/]",
        "Domain expert playbooks (SKILL.md).",
        "apply_skill(skill_name, query) loads full instructions.",
        "When asked which skills you use, cite ids from below.",
        "",
    ]
    lines = list(header)
    detailed_names: set[str] = set()

    for title, _ in _TOPIC_RULES:
        items = buckets[title][:max_detail_per_topic]
        if not items:
            continue
        lines.append(f"{title}:")
        for name, desc in items:
            lines.append(f"  • {name}: {desc}")
            detailed_names.add(name)
        if len(buckets[title]) > max_detail_per_topic:
            extra = ", ".join(n for n, _ in buckets[title][max_detail_per_topic : max_detail_per_topic + 24])
            lines.append(f"  • also: {extra}")
            detailed_names.update(n for n, _ in buckets[title][max_detail_per_topic:])
        lines.append("")

    remaining = [n for n, _ in entries if n not in detailed_names]
    if remaining:
        lines.append(f"All other skill ids ({len(remaining)}):")
        lines.append(", ".join(remaining))

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text

    # Drop descriptions from the longest topic sections first, then truncate name list.
    compact: list[str] = list(header)
    for title, _ in _TOPIC_RULES:
        items = buckets[title][:12]
        if not items:
            continue
        compact.append(f"{title}: " + ", ".join(n for n, _ in items))
    compact.append("")
    other_names = [n for n, _ in entries]
    compact.append(f"All skill ids ({len(other_names)}):")
    name_line = ", ".join(other_names)
    budget = max_chars - len("\n".join(compact)) - 20
    if budget > 200:
        compact.append(name_line[:budget])
        if len(name_line) > budget:
            compact.append(f"... ({len(other_names)} total)")
    return "\n".join(compact)[:max_chars]


def load_all_skills(skills_dir: str | Path | None = None) -> int:
    """Scan the skills directory and register them in ToolRegistry only."""
    root_path = Path(skills_dir) if skills_dir else base_dir() / "skills"
    if not root_path.exists() or not root_path.is_dir():
        return 0

    registry = ToolRegistry.instance()
    loaded_count = 0

    for skill_file in root_path.rglob("SKILL.md"):
        parsed = parse_skill_md(skill_file)
        if not parsed:
            continue

        name = parsed["name"].replace("-", "_")
        description = parsed["description"]
        body = parsed["body"]

        if registry.lookup(name):
            continue

        registry.register(
            name=name,
            description=f"Applies the {name} skill: {description}",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What task you are applying this skill to.",
                    }
                },
                "required": ["query"],
            },
            handler=create_skill_handler(body),
            category="custom_skills",
            agent="planner",
            fast_eligible=False,
        )
        loaded_count += 1

    if loaded_count > 0:
        print(f"[SkillLoader] Loaded {loaded_count} ECC skills (registry only, not Live API).")
    return loaded_count
