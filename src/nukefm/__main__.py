from __future__ import annotations

import argparse

import uvicorn
from loguru import logger

from .accounts import AccountStore
from .app import create_app
from .bags import BagsClient
from .catalog import Catalog
from .config import load_settings
from .logging_utils import configure_logging
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

    if arguments.command == "sync-deposits":
        treasury = SolanaTreasury(
            rpc_url=settings.solana_rpc_url,
            usdc_mint=settings.solana_usdc_mint,
            secret_tool_service=settings.secret_tool_service,
            deposit_master_seed_secret_name=settings.deposit_master_seed_secret_name,
            treasury_seed_secret_name=settings.treasury_seed_secret_name,
        )
        credited_deposits = treasury.reconcile_deposits(account_store)
        logger.info(f"Credited {len(credited_deposits)} deposit balance changes.")
        return

    if arguments.command == "process-withdrawals":
        treasury = SolanaTreasury(
            rpc_url=settings.solana_rpc_url,
            usdc_mint=settings.solana_usdc_mint,
            secret_tool_service=settings.secret_tool_service,
            deposit_master_seed_secret_name=settings.deposit_master_seed_secret_name,
            treasury_seed_secret_name=settings.treasury_seed_secret_name,
        )
        processed_withdrawals = treasury.process_withdrawals(account_store, limit=arguments.limit)
        logger.info(f"Processed {len(processed_withdrawals)} withdrawals.")
        return

    if settings.bags_api_key is None:
        raise SystemExit("BAGS_API_KEY is required to ingest the Bags launch feed.")

    client = BagsClient(
        base_url=settings.bags_base_url,
        feed_path=settings.bags_launch_feed_path,
        api_key=settings.bags_api_key,
    )
    ingested_count = catalog.ingest_tokens(client.list_tokens(limit=arguments.limit))
    logger.info(f"Ingested {ingested_count} Bags tokens into the market catalog.")


if __name__ == "__main__":
    main()
