from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "config.json"


@dataclass(frozen=True)
class Settings:
    app_name: str
    database_path: Path
    log_path: Path
    frontend_refresh_seconds: int
    bags_base_url: str
    bags_launch_feed_path: str
    bags_api_key: str | None


def load_settings() -> Settings:
    load_dotenv(ROOT_DIR / ".env")
    config = json.loads(CONFIG_PATH.read_text())

    return Settings(
        app_name=config["app_name"],
        database_path=ROOT_DIR / config["database_path"],
        log_path=ROOT_DIR / config["log_path"],
        frontend_refresh_seconds=config["frontend_refresh_seconds"],
        bags_base_url=config["bags_base_url"].rstrip("/"),
        bags_launch_feed_path=config["bags_launch_feed_path"],
        bags_api_key=os.getenv("BAGS_API_KEY") or None,
    )
