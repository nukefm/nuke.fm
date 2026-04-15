# Context Log

## Repository Naming

- The repo name is `nuke.fm`.
- The durable repo-name metadata currently lives in the repo path, git remote config, and `uv.lock`; the local `.venv` only mirrors that name as generated workspace state.

## Prediction Market MVP

- The public market term is `nuke`, not `rug`.
- Each token has a rolling market series. When a market resolves, the next market is created immediately in `awaiting_liquidity`.
- Each market tracks its own ATH from its own funding time. Earlier market outcomes do not affect later ATH tracking.
- The trading model is an offchain backend-only weighted AMM between `YES` and `NO`, with displayed USDC prices derived from reserves and weights.
- The frontend is read-only. Trading, balances, positions, deposits, and withdrawals are API-only.
- Market liquidity deposits are one-way USDC deposits into a market-specific address. They do not mint LP shares, cannot be withdrawn, and any remaining liquidity after settlement is swept to the platform as revenue.
- Liquidity seeding is explicitly defined to mint equal YES and NO inventory while preserving the pre-deposit displayed price by retuning pool weights.

## First Deliverable Implementation

- The first shipped slice uses a local SQLite catalog with FastAPI and Jinja templates. It ingests Bags launch-feed metadata, creates one current market per token, and renders that catalog through both HTML pages and `/v1/public` JSON.
- Current-market price, liquidity-address, reference-price, ATH, drawdown, and threshold fields remain explicitly `null` in the public API until the later AMM, liquidity, and settlement deliverables exist. They are not inferred or synthesized.
- The Bags launch-feed route is configurable through `config.json` as `bags_launch_feed_path` because the docs clearly expose the feed concept but the concrete path may shift over time.
- The live Bags launch feed currently rejects query parameters such as `limit`. Fetch the feed without query parameters, then truncate locally after parsing the `response` array.

## Private API And Treasury

- Private API auth uses a one-time challenge string signed by the user's Solana wallet. API keys are then hashed at rest and used for later private requests.
- User deposit wallets are deterministically re-derived from a single master seed stored in `secret-tool`. The repo never persists per-user private keys to disk.
- The current deposit watcher credits users by reconciling monotonic increases in each dedicated USDC token-account balance. That is intentional for this delivery stage because the product has not started sweeping or trading from those deposit accounts yet.
- Withdrawal requests reserve funds immediately in the internal ledger via a hold entry. Broadcast and confirmation happen later through the operator CLI, and failed withdrawals release that hold back into the ledger.

## Weighted AMM And Settlement

- The weighted pool still uses Balancer-style weights as exponents. The MVP divergence is that liquidity deposits retune those exponent weights after equal YES/NO inventory is added so the displayed YES/NO price stays unchanged at deposit time.
- The sell API is intentionally shaped around a requested USDC amount out rather than a requested share amount. That avoids ambiguous residual opposite-side inventory when complete-set redemption and integer token accounting meet weighted-pool math.
- Market liquidity accounts are derived from the same master seed as user deposit accounts, but the HMAC input is domain-separated as `market:{market_id}` instead of `user:{user_id}`.
- Revenue sweep is split into two layers on purpose: the database records the full remaining internal market backing as platform revenue, while the on-chain sweep only moves the resolved market's dedicated USDC deposit account back to treasury because user trading stays offchain inside the shared treasury balance.
