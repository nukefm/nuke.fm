---
name: nuke.fm-api
description: Use when interacting with the nuke.fm API for Bags token scalar prediction markets, including public market discovery, API-key auth, account funding, withdrawals, LONG/SHORT quoting and trading, position inspection, and one-way market liquidity deposits.
---

# nuke.fm API

Use this skill when trading, monitoring, or seeding liquidity through the nuke.fm API.

## Core Model

- The web frontend is read-only.
- Private workflows happen through API keys.
- The token universe comes from Bags token mints.
- Each token has one current frontend-visible market and may have hidden older active markets.
- Current markets are scalar LONG/SHORT markets over the token's USD price on the market end date.
- The market displays a predicted price and a derived implied move in `predicted_nuke_percent`.
- Liquidity deposits are one-way. They do not mint LP shares and cannot be withdrawn.

## Public Data Workflow

Use the public API to:

- list Bags token markets with `GET /v1/public/tokens`
- fetch one token detail with `GET /v1/public/tokens/{mint}`
- inspect `current_market.state`
- read `long_price_usd`, `short_price_usd`, and `implied_price_usd`
- read `min_price_usd`, `max_price_usd`, `market_start`, and the market end timestamp
- read `reference_price_usd`, liquidity, 24h PM volume, and underlying token metrics
- read `bags_token_url` for the external Bags token page

Important rules:

- A market in `awaiting_liquidity` exists but is not tradable.
- Missing data stays `null`; do not synthesize prices, liquidity, or market cap.
- Settlement/reference pricing uses stored 24h-median token price snapshots.

## Private Auth Workflow

1. Request a one-time challenge.
2. Sign the challenge with the Solana wallet.
3. Exchange the signature for an API key.
4. Use that API key on private requests with `X-API-Key` or `Authorization: Bearer`.

Use a dedicated key for bots.

## Trading Workflow

1. Fetch the current market and confirm it is `open`.
2. Fetch a quote with `POST /v1/private/trades/quote`.
3. Submit the trade with `POST /v1/private/trades`.
4. Re-read account balance, positions, and market prices.

Trade body shape:

```json
{
  "market_id": 123,
  "outcome": "long",
  "side": "buy",
  "amount_usdc": "1"
}
```

For sells, submit `share_amount` instead of `amount_usdc`.

## Account Funding And Withdrawal

Use the private API to:

- fetch the account's Solana USDC deposit address
- inspect deposits and withdrawals
- inspect balances, positions, and trade history
- request withdrawals

Wait for deposits to be credited before assuming funds are tradable.

## Liquidity Seeding Workflow

Liquidity deposits sponsor a market; they are not yield positions. Anyone can deposit USDC to a current
market's liquidity address, but deposits do not mint LP shares and cannot be withdrawn.

1. Fetch the current market's liquidity deposit address.
2. Confirm the market is `awaiting_liquidity` or `open`.
3. Send Solana USDC to the address.
4. Wait for reconciliation.
5. Re-read the market state, liquidity, and prices.

The first credited liquidity deposit starts the market clock.

## Safety Checks

- Never trade a non-open market.
- Never assume liquidity deposits are recoverable.
- Re-read market state after settlement or rollover.
- Treat API errors and missing fields as stop conditions, not fallback prompts.
