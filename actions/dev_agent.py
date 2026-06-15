import subprocess
import sys
import json
import re
import time
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
PROJECTS_DIR     = Path.home() / "Desktop" / "AriaProjects"
MAX_FIX_ATTEMPTS = 5
MODEL_PLANNER    = "gemini-2.5-flash"
MODEL_WRITER     = "gemini-2.5-flash"

from core.llm import ask


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _is_rate_limit(error: Exception) -> bool:
    msg = str(error).lower()
    return "429" in msg or "quota" in msg or "resource_exhausted" in msg


def _parse_traceback(output: str, project_files: list[str]) -> tuple[str | None, int | None]:

    pattern = re.compile(r'File ["\']([^"\']+\.py)["\'],\s+line\s+(\d+)', re.IGNORECASE)
    matches = pattern.findall(output)

    for raw_path, line_str in reversed(matches):
        raw_name = Path(raw_path).name
        for pf in project_files:
            if Path(pf).name == raw_name or pf == raw_path or raw_path.endswith(pf):
                return pf, int(line_str)

    return None, None


def _classify_error(output: str) -> str:

    low = output.lower()

    if any(x in low for x in ("no module named", "modulenotfounderror", "importerror")):
        return "dependency_error"

    if "syntaxerror" in low or "invalid syntax" in low:
        return "syntax_error"
    
    if "cannot import" in low or "importerror" in low:
        return "import_error"

    if any(x in low for x in (
        "traceback", "exception", "error:", "nameerror", "typeerror",
        "attributeerror", "valueerror", "keyerror", "indexerror",
        "zerodivisionerror", "filenotfounderror", "permissionerror",
    )):
        return "runtime_error"

    return "none"


def _has_error(output: str, run_command: str) -> bool:
    low = output.lower()

    if "timed out" in low and "likely working" in low:
        return False

    if not output.strip():
        return False

    if _classify_error(output) != "none":
        return True

    fail_phrases = (
        "unexpected error",
        "error occurred",
        "failed",
        "traceback",
        "exception",
        "missing 1 required",
        "missing required",
        "typeerror",
        "nameerror",
        "syntaxerror",
    )
    return any(p in low for p in fail_phrases)

class RateLimitError(Exception):
    pass


def _plan_project(
    description: str,
    language: str,
    user_brief: str = "",
    plan: dict | None = None,
    architecture: str = "",
    project_kind: str = "",
) -> dict:
    plan = plan or {}
    components = plan.get("components") or []
    component_hint = ""
    if components:
        component_hint = "\nPlanned components:\n" + "\n".join(
            f"- {c.get('name', '?')}: {c.get('purpose', '')}" for c in components
        )
    files_hint = ""
    if plan.get("suggested_files"):
        files_hint = f"\nSuggested files (adapt as needed): {', '.join(plan['suggested_files'])}"
    features_hint = ""
    if plan.get("features"):
        features_hint = "\nRequired features:\n" + "\n".join(f"- {f}" for f in plan["features"])
    run_hint = ""
    if plan.get("run_mode") or plan.get("run_command"):
        run_hint = (
            f"\nTarget run mode: {plan.get('run_mode', 'executable')}\n"
            f"Suggested run command: {plan.get('run_command', 'infer from stack')}"
        )

    prompt = f"""You are a senior software architect. Create a minimal, complete file plan for this project.

Project kind: {project_kind or "software project"}
Primary language: {language or "infer from stack and idea"}
Description: {description}

Architecture notes:
{architecture or "Infer the best structure for this idea."}

Developer brief (implement this):
{user_brief or "Use sensible defaults from the description."}
{component_hint}{files_hint}{features_hint}{run_hint}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "project_name": "snake_case_name",
  "entry_point": "main entry file for this stack",
  "files": [
    {{
      "path": "relative/path.ext",
      "description": "what this file does",
      "imports": []
    }}
  ],
  "run_command": "command to run from project root, or none for static/no-run projects",
  "dependencies": ["external packages only — not stdlib"]
}}

Rules — YOU decide based on the idea, not fixed templates:
1. Pick file layout and entry point appropriate for THIS stack (game, web, CLI, API, GUI, etc.).
2. List files in dependency order; entry point last.
3. imports: dot-notation project modules only (e.g. game.board for game/board.py); empty for non-Python or standalone files.
4. Minimal file count — only what's needed for a working v1.
5. run_command: use none for static websites; python/node/etc. for runnable apps; match run_mode.
6. dependencies: only third-party packages (pygame, flask, etc.) — never os, sys, json.
7. For HTML/CSS/JS sites: index.html + styles.css + script.js is fine unless the idea needs more.
8. For Python games/apps: proper module structure with if __name__ guard on entry.
9. Do NOT use React/npm/webpack unless the brief explicitly requires it.

JSON:"""

    response = ""
    try:
        response = ask(prompt, model=MODEL_PLANNER)
        raw = _strip_fences(response)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}\nRaw: {response[:300]}")
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise

def _write_file(
    file_info: dict,
    project_description: str,
    all_files: list[dict],
    language: str,
    project_dir: Path,
    already_written: dict[str, str],
    user_brief: str = "",
) -> str:
    file_path = file_info["path"]
    file_desc = file_info.get("description", "")
    file_imports = file_info.get("imports", [])

    file_list = "\n".join(
        f"  [{i+1}] {f['path']}: {f.get('description', '')}"
        for i, f in enumerate(all_files)
    )

    dependency_context = ""
    for dep_dotted in file_imports:
        dep_path = dep_dotted.replace(".", "/") + ".py"
        if dep_path in already_written:
            code_snippet = already_written[dep_path][:2000]
            dependency_context += f"\n\n--- {dep_path} (you must import from this) ---\n{code_snippet}"

    lang_rules = ""
    ext = Path(file_path).suffix.lower()
    lang_low = language.lower()
    if lang_low == "python" or ext == ".py":
        lang_rules = """
Python-specific rules:
- Use type hints for all function signatures.
- Add docstrings for all public functions and classes.
- Use if __name__ == "__main__": guard in the entry point.
- For relative imports within the project, use: from utils.helpers import foo  (match the project structure exactly).
- Do NOT use implicit relative imports (from . import ...) unless it's a proper package with __init__.py.
- If this is a package subdirectory, create __init__.py files where needed."""
    elif lang_low in ("javascript", "typescript", "js", "ts") or ext in (".js", ".ts"):
        lang_rules = """
JS/TS-specific rules:
- Use ES modules (import/export), not CommonJS (require).
- Add JSDoc comments for all exported functions.
- Handle promise rejections with try/catch in async functions."""
    elif lang_low in ("html", "web") or ext in (".html", ".htm", ".css"):
        lang_rules = """
Web rules (when this file is html/css/js):
- Semantic HTML5, responsive CSS, clean JS.
- Use brief content where provided; sensible placeholders otherwise."""

    prompt = f"""You are a senior {language} developer writing production-quality code for a real project.

Project goal: {project_description}

User brief (use this content in the project):
{user_brief or "Use sensible placeholders."}

Complete project file structure (in dependency order):
{file_list}

{f"Dependencies this file must import from other project files:{dependency_context}" if dependency_context else ""}

Your task: Write the complete, working code for: {file_path}
Purpose of this file: {file_desc}
{f"This file imports from: {', '.join(file_imports)}" if file_imports else "This file has no project-internal imports."}

{lang_rules}

General rules:
- Output ONLY raw code. Absolutely no explanation, no markdown, no triple backticks.
- Write COMPLETE, RUNNABLE code — no placeholders, no "# TODO", no "pass" stubs.
- Every import must either be from the standard library, listed dependencies, or the project files shown above.
- Match import paths EXACTLY to the file paths in the project structure (e.g. if file is "utils/helpers.py", import as "from utils.helpers import ...").
- Use proper error handling (try/except) where I/O or network calls are made.
- The code must work correctly when the project entry point is run from the project root directory.

Code for {file_path}:"""

    try:
        response = ask(prompt, model=MODEL_WRITER)
        code = _strip_fences(response)

        full_path = project_dir / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(code, encoding="utf-8")

        print(f"[DevAgent] ✅ Written: {file_path} ({len(code)} chars)")
        return code

    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise

def _install_dependencies(dependencies: list[str], project_dir: Path) -> str:
    if not dependencies:
        return "No external dependencies."

    to_install = []
    for dep in dependencies:
        pkg_name = re.split(r"[>=<!]", dep)[0].strip()
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", pkg_name],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            to_install.append(dep)
        else:
            print(f"[DevAgent] ✓ Already installed: {pkg_name}")

    if not to_install:
        return f"All dependencies already installed: {', '.join(dependencies)}"

    print(f"[DevAgent Sandbox] 📦 Installing: {to_install}")
    install_cmd = "pip install " + " ".join(to_install)
    return run_command_in(install_cmd, project_dir, timeout=120)

def _open_vscode(project_dir: Path) -> bool:
    from actions.editor_open import open_project_folder

    ok, _ = open_project_folder(project_dir)
    return ok


def _reveal_project(project_dir: Path, entry_point: str, run_command: str) -> None:
    """After build: editor in front + run in Terminal or browser preview."""
    from actions.editor_open import open_project_folder, open_static_preview, open_terminal_run

    open_project_folder(project_dir)
    if entry_point.endswith((".html", ".htm")):
        open_static_preview(project_dir, entry_point)
    elif run_command and str(run_command).lower() not in ("none", "n/a", ""):
        open_terminal_run(project_dir, run_command)

def _run_project(run_command: str, project_dir: Path, timeout: int = 30) -> str:
    if not run_command or str(run_command).lower() in ("none", "n/a"):
        return "Static project — no run step."
    return run_command_in(run_command, project_dir, timeout)

def run_command_in(command: str, project_dir: Path, timeout: int = 60) -> str:
    """Run an arbitrary build/run/install command inside project_dir securely via Docker,
    return real stdout/stderr + exit code."""
    if not command:
        return "No command given."
        
    print(f"[DevAgent Sandbox] Running: {command} in {project_dir}")
    
    # We mount the project dir into /workspace in the container
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{project_dir.resolve()}:/workspace",
        "-w", "/workspace",
        # Provide a default image that has Python; this could be expanded
        "python:3.12-slim",
        "bash", "-c", command
    ]
    
    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        segs = []
        if out:
            segs.append(f"STDOUT:\n{out[:4000]}")
        if err:
            segs.append(f"STDERR:\n{err[:4000]}")
        tail = f"(exit code {result.returncode})"
        return ("\n\n".join(segs) + f"\n{tail}") if segs else f"Ran with no output. {tail}"
    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s — a long-running app (server/GUI) is likely working."
    except FileNotFoundError as e:
        return f"Docker command not found. Is Docker installed and running? {e}"
    except Exception as e:
        return f"Sandbox run error: {e}"


def _try_auto_install(error_output: str, project_dir: Path) -> bool:
    """ModuleNotFoundError varsa eksik paketi otomatik kurmaya çalışır."""
    pattern = re.compile(
        r"No module named ['\"]([a-zA-Z0-9_\-\.]+)['\"]", re.IGNORECASE
    )
    match = pattern.search(error_output)
    if not match:
        return False

    pkg = match.group(1).replace("_", "-").split(".")[0]
    print(f"[DevAgent] 🔧 Auto-installing missing package: {pkg}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60, cwd=str(project_dir)
        )
        return result.returncode == 0
    except Exception:
        return False

def _fix_files(
    error_output: str,
    project_description: str,
    all_files: list[dict],
    file_codes: dict[str, str],
    language: str,
    project_dir: Path,
    entry_point: str,
) -> dict[str, str]:

    error_file, error_line = _parse_traceback(error_output, list(file_codes.keys()))
    error_type = _classify_error(error_output)

    files_to_fix: list[str] = []

    if error_file:
        files_to_fix.append(error_file)
        if error_type == "import_error":
            for fi in all_files:
                if error_file.replace("/", ".").replace(".py", "") in fi.get("imports", []):
                    p = fi["path"]
                    if p not in files_to_fix:
                        files_to_fix.append(p)
    else:
        files_to_fix.append(entry_point)

    updated_codes: dict[str, str] = {}

    for fix_path in files_to_fix:
        current_code = file_codes.get(fix_path, "")

        other_ctx = ""
        for fp, code in file_codes.items():
            if fp != fix_path and code:
                snippet = code[:1500] + ("..." if len(code) > 1500 else "")
                other_ctx += f"\n--- {fp} ---\n{snippet}\n"

        line_hint = f"\nError appears to be near line {error_line} in this file." if (
            error_line and fix_path == error_file
        ) else ""

        prompt = f"""You are an expert {language} debugger. Fix the broken file below.

Project goal: {project_description}

All project files:
{chr(10).join(f"  - {f['path']}: {f.get('description', '')}" for f in all_files)}

Other files for context (read-only — fix only the target file):
{other_ctx[:3500]}

File to fix: {fix_path}{line_hint}
Error type: {error_type}

Error output:
{error_output[:2500]}

Current (broken) code:
{current_code}

Rules:
- Output ONLY the complete fixed code. No explanation, no markdown, no backticks.
- Fix ALL errors visible in the error output.
- Keep all existing correct logic — do not remove working features.
- Ensure import paths match the actual project file structure exactly.
- Do NOT introduce new bugs or remove error handling.

Fixed code for {fix_path}:"""

        try:
            response = ask(prompt, model=MODEL_PLANNER)
            fixed = _strip_fences(response)

            full_path = project_dir / fix_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(fixed, encoding="utf-8")

            updated_codes[fix_path] = fixed
            print(f"[DevAgent] 🔧 Fixed: {fix_path}")

        except Exception as e:
            if _is_rate_limit(e):
                raise RateLimitError(str(e))
            print(f"[DevAgent] ⚠️ Could not fix {fix_path}: {e}")

    return updated_codes

def _is_static_or_no_run(run_command: str, entry_point: str, run_mode: str, language: str) -> bool:
    rc = str(run_command or "").lower().strip()
    if rc in ("none", "n/a", ""):
        return True
    if run_mode in ("static_web", "library"):
        return True
    if entry_point.endswith((".html", ".htm")):
        return True
    if language.lower() in ("html", "web") and run_mode != "dev_server":
        return True
    return False


def _build_project(
    description: str,
    language: str,
    project_name: str,
    timeout: int,
    speak=None,
    player=None,
    *,
    user_brief: str = "",
    plan: dict | None = None,
    architecture: str = "",
    project_kind: str = "",
    project_dir: Path | None = None,
    open_editor: bool = True,
) -> str:

    def log(msg: str):
        print(f"[DevAgent] {msg}")
        if player:
            player.write_log(f"[DevAgent] {msg}")

    full_description = description
    if user_brief:
        full_description = f"{description}\n\nUser brief:\n{user_brief}"

    log("Planning project structure...")
    research_plan = plan or {}
    try:
        file_plan = _plan_project(
            description,
            language,
            user_brief=user_brief,
            plan=research_plan,
            architecture=architecture,
            project_kind=project_kind,
        )
    except RateLimitError:
        msg = "Rate limit reached, sir. Please try again in a moment."
        if speak: speak(msg)
        return msg
    except ValueError as e:
        msg = f"Planning failed: {e}"
        if speak: speak(msg)
        return msg

    proj_name = project_name or file_plan.get("project_name", "aria_project")
    proj_name = re.sub(r"[^\w\-]", "_", proj_name)
    if project_dir is None:
        project_dir = PROJECTS_DIR / proj_name
    project_dir.mkdir(parents=True, exist_ok=True)

    if open_editor:
        _open_vscode(project_dir)
        if player:
            player.write_activity(f"Writing code in {project_dir.name}…")

    files = file_plan.get("files", [])
    entry_point = file_plan.get("entry_point", "main.py")
    run_command = file_plan.get("run_command", f"python {entry_point}")
    dependencies = file_plan.get("dependencies", [])

    static_site = _is_static_or_no_run(
        run_command,
        entry_point,
        research_plan.get("run_mode", ""),
        language,
    )

    log(f"Project: {proj_name} | Files: {len(files)} | Entry: {entry_point}")

    sorted_files = sorted(files, key=lambda fi: len(fi.get("imports", [])))
    file_codes: dict[str, str] = {}

    for file_info in sorted_files:
        file_path = file_info.get("path", "")
        if not file_path:
            continue

        log(f"Writing {file_path}...")
        for attempt in range(2):
            try:
                code = _write_file(
                    file_info=file_info,
                    project_description=full_description,
                    all_files=files,
                    language=language,
                    project_dir=project_dir,
                    already_written=file_codes,
                    user_brief=user_brief,
                )
                file_codes[file_path] = code
                time.sleep(0.4)
                break
            except RateLimitError:
                if attempt == 0:
                    log("Rate limit — waiting 20s...")
                    time.sleep(20)
                else:
                    log(f"Rate limit retry failed for {file_path}, skipping.")
            except Exception as e:
                log(f"Failed to write {file_path}: {e}")
                break

    if not file_codes:
        msg = "I could not write any project files, sir."
        if speak: speak(msg)
        return msg

    if dependencies:
        install_result = _install_dependencies(dependencies, project_dir)
        log(install_result)

    if static_site:
        _reveal_project(project_dir, entry_point, "none")
        index = project_dir / entry_point
        label = project_kind or proj_name.replace("_", " ")
        msg = (
            f"'{proj_name}' is ready — a {label}. "
            f"Saved to {project_dir}. "
            f"Opened in your browser and editor."
        )
        return msg

    last_output = ""
    auto_installs = 0

    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        log(f"Running project (attempt {attempt}/{MAX_FIX_ATTEMPTS})...")
        last_output = _run_project(run_command, project_dir, timeout)
        log(f"Output preview: {last_output[:150]}")

        if not _has_error(last_output, run_command):
            _reveal_project(project_dir, entry_point, run_command)
            msg = (
                f"Project '{proj_name}' is ready. "
                f"Saved to {project_dir}. "
                f"VS Code and Terminal are open so you can see it run."
            )
            return f"{msg}\n\nOutput:\n{last_output}"

        if attempt == MAX_FIX_ATTEMPTS:
            break

        error_type = _classify_error(last_output)
        if error_type == "dependency_error" and auto_installs < 3:
            installed = _try_auto_install(last_output, project_dir)
            if installed:
                auto_installs += 1
                log("Missing dependency installed, retrying...")
                time.sleep(1)
                continue

        log(f"Fixing errors (type: {error_type})...")
        try:
            updated = _fix_files(
                error_output=last_output,
                project_description=full_description,
                all_files=files,
                file_codes=file_codes,
                language=language,
                project_dir=project_dir,
                entry_point=entry_point,
            )
            file_codes.update(updated)
            time.sleep(1)
        except RateLimitError:
            msg = "Rate limit reached during fix. Project saved, check it manually in VSCode."
            if speak: speak(msg)
            return msg
        except Exception as e:
            log(f"Fix step failed: {e}")

    msg = (
        f"I couldn't fully fix '{proj_name}' after {MAX_FIX_ATTEMPTS} attempts, sir. "
        f"Project is saved at {project_dir} — open it in VSCode and check manually."
    )
    if speak: speak(msg)
    return f"{msg}\n\nLast error:\n{last_output[:600]}"


def build_from_brief(
    description: str,
    user_brief: str,
    language: str,
    project_name: str,
    plan: dict,
    stack: list,
    project_dir: Path,
    speak=None,
    player=None,
    timeout: int = 30,
    architecture: str = "",
    project_kind: str = "",
) -> str:
    """Called by project_builder after folder scaffold + editor open."""
    lang = (language or "").strip()
    if not lang and stack:
        lang = stack[0]
    if not lang:
        lang = "python"

    if speak:
        speak("Writing the code now.")

    return _build_project(
        description=description,
        language=lang,
        project_name=project_name,
        timeout=timeout,
        speak=speak,
        player=player,
        user_brief=user_brief,
        plan=plan,
        architecture=architecture,
        project_kind=project_kind,
        project_dir=project_dir,
        open_editor=False,
    )


def dev_agent(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    p            = parameters or {}
    description  = p.get("description", "").strip()
    language     = p.get("language", "python").strip()
    project_name = p.get("project_name", "").strip()
    timeout      = int(p.get("timeout", 30))

    if not description:
        return "Please describe the project you want me to build, sir."

    return _build_project(
        description  = description,
        language     = language,
        project_name = project_name,
        timeout      = timeout,
        speak        = speak,
        player       = player,
    )
