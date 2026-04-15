# nuke.fm

Read-only MVP for the nuke.fm prediction market catalog.

## Runtime

- Python 3.13
- `BAGS_API_KEY` in `.env` for feed ingestion

## Commands

- `uv sync`
- `uv run --env-file .env python -m nukefm ingest --limit 100`
- `uv run --env-file .env python -m nukefm serve --host 127.0.0.1 --port 8000`

## Public Surface

- `GET /v1/public/tokens`
- `GET /v1/public/tokens/{mint}`
- `GET /`
- `GET /tokens/{mint}`

The first deliverable persists Bags token metadata, creates one current market per token in `awaiting_liquidity`, and renders that catalog through the public API and the read-only frontend. Liquidity seeding, XYK pricing, and settlement metrics are deferred to the later MVP stages.

If Bags changes the launch-feed route, update `bags_launch_feed_path` in `config.json` without changing application code.
