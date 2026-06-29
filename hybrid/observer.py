"""Continuous Learning Observer — watches the TaskBus to learn new skills."""

import os
import uuid
from typing import Any
from pathlib import Path

from hybrid.task_bus import TaskBus
from hybrid.types import AgentMessage


class ContinuousLearningObserver:
    def __init__(self, bus: TaskBus):
        self.bus = bus
        self.bus.subscribe("task.completed", self._evaluate_session)

    def _evaluate_session(self, message: AgentMessage) -> None:
        import threading
        
        def _background_evaluate():
            try:
                goal = message.payload.get("goal")
                steps = message.payload.get("steps", [])
                res = message.payload.get("result", None)
                
                if not goal or not steps or len(steps) < 2:
                    return

                # Check if there was at least one failure
                had_failure = any(
                    (not getattr(s, "ok", True)) or 
                    ("not found" in str(getattr(s, "result", "")).lower()) or 
                    ("error" in str(getattr(s, "result", "")).lower()) 
                    for s in steps
                )
                # Check if there was at least one success
                had_success = any(getattr(s, "ok", True) for s in steps)
                
                if not (had_failure and had_success):
                    return  # Nothing interesting to learn (either all failed or all succeeded easily)

                print(f"[Observer] Analyzing completed task for continuous learning: {goal}")
                
                # Format the history
                history_text = "\n".join([f"Step {i+1}:\nTool: {s.tool}\nArgs: {s.args}\nResult: {s.result}\nOK: {s.ok}" for i, s in enumerate(steps)])
                final_answer = getattr(res, "answer", "") if res else ""
                
                prompt = f"""You are NEO's continuous learning module.
The agent just completed a task with the following goal: "{goal}"

Here is the execution history:
{history_text}

Final Agent Answer:
{final_answer}

The agent initially failed at some steps but eventually succeeded or learned something.
Your job is to analyze this history and determine if there is a generalized, reusable lesson or "skill" that NEO should remember for next time to avoid making the same mistakes.

If there is a valuable lesson (e.g. "Screenshots are in Pictures/Screenshots, not Desktop", or "Always use X command for Y"), output a JSON object containing the skill.
If it was just a transient error (e.g. network timeout) or not a reusable lesson, output {{"learn": false}}.

Output format:
{{
  "learn": true,
  "skill_name": "find_screenshots",
  "description": "Short 1 sentence description of what this skill does",
  "instructions": "Markdown formatted instructions to the agent on how to correctly perform this action next time based on the successful steps."
}}
"""
                from core.llm import ask_json
                from core.models import DEEP_ANALYSIS
                
                analysis = ask_json(prompt, model=DEEP_ANALYSIS)
                if not analysis.get("learn"):
                    return
                
                skill_name = analysis.get("skill_name", "auto_learned_skill_" + str(uuid.uuid4())[:8])
                # Sanitize skill name
                import re
                skill_name = re.sub(r'[^a-zA-Z0-9_]', '_', skill_name.lower())
                description = analysis.get("description", "Automatically learned pattern")
                instructions = analysis.get("instructions", "")
                
                skill_content = f"""---
name: {skill_name}
description: {description}
---

# {skill_name.replace('_', ' ').title()}

This skill was automatically extracted by the Continuous Learning Observer.

## Instructions
{instructions}
"""
                
                skills_dir = Path("c:/Users/Madhav/.aria/skills")
                if not skills_dir.exists():
                    skills_dir.mkdir(parents=True, exist_ok=True)
                    
                new_skill_dir = skills_dir / skill_name
                new_skill_dir.mkdir(exist_ok=True)
                skill_file = new_skill_dir / "SKILL.md"
                skill_file.write_text(skill_content, encoding="utf-8")
                print(f"[Observer] Extracted new skill: {skill_name}")
                
                # Reload skills
                from actions.skill_loader import load_all_skills
                load_all_skills()
            except Exception as e:
                print(f"[Observer] Failed to evaluate learning: {e}")

        # Run in background so we don't block the task bus
        threading.Thread(target=_background_evaluate, daemon=True).start()
