"""Settings and download history, stored in the user's app-data folder."""

import json
import os

from pathlib import Path


APP_DIR      = Path(os.environ.get("APPDATA", Path.home())) / "YTDownloader"


HISTORY_FILE = APP_DIR / "history.json"


CONFIG_FILE  = APP_DIR / "config.json"


APP_DIR.mkdir(parents=True, exist_ok=True)


DEFAULT_OUTPUT = str(Path.home() / "Videos" / "YT Downloads")


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"default_output": DEFAULT_OUTPUT, "default_quality": "best"}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def load_history() -> list:
    """History is a convenience: a missing or damaged file must never break a download."""
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_history(entry: dict):
    history = load_history()
    history.append(entry)
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
