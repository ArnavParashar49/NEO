"""Cross-platform, terminal-first plans for downloads and app installation."""

from __future__ import annotations

import platform
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


_SAFE_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@/ -]{0,119}$")
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")

# Verified package identifiers for names whose product name does not match the
# native registry entry. Keep this intentionally small and source-backed.
_GENERIC_PRODUCT_SUFFIXES = re.compile(
    r"\s+(?:cli|app|application|desktop(?:\s+app)?)$", re.I
)


@dataclass(frozen=True)
class TerminalPlan:
    executable: str
    args: tuple[str, ...]
    display: str
    cwd: Path | None = None
    browser_fallback: bool = False
    source: str = "Terminal"


def _display_command(executable: str, args: tuple[str, ...]) -> str:
    command = Path(executable).name
    if platform.system() == "Windows":
        command = command.lower()
    argv = (command, *args)
    if platform.system() == "Windows":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _plan(executable: str, *args: str, cwd: Path | None = None,
          browser_fallback: bool = False, source: str = "Terminal") -> TerminalPlan:
    values = tuple(str(arg) for arg in args)
    return TerminalPlan(
        executable=executable,
        args=values,
        display=_display_command(executable, values),
        cwd=cwd,
        browser_fallback=browser_fallback,
        source=source,
    )


def _normalized_product(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _search_queries(package: str) -> tuple[str, ...]:
    simplified = _GENERIC_PRODUCT_SUFFIXES.sub("", package).strip()
    return (package,) if simplified == package else (package, simplified)


def _run_catalog_command(argv: list[str], timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return _clean_output(result.stdout or result.stderr or "")


def _find_winget_id(executable: str, package: str) -> str | None:
    for query in _search_queries(package):
        output = _run_catalog_command([
            executable, "search", "--query", query, "--source", "winget",
            "--accept-source-agreements",
        ])
        wanted = _normalized_product(query)
        for line in output.splitlines():
            columns = re.split(r"\s{2,}", line.strip())
            if len(columns) >= 3 and _normalized_product(columns[0]) == wanted:
                package_id = columns[1].strip()
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{1,119}", package_id):
                    return package_id
    return None


def _find_brew_cask(executable: str, package: str) -> str | None:
    for query in _search_queries(package):
        output = _run_catalog_command([executable, "search", "--casks", query])
        wanted = _normalized_product(query)
        for line in output.splitlines():
            candidate = line.strip()
            if _normalized_product(candidate) == wanted:
                return candidate
    return None


def _find_linux_package(executable: str, package: str) -> str | None:
    if " " in package:
        return None
    output = _run_catalog_command([executable, "search", package])
    wanted = package.casefold()
    for line in output.splitlines():
        candidate = re.split(r"[\s/]", line.strip(), maxsplit=1)[0]
        if candidate.casefold() == wanted:
            return candidate
    return None


def resolve_install_plan(package: str) -> TerminalPlan | None:
    """Choose the fastest installed native package manager for this OS."""
    package = " ".join((package or "").split())
    if not package or not _SAFE_PACKAGE.fullmatch(package):
        return None

    system = platform.system()
    if system == "Windows":
        if executable := shutil.which("winget"):
            if package_id := _find_winget_id(executable, package):
                return _plan(
                    executable, "install", "--id", package_id, "--exact",
                    "--accept-source-agreements", "--accept-package-agreements",
                    browser_fallback=True, source="WinGet",
                )
    elif system == "Darwin":
        if executable := shutil.which("brew"):
            if cask := _find_brew_cask(executable, package):
                return _plan(
                    executable, "install", "--cask", cask,
                    browser_fallback=True, source="Homebrew",
                )
    elif system == "Linux":
        managers = (
            ("apt-get", ("install", "--yes", package), True),
            ("dnf", ("install", "--assumeyes", package), True),
            ("pacman", ("-S", "--noconfirm", package), True),
            ("zypper", ("--non-interactive", "install", package), True),
            ("flatpak", ("install", "--assumeyes", "flathub", package), False),
        )
        for command, args, needs_sudo in managers:
            if executable := shutil.which(command):
                package_name = _find_linux_package(executable, package)
                if not package_name:
                    continue
                args = tuple(package_name if arg == package else arg for arg in args)
                if needs_sudo and shutil.which("sudo"):
                    return _plan(
                        "sudo", executable, *args, browser_fallback=True,
                        source=command,
                    )
                return _plan(
                    executable, *args, browser_fallback=True, source=command,
                )
    return None


def resolve_url_download_plan(url: str, destination: Path, filename: str) -> TerminalPlan | None:
    """Build an allowlisted direct-download command without invoking a shell."""
    if executable := shutil.which("curl"):
        return _plan(
            executable, "--location", "--fail", "--show-error",
            "--output", filename, url, cwd=destination,
        )
    if executable := shutil.which("wget"):
        return _plan(executable, "--output-document", filename, url, cwd=destination)
    return None


def resolve_cli_plan(tool: str, args: list[str], destination: Path) -> TerminalPlan | None:
    """Resolve an explicitly requested, allowlisted download CLI."""
    normalized = (tool or "").strip().lower()
    if normalized not in {"curl", "wget", "yt-dlp", "youtube-dl"} or not args:
        return None
    if not (executable := shutil.which(normalized)):
        return None
    return _plan(executable, *args, cwd=destination)


def run_plan(plan: TerminalPlan, timeout: int = 900) -> tuple[bool, str]:
    """Execute a reviewed plan directly; shell parsing is intentionally disabled."""
    try:
        completed = subprocess.run(
            [plan.executable, *plan.args],
            cwd=str(plan.cwd) if plan.cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout} seconds."
    except OSError as exc:
        return False, str(exc)
    output = _clean_output(completed.stdout or completed.stderr or "")
    if completed.returncode != 0:
        return False, output[-500:] or f"Exit code {completed.returncode}."
    return True, output[-500:]


def _clean_output(output: str) -> str:
    """Remove terminal progress animation and control sequences from UI text."""
    cleaned = _ANSI_ESCAPE.sub("", output or "").replace("\r", "\n")
    lines = []
    for raw_line in cleaned.splitlines():
        line = "".join(char for char in raw_line if char.isprintable()).strip()
        if not line or line == "�":
            continue
        # Package managers render animated progress bars using block glyphs.
        non_progress = re.sub(r"[\u2580-\u259f\s\d%./-]", "", line)
        if not non_progress:
            continue
        lines.append(line)
    return "\n".join(lines)
