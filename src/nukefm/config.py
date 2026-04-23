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
    market_price_range_multiple: str
    market_rollover_boundary_rate: str
    market_rollover_liquidity_transfer_fraction: str
    solana_rpc_url: str
    solana_usdc_mint: str
    secret_tool_service: str
    deposit_master_seed_secret_name: str
    treasury_seed_secret_name: str
    jupiter_tokens_base_url: str = "https://api.jup.ag/tokens/v2"
    bags_api_base_url: str = "https://public-api-v2.bags.fm/api/v1"
    bags_api_key: str | None = None


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
        market_price_range_multiple=config["market_price_range_multiple"],
        market_rollover_boundary_rate=config["market_rollover_boundary_rate"],
        market_rollover_liquidity_transfer_fraction=config["market_rollover_liquidity_transfer_fraction"],
        jupiter_tokens_base_url=config.get("jupiter_tokens_base_url", "https://api.jup.ag/tokens/v2").rstrip("/"),
        bags_api_base_url=config.get("bags_api_base_url", "https://public-api-v2.bags.fm/api/v1").rstrip("/"),
        bags_api_key=os.environ.get("BAGS_API_KEY"),
        solana_rpc_url=config["solana_rpc_url"],
        solana_usdc_mint=config["solana_usdc_mint"],
        secret_tool_service=config["secret_tool_service"],
        deposit_master_seed_secret_name=config["deposit_master_seed_secret_name"],
        treasury_seed_secret_name=config["treasury_seed_secret_name"],
    )
