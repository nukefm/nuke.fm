# nuke.fm

Prediction market MVP for nuke.fm.

## Goal

nuke.fm is an offchain prediction market product for Bags tokens that settles in Solana USDC.
Each token has a rolling series of markets that ask whether the token will nuke within the
next 90 days after that market opens.

This repository currently includes two MVP slices:

- ingest Bags token metadata into a local market catalog
- create and maintain one current market per token
- expose that catalog through a public JSON API
- render the same catalog through a read-only web frontend
- bootstrap private API access with Solana wallet signatures and API keys
- issue per-user USDC deposit addresses
- reconcile credited deposits into an internal ledger
- accept withdrawals and process them through the operator CLI
- expose account and portfolio data through the private API

The frontend still publishes market state only. Trading, AMM state, and settlement logic remain
later deliverables.

## High-Level Model

- The canonical token identifier is the mint address.
- Every token has one current market and zero or more past markets.
- A new token starts with a current market in `awaiting_liquidity`.
- When a market resolves, the next market for that same token is created immediately.
- The public market term is `nuke`, not `rug`.

## How It Works

At a high level, the app now has five moving parts.

First, the ingestion command pulls token launch data from the Bags launch feed and stores token
metadata in a local SQLite database.

Second, the catalog layer ensures each token has exactly one active current market. For the first
deliverable, that means creating a market record in `awaiting_liquidity` if no current market
exists yet, and rolling the series forward when a market is resolved.

Third, the auth layer issues one-time challenges, verifies Solana wallet signatures, and mints
API keys for private access.

Fourth, the treasury layer derives deterministic per-user deposit wallets from a master seed in
`secret-tool`, ensures each user has a USDC associated token account, reconciles deposit balance
changes, and broadcasts withdrawals from the platform treasury wallet.

Fifth, the FastAPI app reads the catalog and account ledger and serves them in two forms:

- JSON endpoints under `/v1/public`
- JSON endpoints under `/v1/auth` and `/v1/private`
- HTML pages for the market list and token detail views

The same database backs the public catalog and the private ledger, while the frontend stays a thin
read-only view over public market data.

## Current Scope

The current implementation intentionally does not invent data that belongs to later MVP stages.
That means current-market price, liquidity address, reference price, ATH, drawdown, and threshold
fields remain explicitly unavailable until the AMM, liquidity, and settlement systems exist.

The private portfolio surface is also intentionally narrow at this stage. API auth, deposit
addresses, deposit history, withdrawals, and cash balance are implemented now. Positions and trade
history are real endpoints already, but they return empty arrays until the trading engine exists.

## Runtime

- Python 3.13
- `BAGS_API_KEY` in `.env` for feed ingestion
- `secret-tool` entries for the deposit master seed and treasury seed

## Commands

- `uv sync`
- `uv run --env-file .env python -m nukefm ingest --limit 100`
- `uv run --env-file .env python -m nukefm serve --host 127.0.0.1 --port 8000`
- `uv run --env-file .env python -m nukefm sync-deposits`
- `uv run --env-file .env python -m nukefm process-withdrawals --limit 100`

## Private Surface

- `POST /v1/auth/challenge`
- `POST /v1/auth/api-key`
- `GET /v1/private/account`
- `GET /v1/private/account/deposit-address`
- `GET /v1/private/account/deposits`
- `GET /v1/private/account/withdrawals`
- `GET /v1/private/account/portfolio`
- `GET /v1/private/account/positions`
- `GET /v1/private/account/trades`
- `POST /v1/private/withdrawals`

## Secret-Tool Setup

The private-key material is not read from `.env`.

The app expects two `secret-tool` values under the `service` configured in `config.json`:

- `deposit-master-seed`
- `treasury-seed`

Each value must be a 32-byte seed encoded as 64 hex characters.

Example:

```bash
secret-tool store --label "nuke.fm deposit master seed" service nuke.fm name deposit-master-seed
secret-tool store --label "nuke.fm treasury seed" service nuke.fm name treasury-seed
```

The deposit master seed deterministically derives one deposit wallet per user. The treasury seed
controls the platform wallet that funds associated token-account creation and withdrawal
broadcasts.

## Public Surface

- `GET /v1/public/tokens`
- `GET /v1/public/tokens/{mint}`
- `GET /`
- `GET /tokens/{mint}`

Deposits are reconciled from observed USDC token-account balance increases. That works cleanly at
this stage because user deposit accounts are one-way funding addresses and the current MVP slice
does not sweep or trade from them yet.

If Bags changes the launch-feed route, update `bags_launch_feed_path` in `config.json` without changing application code.
