"""Startup validation — verifies all dependencies before NEO runs.

Checks: API keys, ChromaDB, Vosk model, YOLO weights, Playwright.
Returns clear, actionable error messages.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.paths import base_dir

logger = logging.getLogger(__name__)


class CheckStatus(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str = ""
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class StartupReport:
    results: list[CheckResult] = field(default_factory=list)
    all_pass: bool = True
    total_duration_ms: float = 0.0

    def to_lines(self) -> list[str]:
        lines = ["── NEO Startup Check ──"]
        icons = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}
        for r in self.results:
            icon = icons[r.status.value]
            lines.append(f"  {icon} {r.name}: {r.message}")
            if r.detail:
                lines.append(f"     {r.detail}")
        status = "✅ All checks passed" if self.all_pass else "❌ Some checks failed"
        lines.append(f"\n  {status}")
        return lines


def run_all_checks(*, verbose: bool = True) -> StartupReport:
    """Run all startup validation checks."""
    report = StartupReport()
    started = time.monotonic()

    for check_fn in [
        _check_gemini_key, _check_chromadb, _check_vosk,
        _check_yolo, _check_playwright, _check_filesystem,
    ]:
        t0 = time.monotonic()
        try:
            result = check_fn()
        except Exception as exc:
            result = CheckResult(
                name=check_fn.__name__.replace("_check_", ""),
                status=CheckStatus.FAIL,
                message=str(exc),
            )
        result.duration_ms = (time.monotonic() - t0) * 1000
        report.results.append(result)
        if result.status == CheckStatus.FAIL:
            report.all_pass = False
        if verbose:
            logger.info("[%s] %s: %s", result.name, result.status.value, result.message)

    report.total_duration_ms = (time.monotonic() - started) * 1000
    return report


def run_quick_check() -> bool:
    """Quick pass/fail — returns True if all critical checks pass."""
    report = run_all_checks(verbose=False)
    return report.all_pass


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_gemini_key() -> CheckResult:
    try:
        from config import get_api_key
        key = get_api_key("gemini_api_key", required=False)
        if not key:
            return CheckResult("gemini_key", CheckStatus.FAIL,
                               "No Gemini API key",
                               "Set GEMINI_API_KEY in .env or config/api_keys.json")
        if any(m in key.lower() for m in ("your", "paste", "changeme", "xxx")):
            return CheckResult("gemini_key", CheckStatus.FAIL,
                               "API key is a placeholder",
                               "Get your real key from aistudio.google.com")
        return CheckResult("gemini_key", CheckStatus.PASS, "Ready")
    except ImportError:
        return CheckResult("gemini_key", CheckStatus.WARN, "Config module unavailable")


def _check_chromadb() -> CheckResult:
    try:
        import chromadb  # noqa: F401
        from core.memory_rag import _init_db, _collection  # noqa: F401
        _init_db()
        if _collection is not None:
            return CheckResult("chromadb", CheckStatus.PASS, "Ready")
        return CheckResult("chromadb", CheckStatus.WARN, "Collection not initialised")
    except ImportError:
        return CheckResult("chromadb", CheckStatus.WARN,
                           "Not installed — memory disabled", "pip install chromadb")
    except Exception as exc:
        return CheckResult("chromadb", CheckStatus.WARN, str(exc)[:60])


def _check_vosk() -> CheckResult:
    vosk_dir = base_dir() / "models" / "vosk-model-small-en-us-0.15"
    if (vosk_dir / "am").exists() or (vosk_dir / "graph").exists():
        return CheckResult("vosk", CheckStatus.PASS, "Ready")
    try:
        import vosk  # noqa: F401
        return CheckResult("vosk", CheckStatus.WARN,
                           "Not downloaded yet (~40 MB)")
    except ImportError:
        return CheckResult("vosk", CheckStatus.WARN,
                           "Not installed — wake word disabled", "pip install vosk")


def _check_yolo() -> CheckResult:
    if (base_dir() / "models" / "yolov8n.pt").exists():
        return CheckResult("yolo", CheckStatus.PASS, "Ready")
    return CheckResult("yolo", CheckStatus.WARN, "Not downloaded yet")


def _check_playwright() -> CheckResult:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return CheckResult("playwright", CheckStatus.PASS, "Ready")
    except ImportError:
        return CheckResult("playwright", CheckStatus.WARN,
                           "Not installed — browser limited",
                           "pip install playwright && playwright install")


def _check_filesystem() -> CheckResult:
    try:
        for d in ["memory", "memory/chroma", "models", "config"]:
            (base_dir() / d).mkdir(parents=True, exist_ok=True)
        return CheckResult("filesystem", CheckStatus.PASS, "Ready")
    except Exception as exc:
        return CheckResult("filesystem", CheckStatus.FAIL, str(exc)[:60])

