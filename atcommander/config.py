from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIR = Path.home() / ".atcommander"
APP_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = APP_DIR / "settings.json"
BROWSER_PROFILE_DIR = APP_DIR / "browser_profile"
BROWSER_CACHE_DIR = APP_DIR / "browser_cache"
STATS_FILE = APP_DIR / "stats.jsonl"


@dataclass
class Settings:
    game_url: str = "https://www.kongregate.com/en/games/throwdown/animation-throwdown"
    auto_battle_count: int = 5
    delay_seconds: float = 0.75
    arena_battles: int = 10
    adventure_battles: int = 10
    adventure_mission_id: int = 180
    stop_on_error: bool = True
    refresh_browser_after_batch: bool = True
    devtools_port: int = 9222


def load_settings() -> Settings:
    if not SETTINGS_FILE.exists():
        s = Settings()
        save_settings(s)
        return s
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        defaults = asdict(Settings())
        defaults.update({k: v for k, v in data.items() if k in defaults})
        return Settings(**defaults)
    except Exception:
        return Settings()


def save_settings(settings: Settings) -> None:
    SETTINGS_FILE.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
