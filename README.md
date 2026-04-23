# nuke.fm

Prediction market MVP for nuke.fm.

## Goal

nuke.fm is an offchain prediction market product for Bags tokens that settles in Solana USDC.
Each token has a rolling series of markets that ask whether the token will nuke within the
next 90 days after that market opens.

This repository currently includes these MVP slices:

- ingest Bags token metadata into a local market catalog
- create and maintain one frontend-visible fixed-anchor market per token
- derive and publish one public USDC liquidity deposit address per active market
- run a weighted YES/NO AMM per open market
- reconcile one-way market liquidity deposits into pool depth and cash backing
- quote and execute API-only trades against the weighted pool
- capture hourly settlement snapshots from a rolling 24h median of Jupiter USD price candles
- track each market against a fixed starting price, fixed nuke threshold, and fixed rollover range
- capture Jupiter token metrics and sort the public market board by liquidity, dump %, underlying volume, or underlying market cap
- debt-fund a weekly $1 PM seed into the top 10 current markets by underlying market cap
- resolve markets from stored historical snapshots, roll the frontend-visible series when the price exits range, pay winning accounts, and record revenue sweeps
- expose that catalog through a public JSON API
- render the same catalog through a read-only web frontend
- bootstrap private API access with Solana wallet signatures and API keys
- issue per-user USDC deposit addresses
- reconcile credited deposits into an internal ledger
- accept withdrawals and process them through the operator CLI
- expose account, position, trade, and portfolio data through the private API

The frontend still publishes market state only. Wallet connection and trading stay API-only.

## High-Level Model

- The canonical token identifier is the mint address.
- Every token has one frontend-visible current market, zero or more hidden active markets, and zero or more past markets.
- A market is created from a real observed token price and stores fixed lifecycle anchors up front:
  - starting price
  - nuke threshold
  - rollover range floor
  - rollover range ceiling
- Older rolled markets can stay active, tradable, and later resolvable even after the frontend moves on to a newer visible market.
- The public market term is `nuke`, not `rug`.

## How It Works

At a high level, the app now has seven moving parts.

First, the ingestion command pulls Bags launchpad gems from Jupiter's pool data feed and stores
token metadata in a local SQLite database.

Second, the catalog layer stores token metadata and market state, while the market lifecycle code
creates missing visible markets from real observed prices during token-metric refreshes.

Third, the market engine stores a weighted YES/NO pool for each active market. Liquidity deposits
mint equal YES and NO inventory into the pool, then retune the weights so the displayed YES/NO
prices stay unchanged at the instant of deposit. Buys spend USDC. Sells submit a share amount and
the backend uses an integer binary search to find the largest exact USDC redemption that can be
funded without inventing opposite-side dust. If atomic rounding prevents filling the full requested
share amount exactly, the response reports the small unfilled remainder explicitly.

Fourth, the settlement loop captures hourly rolling 24h median reference prices from historical
trade data, resolves `YES` when that monitored price first falls through the market's fixed
threshold before expiry, resolves `NO` at expiry otherwise, and rolls the frontend-visible series
forward when the monitored price leaves that market's configured range.

Fifth, the auth layer issues one-time challenges, verifies Solana wallet signatures, and mints
API keys for private access.

Sixth, the treasury layer derives deterministic per-user and per-market USDC wallets from a master
seed in `secret-tool`, ensures the associated token accounts exist, reconciles deposit balance
changes, broadcasts withdrawals from the platform treasury wallet, and sweeps resolved market
deposit accounts back to the treasury USDC account.

Seventh, the FastAPI app reads the catalog, AMM state, and account ledger and serves them in two forms:

- JSON endpoints under `/v1/public`
- JSON endpoints under `/v1/auth` and `/v1/private`
- HTML pages for the market list and token detail views

The same SQLite database backs the public catalog, AMM state, settlement snapshots, and private
ledger, while the frontend stays a thin read-only view over public market data.

## Current Scope

The current implementation now covers the market engine and settlement loop, but it is still an
MVP. Important current constraints:

- market liquidity deposits are one-way only and do not mint LP shares
- revenue sweep records the full internal leftover backing, but the on-chain transfer only sweeps
  the market-specific USDC deposit account because user trading stays offchain inside the shared treasury
- the web frontend remains read-only even though the private trading API is live

## Runtime

- Python 3.13
- `secret-tool` entries for the deposit master seed and treasury seed
- network access to Jupiter charts for hourly settlement snapshot jobs
- network access to Jupiter gems for board metrics and Jupiter token search for 5 minute market chart snapshot jobs

## Commands

- `uv sync`
- `uv run --env-file .env python -m nukefm ingest --limit 100`
- `uv run --env-file .env python -m nukefm serve --host 127.0.0.1 --port 8000`
- `uv run --env-file .env python -m nukefm sync-deposits`
- `uv run --env-file .env python -m nukefm sync-market-liquidity`
- `uv run --env-file .env python -m nukefm sync-token-metrics`
- `uv run --env-file .env python -m nukefm snapshot-market-charts`
- `uv run --env-file .env python -m nukefm seed-weekly-liquidity --top 10 --amount-usdc 1`
- `uv run --env-file .env python -m nukefm record-treasury-funding --amount-usdc 10`
- `uv run --env-file .env python -m nukefm snapshot-markets`
- `uv run --env-file .env python -m nukefm resolve-markets`
- `uv run --env-file .env python -m nukefm process-withdrawals --limit 100`

## EC2 Deploy

The repo now includes a minimal EC2 deploy path under [`ops/ec2`](ops/ec2):

- `bootstrap-host.sh` installs the host prerequisites, configures Caddy for `https://nukefm.xyz`, creates `/srv/nukefm`, installs a systemd service, and creates a bare git repo with a `post-receive` hook.
- `push-production.sh` pushes the current local `HEAD` to that bare repo as `main`.
- `sync-state.sh` copies `.env` and imports the two `secret-tool` seeds into the remote host.
- `sync-state.sh --with-db` additionally restores `data/nukefm.sqlite3` as an explicit operator action.
- Set `NUKEFM_SSH_KEY=/path/to/key.pem` when the host uses a dedicated EC2 key pair instead of your default SSH agent/config.
- Set `NUKEFM_SSH_CONFIG_FILE=/dev/null` if your local SSH config is broken or you need the scripts to ignore it.

Recommended first deploy flow:

1. Launch an Ubuntu EC2 instance and open inbound `22`, `80`, and `443`.
2. Run `./ops/ec2/bootstrap-host.sh <host> [user]`.
3. On the server, set `NUKEFM_KEYRING_PASSWORD` in `/srv/nukefm/shared/runtime.env`.
4. Run `./ops/ec2/push-production.sh <host> [user]`.
5. Run `./ops/ec2/sync-state.sh <host> [user]`.

After that, a normal update is just:

1. `git push origin <branch>`
2. `./ops/ec2/push-production.sh <host> [user]`

The headless host still uses `secret-tool`. `run-service.sh` starts a private D-Bus and
GNOME keyring session before launching the app so the existing treasury code can keep reading
the Solana seeds from Secret Service instead of moving those seeds into `.env`. The app now
binds to `127.0.0.1:8000`, and Caddy terminates TLS for `nukefm.xyz` and proxies requests to it.

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
- `POST /v1/private/trades/quote`
- `POST /v1/private/trades`
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

The deposit master seed deterministically derives one deposit wallet per user and one public
liquidity wallet per market. The treasury seed controls the platform wallet that funds associated
token-account creation, withdrawal broadcasts, and resolved-market revenue sweeps.

## Public Surface

- `GET /v1/public/tokens`
- `GET /v1/public/tokens/{mint}`
- `GET /`
- `GET /tokens/{mint}`

The public token list and board support `sort_by` and `sort_direction` query parameters. Accepted
`sort_by` values are `market_liquidity`, `dump_percentage`, `underlying_volume`, and
`underlying_market_cap`.

The visible frontend question is dynamic:

- `Will {symbol} nuke by {x}% by {date}?`

`sync-token-metrics` now does double duty: it stores token metrics and creates any missing
frontend-visible market using the current observed token price as the fixed market anchor.
`ingest` uses the same Bags gems source, so the catalog and the market-cap sort are fed by the
same ranking universe.

Deposits are reconciled from observed USDC token-account balance increases. That works cleanly at
this stage because user deposit accounts are one-way funding addresses and the current MVP slice
does not sweep or trade from them yet.

Market liquidity deposits use the same monotonic-balance reconciliation pattern, but they credit
weighted-pool depth and market cash backing instead of a user cash balance.

Weekly auto-seeds are different on purpose. They can open or deepen the top 10 current markets by
captured underlying market cap without waiting for an observed on-chain deposit. When they do, the
seed is recorded as explicit treasury debt that the operator can fund later with a matching
treasury-funding entry.

If the Jupiter Bags gems route changes, update `jupiter_gems_base_url` in `config.json` without changing application code.
