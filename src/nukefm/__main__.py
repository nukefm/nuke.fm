from __future__ import annotations

import argparse

import uvicorn
from loguru import logger

from .app import create_app
from .bags import BagsClient
from .catalog import Catalog
from .config import load_settings
from .logging_utils import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="nukefm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("--limit", type=int, default=100)

    arguments = parser.parse_args()
    settings = load_settings()
    configure_logging(settings.log_path)

    if arguments.command == "serve":
        uvicorn.run("nukefm.app:create_app", factory=True, host=arguments.host, port=arguments.port)
        return

    if settings.bags_api_key is None:
        raise SystemExit("BAGS_API_KEY is required to ingest the Bags launch feed.")

    client = BagsClient(
        base_url=settings.bags_base_url,
        feed_path=settings.bags_launch_feed_path,
        api_key=settings.bags_api_key,
    )
    catalog = Catalog(settings.database_path)
    catalog.initialize()
    ingested_count = catalog.ingest_tokens(client.list_tokens(limit=arguments.limit))
    logger.info(f"Ingested {ingested_count} Bags tokens into the market catalog.")


if __name__ == "__main__":
    main()
