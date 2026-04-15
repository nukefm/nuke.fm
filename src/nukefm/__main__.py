from __future__ import annotations

import argparse
from decimal import Decimal

import uvicorn
from loguru import logger

from .accounts import AccountStore
from .amounts import parse_usdc_amount
from .app import create_app
from .bags import BagsClient
from .catalog import Catalog
from .config import load_settings
from .dexscreener import DexScreenerClient
from .jupiter import JupiterTokensClient
from .logging_utils import configure_logging
from .markets import MarketStore
from .settlement import JupiterChartsSettlementPriceClient
from .treasury import SolanaTreasury


def main() -> None:
    parser = argparse.ArgumentParser(prog="nukefm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("--limit", type=int, default=100)

    subparsers.add_parser("sync-deposits")
    subparsers.add_parser("sync-market-liquidity")
    subparsers.add_parser("sync-token-metrics")
    subparsers.add_parser("snapshot-markets")
    subparsers.add_parser("snapshot-market-charts")
    subparsers.add_parser("resolve-markets")

    seed_weekly_liquidity_parser = subparsers.add_parser("seed-weekly-liquidity")
    seed_weekly_liquidity_parser.add_argument("--top", type=int, default=10)
    seed_weekly_liquidity_parser.add_argument("--amount-usdc", default="1")

    record_treasury_funding_parser = subparsers.add_parser("record-treasury-funding")
    record_treasury_funding_parser.add_argument("--amount-usdc", required=True)
    record_treasury_funding_parser.add_argument("--note")

    process_withdrawals_parser = subparsers.add_parser("process-withdrawals")
    process_withdrawals_parser.add_argument("--limit", type=int, default=100)

    arguments = parser.parse_args()
    settings = load_settings()
    configure_logging(settings.log_path)

    if arguments.command == "serve":
        uvicorn.run(
            "nukefm.app:create_app",
            factory=True,
            host=arguments.host,
            port=arguments.port,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
        return

    catalog = Catalog(settings.database_path)
    catalog.initialize()
    account_store = AccountStore(settings.database_path)
    account_store.initialize()
    market_store = MarketStore(
        settings.database_path,
        market_duration_days=settings.market_duration_days,
        resolution_threshold_fraction=Decimal(settings.market_resolution_threshold_fraction),
        rollover_lower_bound_fraction=Decimal(settings.market_rollover_lower_bound_fraction),
        rollover_upper_bound_fraction=Decimal(settings.market_rollover_upper_bound_fraction),
    )
    market_store.initialize()
    treasury: SolanaTreasury | None = None

    def get_treasury() -> SolanaTreasury:
        nonlocal treasury
        if treasury is None:
            treasury = SolanaTreasury(
                rpc_url=settings.solana_rpc_url,
                usdc_mint=settings.solana_usdc_mint,
                secret_tool_service=settings.secret_tool_service,
                deposit_master_seed_secret_name=settings.deposit_master_seed_secret_name,
                treasury_seed_secret_name=settings.treasury_seed_secret_name,
            )
        return treasury

    if arguments.command == "sync-deposits":
        credited_deposits = get_treasury().reconcile_deposits(account_store)
        logger.info(f"Credited {len(credited_deposits)} deposit balance changes.")
        return

    if arguments.command == "sync-market-liquidity":
        treasury_instance = get_treasury()
        market_store.ensure_missing_market_liquidity_accounts(treasury_instance)
        credited_deposits = treasury_instance.reconcile_market_liquidity(market_store)
        logger.info(f"Credited {len(credited_deposits)} market liquidity balance changes.")
        return

    if arguments.command == "sync-token-metrics":
        captured_metrics = market_store.capture_token_metrics(
            JupiterTokensClient(base_url=settings.jupiter_tokens_base_url),
        )
        logger.info(f"Captured {len(captured_metrics)} token metric snapshots.")
        return

    if arguments.command == "process-withdrawals":
        processed_withdrawals = get_treasury().process_withdrawals(account_store, limit=arguments.limit)
        logger.info(f"Processed {len(processed_withdrawals)} withdrawals.")
        return

    if arguments.command == "snapshot-markets":
        snapshots = market_store.capture_hourly_snapshots(
            JupiterChartsSettlementPriceClient(),
        )
        logger.info(f"Captured {len(snapshots)} market snapshot updates.")
        return

    if arguments.command == "snapshot-market-charts":
        snapshots = market_store.capture_market_chart_snapshots(
            JupiterTokensClient(base_url=settings.jupiter_tokens_base_url),
        )
        logger.info(f"Captured {len(snapshots)} market chart snapshot updates.")
        return

    if arguments.command == "resolve-markets":
        resolved = market_store.resolve_markets(catalog=catalog, treasury=get_treasury())
        logger.info(f"Resolved {len(resolved)} markets.")
        return

    if arguments.command == "seed-weekly-liquidity":
        seeded_markets = market_store.seed_top_markets_by_market_cap(
            amount_atomic=parse_usdc_amount(arguments.amount_usdc),
            limit=arguments.top,
        )
        logger.info(
            "Debt-funded weekly seeds applied to {} markets. Outstanding treasury debt is {} USDC.",
            len(seeded_markets),
            market_store.get_outstanding_treasury_debt_usdc(),
        )
        return

    if arguments.command == "record-treasury-funding":
        funding = market_store.record_treasury_funding(
            amount_atomic=parse_usdc_amount(arguments.amount_usdc),
            note=arguments.note,
        )
        logger.info(
            "Recorded treasury funding of {} USDC. Remaining auto-seed debt is {} USDC.",
            funding["funded_amount_usdc"],
            funding["remaining_debt_usdc"],
        )
        return

    if settings.bags_api_key is None:
        raise SystemExit("BAGS_API_KEY is required to ingest the Bags launch feed.")

    client = BagsClient(
        base_url=settings.bags_base_url,
        feed_path=settings.bags_launch_feed_path,
        api_key=settings.bags_api_key,
    )
    ingested_count = catalog.ingest_tokens(client.list_tokens(limit=arguments.limit))
    captured_metrics = market_store.capture_token_metrics(
        JupiterTokensClient(base_url=settings.jupiter_tokens_base_url),
    )
    market_store.ensure_missing_market_liquidity_accounts(get_treasury())
    logger.info(
        "Ingested {} Bags tokens into the market catalog and refreshed {} token metric snapshots.",
        ingested_count,
        len(captured_metrics),
    )


if __name__ == "__main__":
    main()
