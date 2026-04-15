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
    api_challenge_ttl_seconds: int
    market_duration_days: int
    market_resolution_threshold_fraction: str
    market_rollover_lower_bound_fraction: str
    market_rollover_upper_bound_fraction: str
    bags_base_url: str
    bags_launch_feed_path: str
    bags_api_key: str | None
    dexscreener_base_url: str
    solana_rpc_url: str
    solana_usdc_mint: str
    secret_tool_service: str
    deposit_master_seed_secret_name: str
    treasury_seed_secret_name: str
    jupiter_tokens_base_url: str = "https://api.jup.ag/tokens/v2"


def load_settings() -> Settings:
    load_dotenv(ROOT_DIR / ".env")
    config = json.loads(CONFIG_PATH.read_text())

    return Settings(
        app_name=config["app_name"],
        database_path=ROOT_DIR / config["database_path"],
        log_path=ROOT_DIR / config["log_path"],
        frontend_refresh_seconds=config["frontend_refresh_seconds"],
        api_challenge_ttl_seconds=config["api_challenge_ttl_seconds"],
        market_duration_days=config["market_duration_days"],
        market_resolution_threshold_fraction=config["market_resolution_threshold_fraction"],
        market_rollover_lower_bound_fraction=config["market_rollover_lower_bound_fraction"],
        market_rollover_upper_bound_fraction=config["market_rollover_upper_bound_fraction"],
        bags_base_url=config["bags_base_url"].rstrip("/"),
        bags_launch_feed_path=config["bags_launch_feed_path"],
        bags_api_key=os.getenv("BAGS_API_KEY") or None,
        dexscreener_base_url=config["dexscreener_base_url"].rstrip("/"),
        jupiter_tokens_base_url=config.get("jupiter_tokens_base_url", "https://api.jup.ag/tokens/v2").rstrip("/"),
        solana_rpc_url=config["solana_rpc_url"],
        solana_usdc_mint=config["solana_usdc_mint"],
        secret_tool_service=config["secret_tool_service"],
        deposit_master_seed_secret_name=config["deposit_master_seed_secret_name"],
        treasury_seed_secret_name=config["treasury_seed_secret_name"],
    )
