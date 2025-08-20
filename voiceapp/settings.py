from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Dict, Optional


APP_NAME = "VoiceApp"


def config_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        # Fallback to standard Roaming path
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    # Linux / other
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME.lower()
    return Path.home() / ".config" / APP_NAME.lower()


def config_file() -> Path:
    return config_dir() / "config.json"


def load_settings() -> Dict[str, str]:
    cf = config_file()
    if not cf.exists():
        return {}
    try:
        return json.loads(cf.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: Dict[str, str]) -> Path:
    cd = config_dir()
    cd.mkdir(parents=True, exist_ok=True)
    cf = cd / "config.json"
    cf.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return cf


TXT_CANDIDATES = ("openai.txt", "openai.key", "OPENAI_API_KEY.txt")


def _read_key_file(path: Path) -> Optional[str]:
    try:
        if path.exists() and path.is_file():
            val = path.read_text(encoding="utf-8").strip()
            return val or None
    except Exception:
        return None
    return None


def resolve_openai_key() -> Optional[str]:
    # 1) Environment variable (includes .env once loaded by CLI)
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key

    # 2) Per-user JSON config
    data = load_settings()
    if data.get("OPENAI_API_KEY"):
        return data.get("OPENAI_API_KEY")

    # 3) Simple text files in CWD
    cwd = Path.cwd()
    for name in TXT_CANDIDATES:
        k = _read_key_file(cwd / name)
        if k:
            return k

    # 4) Simple text files in config dir
    cd = config_dir()
    for name in TXT_CANDIDATES:
        k = _read_key_file(cd / name)
        if k:
            return k

    return None
