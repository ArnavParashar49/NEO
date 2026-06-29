"""NEO configuration — loads `.env`, keyring, and legacy config files."""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path

import keyring
import platformdirs

APP_NAME = "NEO"
APP_AUTHOR = "ArnavParashar49"

_LEGACY_CONFIG_PATH = Path(__file__).parent / "api_keys.json"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

# Internal name -> environment variable
_ENV_KEY_ALIASES: dict[str, str] = {
    "gemini_api_key": "GEMINI_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "groq_api_key": "GROQ_API_KEY",
    "cometapi_api_key": "COMETAPI_API_KEY",
    "nvidia_nim_api_key": "NVIDIA_NIM_API_KEY",
    "nvidia_nim_kimi_api_key": "NVIDIA_NIM_KIMI_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "exa_api_key": "EXA_API_KEY",

}

_PLACEHOLDER_MARKERS = ("your", "paste-key", "changeme", "xxx")


def _load_dotenv() -> None:
    if not _ENV_FILE.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE, override=False)
    except ImportError:
        # Minimal parser if python-dotenv is not installed yet.
        try:
            for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except Exception:
            pass


_load_dotenv()

DEFAULT_SEARCH_ENGINE = (
    os.getenv("NEO_DEFAULT_SEARCH_ENGINE") or "google"
).strip().lower()

SEARCH_ENGINE_BASES: dict[str, str] = {
    "google": "https://www.google.com/search?q=",
    "bing": "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "yandex": "https://yandex.com/search/?text=",
}


def search_engine_url(query: str, engine: str | None = None, *, images: bool = False) -> str:
    """Build a search URL for the given query (default engine: Google)."""
    from urllib.parse import quote_plus

    eng = (engine or DEFAULT_SEARCH_ENGINE).lower()
    base = SEARCH_ENGINE_BASES.get(eng, SEARCH_ENGINE_BASES[DEFAULT_SEARCH_ENGINE])
    url = base + quote_plus((query or "").strip())
    if images:
        if eng == "google":
            url += "&tbm=isch"
        elif eng == "duckduckgo":
            url += "&iax=images&ia=images"
    return url


def get_default_location() -> str | None:
    """User's default city for weather — .env, then config.json."""
    env_loc = (os.getenv("NEO_DEFAULT_LOCATION") or "").strip()
    if env_loc:
        return env_loc
    cfg = get_config()
    for key in ("default_location", "location", "city", "home_city"):
        val = (cfg.get(key) or "").strip()
        if val:
            return val
    user = cfg.get("user")
    if isinstance(user, dict):
        for key in ("location", "city", "default_location"):
            val = (user.get(key) or "").strip()
            if val:
                return val
    return None


def set_default_location(city: str) -> None:
    """Remember the user's city for weather follow-ups."""
    city = (city or "").strip()
    if not city:
        return
    cfg = get_config()
    cfg["default_location"] = city
    save_config(cfg)


def _sync_keyring_keys_to_env() -> None:
    """One-way sync: copy keys stored in keyring into `.env` when missing there."""
    for internal_name, env_name in _ENV_KEY_ALIASES.items():
        if _env_value(internal_name):
            continue
        try:
            key = keyring.get_password(APP_NAME, internal_name)
        except Exception:
            key = None
        if key and not _is_placeholder(key):
            set_env_var(env_name, key)


_VALID_OS = {"windows", "mac", "linux"}
_PLATFORM_OS = {"Darwin": "mac", "Windows": "windows", "Linux": "linux"}


def project_root() -> Path:
    return _PROJECT_ROOT


def env_file_path() -> Path:
    return _ENV_FILE


def _is_placeholder(value: str) -> bool:
    low = (value or "").strip().lower()
    if not low:
        return True
    return any(marker in low for marker in _PLACEHOLDER_MARKERS)


def _env_value(name: str) -> str:
    env_name = _ENV_KEY_ALIASES.get(name, name.upper())
    return os.environ.get(env_name, "").strip()


def get_config_dir() -> Path:
    config_dir = Path(platformdirs.user_config_dir(APP_NAME, APP_AUTHOR))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def _migrate_legacy_config() -> None:
    new_path = get_config_path()
    if not new_path.exists() and _LEGACY_CONFIG_PATH.exists():
        try:
            shutil.copy2(_LEGACY_CONFIG_PATH, new_path)
        except Exception:
            pass


def get_config() -> dict:
    _migrate_legacy_config()
    path = get_config_path()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    if _LEGACY_CONFIG_PATH.exists():
        try:
            with open(_LEGACY_CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    path = get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)


def get_api_key(name: str = "gemini_api_key", *, required: bool = True) -> str:
    """Resolve an API key: .env -> keyring -> config.json -> legacy api_keys.json."""
    key = _env_value(name)
    if key and not _is_placeholder(key):
        return key

    try:
        key = keyring.get_password(APP_NAME, name)
        if key and not _is_placeholder(key):
            return key
    except Exception:
        pass

    cfg = get_config()
    key = (cfg.get(name) or "").strip()
    if key and not _is_placeholder(key):
        return key

    if _LEGACY_CONFIG_PATH.exists() and name in cfg:
        pass  # already tried via get_config fallback

    if required:
        env_name = _ENV_KEY_ALIASES.get(name, name.upper())
        raise KeyError(
            f"Missing API key: {name}. Set {env_name} in .env "
            f"(see .env.example)."
        )
    return ""


def set_api_key(name: str, value: str) -> None:
    env_name = _ENV_KEY_ALIASES.get(name, name.upper())
    os.environ[env_name] = value
    set_env_var(env_name, value)
    try:
        keyring.set_password(APP_NAME, name, value)
    except Exception:
        cfg = get_config()
        cfg[name] = value
        save_config(cfg)


def set_env_var(key: str, value: str) -> None:
    """Upsert a single KEY=value line in the project `.env` file."""
    key = key.strip()
    value = (value or "").strip()
    lines: list[str] = []
    if _ENV_FILE.exists():
        lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        k = line.split("=", 1)[0].strip()
        if k == key:
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    _ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    os.environ[key] = value


def detect_os() -> str:
    return _PLATFORM_OS.get(platform.system(), "linux")


def get_os() -> str:
    env_os = os.environ.get("OS_SYSTEM", "").strip().lower()
    if env_os in _VALID_OS:
        return env_os
    try:
        override = str(get_config().get("os_system", "")).strip().lower()
    except Exception:
        override = ""
    return override if override in _VALID_OS else detect_os()


def is_windows() -> bool:
    return get_os() == "windows"


def is_mac() -> bool:
    return get_os() == "mac"


def is_linux() -> bool:
    return get_os() == "linux"


_sync_keyring_keys_to_env()
