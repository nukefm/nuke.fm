# Decision Log

## Product and market boundary

- nuke.fm is a token-trader briefing that exposes AI agents' long-term price forecasts and rationales for bags.fm tokens. Public language uses predicted price and market end date rather than prediction-market jargon.
- The public frontend is read-only. Agents trade through the private API; users sponsor markets by sending Solana USDC to a displayed market wallet.
- Sponsorship deposits are one-way, mint no LP shares, cannot be withdrawn, and leave remaining backing as platform revenue after settlement.

## Scalar market lifecycle

- Markets trade offchain LONG and SHORT inventory in a weighted AMM. A market starts at 50/50 using a symmetric log-price range around its observed starting price.
- Only one active market per token is frontend-visible. Crossing either configured range boundary rolls the visible series to a successor while the older market remains active, tradable, and inspectable.
- Rollover transfers only AMM-owned complete sets. Existing binary YES/NO state was deliberately reset rather than semantically migrated into scalar LONG/SHORT state.

## Canonical market data

- Bags pools defines eligible token mints. Jupiter hydrates exact-mint metadata, supply, price candles, and snapshots; symbols are never sufficient identity.
- Current and predicted market cap are derived from one supply and the corresponding canonical price, not ingested market-cap values. Missing metrics remain missing and sort last.
- Hourly settlement/reference snapshots are canonical for lifecycle math. Five-minute chart snapshots are a separate display series and may smooth only in the frontend; stored/API values remain raw.

## Treasury and identity

- Wallet-signed one-time challenges mint hashed private API keys. User deposit and market wallets are deterministically derived from one Secret Service seed with domain-separated derivation; per-wallet private keys are not stored.
- Withdrawals reserve ledger funds immediately and release the hold on failure. Weekly internal liquidity seeding records explicit treasury debt and remains separate from reconciliation of actual on-chain deposits.
- Public reads use stored state and never instantiate signing infrastructure. Production keeps uvicorn private behind Caddy and loads seeds through its private D-Bus/keyring session rather than `.env`.

## Forecast bot integrity

- A missing, invalid, or schema-breaking forecast produces a visible no-trade result. Spot price is never substituted for a model forecast.
- External forecast evidence must verify the exact Bags mint. The nuke.fm mint, reference price, and market cap remain canonical when ticker-symbol sources disagree.
- Bot rationales are token-level state submitted before trades, so the public thesis remains associated with the bot and mint across rolled markets.
