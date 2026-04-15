from __future__ import annotations

import argparse
from decimal import Decimal

import uvicorn
from loguru import logger

from .accounts import AccountStore
from .app import create_app
from .bags import BagsClient
from .catalog import Catalog
from .config import load_settings
from .logging_utils import configure_logging
from .markets import MarketStore
from .settlement import BitquerySettlementPriceClient
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
    subparsers.add_parser("snapshot-markets")
    subparsers.add_parser("resolve-markets")

    process_withdrawals_parser = subparsers.add_parser("process-withdrawals")
    process_withdrawals_parser.add_argument("--limit", type=int, default=100)

    arguments = parser.parse_args()
    settings = load_settings()
    configure_logging(settings.log_path)

    if arguments.command == "serve":
        uvicorn.run("nukefm.app:create_app", factory=True, host=arguments.host, port=arguments.port)
        return

    catalog = Catalog(settings.database_path)
    catalog.initialize()
    account_store = AccountStore(settings.database_path)
    account_store.initialize()
    market_store = MarketStore(
        settings.database_path,
        market_duration_days=settings.market_duration_days,
        threshold_fraction=Decimal(settings.market_threshold_fraction),
    )
    market_store.initialize()
    treasury = SolanaTreasury(
        rpc_url=settings.solana_rpc_url,
        usdc_mint=settings.solana_usdc_mint,
        secret_tool_service=settings.secret_tool_service,
        deposit_master_seed_secret_name=settings.deposit_master_seed_secret_name,
        treasury_seed_secret_name=settings.treasury_seed_secret_name,
    )

    if arguments.command == "sync-deposits":
        credited_deposits = treasury.reconcile_deposits(account_store)
        logger.info(f"Credited {len(credited_deposits)} deposit balance changes.")
        return

    if arguments.command == "sync-market-liquidity":
        market_store.ensure_missing_market_liquidity_accounts(treasury)
        credited_deposits = treasury.reconcile_market_liquidity(market_store)
        logger.info(f"Credited {len(credited_deposits)} market liquidity balance changes.")
        return

    if arguments.command == "process-withdrawals":
        processed_withdrawals = treasury.process_withdrawals(account_store, limit=arguments.limit)
        logger.info(f"Processed {len(processed_withdrawals)} withdrawals.")
        return

    if arguments.command == "snapshot-markets":
        if settings.bitquery_api_key is None:
            raise SystemExit("BITQUERY_API_KEY is required to snapshot markets from settlement trade data.")
        snapshots = market_store.capture_hourly_snapshots(
            BitquerySettlementPriceClient(api_key=settings.bitquery_api_key),
        )
        logger.info(f"Captured {len(snapshots)} market snapshot updates.")
        return

    if arguments.command == "resolve-markets":
        resolved = market_store.resolve_markets(catalog=catalog, treasury=treasury)
        logger.info(f"Resolved {len(resolved)} markets.")
        return

    if settings.bags_api_key is None:
        raise SystemExit("BAGS_API_KEY is required to ingest the Bags launch feed.")

    client = BagsClient(
        base_url=settings.bags_base_url,
        feed_path=settings.bags_launch_feed_path,
        api_key=settings.bags_api_key,
    )
    ingested_count = catalog.ingest_tokens(client.list_tokens(limit=arguments.limit))
    market_store.ensure_missing_market_liquidity_accounts(treasury)
    logger.info(f"Ingested {ingested_count} Bags tokens into the market catalog.")


if __name__ == "__main__":
    main()
