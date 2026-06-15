import json
import platform
import shutil
from pathlib import Path
import platformdirs
import keyring

APP_NAME = "ARIA"
APP_AUTHOR = "ArnavParashar49"

_LEGACY_CONFIG_PATH = Path(__file__).parent / "api_keys.json"

def get_config_dir() -> Path:
    config_dir = Path(platformdirs.user_config_dir(APP_NAME, APP_AUTHOR))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir

def get_config_path() -> Path:
    return get_config_dir() / "config.json"

_VALID_OS = {"windows", "mac", "linux"}
_PLATFORM_OS = {"Darwin": "mac", "Windows": "windows", "Linux": "linux"}

def _migrate_legacy_config() -> None:
    new_path = get_config_path()
    if not new_path.exists() and _LEGACY_CONFIG_PATH.exists():
        try:
            shutil.copy2(_LEGACY_CONFIG_PATH, new_path)
            # Try to migrate the api key to keyring
            with open(_LEGACY_CONFIG_PATH, "r", encoding="utf-8") as f:
                legacy_data = json.load(f)
                gemini_key = legacy_data.get("gemini_api_key", "")
                if gemini_key and "your" not in gemini_key.lower():
                    set_api_key("gemini_api_key", gemini_key)
        except Exception:
            pass

def get_config() -> dict:
    _migrate_legacy_config()
    path = get_config_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_config(cfg: dict) -> None:
    path = get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)

def get_api_key(name: str = "gemini_api_key", *, required: bool = True) -> str:
    # 1. Try keyring
    key = None
    try:
        key = keyring.get_password(APP_NAME, name)
    except Exception:
        pass
        
    if key:
        return key
        
    # 2. Try config file
    cfg = get_config()
    key = cfg.get(name, "")
    
    if key and "your" not in key.lower():
        # save to keyring for future
        set_api_key(name, key)
        return key

    if required:
        raise KeyError(f"Missing API key: {name}")
    return ""

def set_api_key(name: str, value: str) -> None:
    try:
        keyring.set_password(APP_NAME, name, value)
    except Exception:
        # Fallback to config if keyring fails
        cfg = get_config()
        cfg[name] = value
        save_config(cfg)

def detect_os() -> str:
    return _PLATFORM_OS.get(platform.system(), "linux")

def get_os() -> str:
    try:
        override = str(get_config().get("os_system", "")).strip().lower()
    except Exception:
        override = ""
    return override if override in _VALID_OS else detect_os()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"
