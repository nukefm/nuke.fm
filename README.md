# nuke.fm

Read-only MVP for the nuke.fm prediction market catalog.

## Goal

nuke.fm is an offchain prediction market product for Bags tokens that settles in Solana USDC.
Each token has a rolling series of markets that ask whether the token will nuke within the
next 90 days after that market opens.

This repository contains the first MVP slice:

- ingest Bags token metadata into a local market catalog
- create and maintain one current market per token
- expose that catalog through a public JSON API
- render the same catalog through a read-only web frontend

The frontend publishes market state only. Trading, balances, deposits, withdrawals, AMM state,
and settlement logic are separate later deliverables.

## High-Level Model

- The canonical token identifier is the mint address.
- Every token has one current market and zero or more past markets.
- A new token starts with a current market in `awaiting_liquidity`.
- When a market resolves, the next market for that same token is created immediately.
- The public market term is `nuke`, not `rug`.

## How It Works

At a high level, the app has three moving parts.

First, the ingestion command pulls token launch data from the Bags launch feed and stores token
metadata in a local SQLite database.

Second, the catalog layer ensures each token has exactly one active current market. For the first
deliverable, that means creating a market record in `awaiting_liquidity` if no current market
exists yet, and rolling the series forward when a market is resolved.

Third, the FastAPI app reads that catalog and serves it in two forms:

- JSON endpoints under `/v1/public`
- HTML pages for the market list and token detail views

The same underlying catalog powers both surfaces, so the frontend stays a thin read-only view over
the public market data.

## Current Scope

The current implementation intentionally does not invent data that belongs to later MVP stages.
That means current-market price, liquidity address, reference price, ATH, drawdown, and threshold
fields remain explicitly unavailable until the AMM, liquidity, and settlement systems exist.

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

The first deliverable persists Bags token metadata, creates one current market per token in
`awaiting_liquidity`, and renders that catalog through the public API and the read-only frontend.

If Bags changes the launch-feed route, update `bags_launch_feed_path` in `config.json` without changing application code.
