---
name: bagrug-api
description: Use when interacting with the Bags nuke prediction market API for bots or agents. Covers public market discovery, API-key auth bootstrap, account funding, withdrawals, XYK AMM quoting and trading, position inspection, and one-way market liquidity deposits.
---

# Bagrug API

Use this skill when the task is to trade, monitor, or seed liquidity through the Bags nuke market API.

## Core Model

- The web frontend is read-only.
- Private workflows happen through the API.
- Each token has a rolling market series with one current market and zero or more past resolved markets.
- `Nuke` means the token fell more than 95% from that market's own ATH within that market's 90 day window.
- Each active market uses one offchain XYK pool between `YES` and `NO`.
- Liquidity deposits are one-way only. They do not mint LP shares and cannot be withdrawn.

## First Step

If an OpenAPI document or API reference exists, read that first and follow it over this skill. Use this skill for workflow, field expectations, and market semantics.

Likely docs to check first:

- `/openapi.json`
- `/docs`
- repo API reference files

## Public Data Workflow

Use the public API to:

- list tokens
- fetch a token page by mint
- inspect the current market state
- inspect past resolved markets for that token
- read `yes_price_usd` and `no_price_usd`
- read `reference_price_usd`
- read `ath_price_usd`
- read `ath_timestamp`
- read `drawdown_fraction`
- read `threshold_price_usd`
- read `market_start`
- read `expiry`
- read the current market liquidity deposit address

Important interpretation rules:

- `ATH` is per market, not lifetime.
- A current market in `awaiting_liquidity` exists but is not tradable.
- A new market is created immediately after the previous one resolves.

## Private Auth Workflow

Use API keys for private access.

Recommended bootstrap sequence:

1. Request a one-time challenge.
2. Sign the challenge with the wallet.
3. Exchange the signature for an API key.
4. Use the API key on later private requests.

If the implementation supports key rotation or multiple keys, prefer a dedicated key per bot.

## Trading Workflow

1. Fetch the current market and confirm it is `open`.
2. Fetch a quote for the intended side and size.
3. Submit the trade.
4. Re-read balances, positions, and market prices.

Expect trade-facing endpoints for:

- quote preview
- order submission
- positions
- balances
- trade history

Treat the AMM as the source of truth for current price. There is no order book.

## Account Funding And Withdrawal

Use the private API to:

- fetch the account's Solana USDC deposit address
- inspect deposit history
- inspect available balance
- request withdrawals
- inspect withdrawal history

Wait for deposits to be credited before assuming funds are tradable.

## Liquidity Seeding Workflow

Use the public market liquidity deposit address for market seeding.

Rules:

- anyone can deposit liquidity into any market
- liquidity is market-specific
- liquidity deposits are permanent
- no liquidity withdrawal exists
- no LP ownership share exists
- after market resolution and user payouts, remaining liquidity is platform revenue

When seeding a market:

1. Fetch the current market's liquidity deposit address.
2. Confirm whether the market is `awaiting_liquidity` or already `open`.
3. Send Solana USDC to that address.
4. Wait for the deposit to be credited.
5. Re-read the market state and pool prices.

The first credited liquidity deposit starts the market's 90 day clock.

## Price Semantics

The AMM is `YES <> NO`, but prices are displayed in USDC and probability terms.

Expected fields:

- `yes_price_usd`
- `no_price_usd`

Expected interpretation:

- `yes_price_usd + no_price_usd = 1.0`
- each value can also be read as an implied probability

## Useful Endpoint Shape

If the implementation follows the spec closely, expect endpoints similar to:

- `GET /v1/public/tokens`
- `GET /v1/public/tokens/{mint}`
- `GET /v1/public/tokens/{mint}/markets`
- `POST /v1/auth/challenge`
- `POST /v1/auth/api-key`
- `GET /v1/private/account`
- `GET /v1/private/account/deposit-address`
- `GET /v1/private/account/positions`
- `GET /v1/private/account/trades`
- `POST /v1/private/trades/quote`
- `POST /v1/private/trades`
- `POST /v1/private/withdrawals`

If actual endpoint names differ, follow the live API schema.

## Safety Checks

- Never treat a past market's ATH as relevant to the current market.
- Never assume a token with no active liquidity is tradable just because the token page exists.
- Never assume liquidity deposits are recoverable.
- Re-read market state after any settlement event before placing a new trade.
