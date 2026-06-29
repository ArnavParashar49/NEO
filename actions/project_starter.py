"""Safe project bootstrap with deterministic scaffolding and market research."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")[:60]


def _files_for(name: str, description: str, stack: str) -> dict[str, str]:
    stack_l = stack.casefold()
    readme = f"# {name}\n\n{description or 'Project overview.'}\n\n## Development\n\nAdd setup and run instructions here.\n"
    common = {"README.md": readme, ".gitignore": ".env\n.venv/\nnode_modules/\n__pycache__/\n*.pyc\n"}
    if "python" in stack_l:
        package = _slug(name).replace("-", "_")
        return common | {
            "pyproject.toml": "[project]\nname = \"" + _slug(name) + "\"\nversion = \"0.1.0\"\nrequires-python = \">=3.11\"\n\n[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n",
            f"src/{package}/__init__.py": "",
            "tests/test_smoke.py": "def test_project_imports():\n    assert True\n",
        }
    if any(token in stack_l for token in ("javascript", "typescript", "node", "react", "web")):
        return common | {
            "package.json": json.dumps({"name": _slug(name), "version": "0.1.0", "private": True, "scripts": {"test": "node --test"}}, indent=2) + "\n",
            "src/index.js": "export function main() {\n  return true;\n}\n",
            "tests/smoke.test.js": "import test from 'node:test';\nimport assert from 'node:assert/strict';\ntest('project starts', () => assert.ok(true));\n",
        }
    return common | {"src/.gitkeep": "", "tests/.gitkeep": "", "docs/architecture.md": f"# {name} architecture\n"}


def project_starter(parameters: dict | None = None, response=None, player=None,
                    session_memory=None) -> str:
    params = parameters or {}
    name = " ".join((params.get("name") or "").split())
    description = " ".join((params.get("description") or params.get("idea") or "").split())
    stack = " ".join((params.get("stack") or "generic").split())
    if not name or not _slug(name):
        return "NEEDS_USER: What should I name the new project?"

    root = Path(params.get("parent") or os.getenv("NEO_PROJECTS_DIR") or Path.home() / "Projects").expanduser().resolve()
    project = (root / _slug(name)).resolve()
    if root not in project.parents:
        return "FAILED: Invalid project path."
    if project.exists():
        return f"NEEDS_USER: {project} already exists. Choose another project name."

    created: list[Path] = []
    try:
        project.mkdir(parents=True)
        created.append(project)
        for relative, content in _files_for(name, description, stack).items():
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created.append(target)

        research = ""
        try:
            from actions.web_search import web_search

            research = web_search({
                "query": (
                    f"{description or name}: similar existing open-source and commercial projects, "
                    "useful inspiration, differentiators, reusable standards and libraries"
                )
            })
        except Exception as exc:
            research = f"Research unavailable: {exc}"
        (project / "RESEARCH.md").write_text(
            f"# Research and inspiration\n\n{research[:12000]}\n", encoding="utf-8"
        )
        return f"Created {name} at {project} with a {stack} scaffold and research notes in RESEARCH.md."
    except Exception as exc:
        for path in reversed(created):
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass
        return f"FAILED: Could not create project: {exc}"
