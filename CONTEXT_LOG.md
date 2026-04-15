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
- The sell API takes a requested share amount. The backend binary-searches the largest exact USDC redemption whose required share burn fits inside that request, and reports any tiny unfilled same-side remainder explicitly instead of creating hidden opposite-side dust.
- Market liquidity accounts are derived from the same master seed as user deposit accounts, but the HMAC input is domain-separated as `market:{market_id}` instead of `user:{user_id}`.
- Revenue sweep is split into two layers on purpose: the database records the full remaining internal market backing as platform revenue, while the on-chain sweep only moves the resolved market's dedicated USDC deposit account back to treasury because user trading stays offchain inside the shared treasury balance.

## Token Metrics And Sorting

- The live public token-list order now lives in `MarketStore.list_token_cards()`, not in the catalog layer, so the same sort path can serve both `/v1/public/tokens` and `/`.
- Token-level metrics are stored as snapshots in SQLite instead of being fetched during reads. That keeps the board deterministic and lets the operator refresh metrics explicitly with a CLI command.
- Jupiter token search is the current metric source for Bags tokens because it covers Bags mints and returns market-cap, liquidity, and rolling volume fields that were often missing under the earlier DexScreener path.
- Underlying volume is stored from the current metric source snapshot, while underlying market cap should come from an explicit reported market-cap field rather than being inferred at read time.
- Missing metrics stay `null` and are sorted last in both directions so absent data never dominates the board.

## Weekly PM Seeding

- The weekly top-10 auto-seed is intentionally separate from on-chain liquidity reconciliation. `sync-market-liquidity` still means "observed USDC arrived on-chain", while the weekly seed books internal pool liquidity immediately and records matching treasury debt.
- That debt is explicit, not an implicit negative treasury balance. Operators pay it down later with a dedicated treasury-funding command after they top up treasury USDC.

## Frontend Positioning

- The web UI should read like a token-trader briefing, not a PM trading terminal. The primary user is a Bags trader who wants PM-derived risk context for the underlying token.
- The first screen should answer three questions quickly: is the PM signal live, what level matters, and how much supporting token context exists. Lower sections can hold mechanics and history.

## EC2 Deploy

- The public web and public JSON routes no longer instantiate `SolanaTreasury`. They serve already-stored market state and stay read-only at request time.
- The EC2 deploy path starts a private D-Bus plus GNOME keyring session inside the systemd service wrapper so the app can keep loading the Solana seeds from Secret Service instead of moving those seeds into `.env`.
- Deploy updates are intentionally built around a bare git repo and `post-receive` hook on the host so normal code pushes stay one explicit step and do not depend on GitHub webhooks or Actions.
- The EC2 host now terminates TLS with Caddy for `nukefm.xyz` and keeps uvicorn private on `127.0.0.1:8000`; proxy headers are trusted only from the local reverse proxy.
- `ops/ec2/sync-state.sh` must not restore the SQLite DB by default. Database restore is now an explicit `--with-db` action because a failed later secret import previously clobbered live state.

## Live Data Dependencies

- Settlement snapshots now use Jupiter 15-minute USD price candles instead of Bitquery. That removes billing as an operational dependency and keeps the snapshot job aligned with the same market-data family already used for token metrics.
- Settlement snapshots use finalized wall-clock hours. The first snapshot for a newly funded market is the hour ending at `floor(market_start)`, so a market opened mid-hour gets an immediate pre-open baseline instead of waiting for the next top-of-hour bucket.
- The rolling 24h settlement median is intentionally not clipped to `market_start`. Early snapshots should reflect the full trailing underlying-token median, including pre-market trading, so the series opens against a real 24h context instead of an artificially shortened window.
- Jupiter charts do not currently emit empty carry-forward candles for quiet periods. When a finalized hour has no candles in-range, the snapshot layer explicitly carries forward the last known price at or before that hour end.
- Market-liquidity account creation is now retried on Solana RPC `429` responses, and the bulk account-creation path prioritizes already-open markets first so the frontend-visible seeded markets recover before the long tail of awaiting-liquidity markets.

## Token Detail Charting

- The token detail overlay chart uses its own `market_chart_snapshots` table and operator job instead of reusing hourly settlement snapshots. That separation is intentional: the chart is a 5-minute trader-facing read, while hourly settlement snapshots remain the canonical resolution/reference path.
- Each chart row stores both the current token USD price and the current market `YES` probability at the same bucketed timestamp so the frontend can render one aligned dual-axis overlay without stitching together mismatched histories at read time.

## Fixed-Anchor Market Lifecycle

- New markets are no longer created blindly during catalog ingest. They are created from a real observed token price during token-metric capture so every market row can stamp fixed anchors up front.
- Each market now stores `starting_price_usd`, `threshold_price_usd`, `range_floor_price_usd`, and `range_ceiling_price_usd` on the market row itself. The old ATH/drawdown threshold logic is no longer the active lifecycle rule.
- Only one active market per token is frontend-visible. When that visible market's monitored price moves outside its configured range, the frontend rolls to a new successor market while the older market stays active, tradable, and publicly inspectable through `hidden_active_markets`.
- The visible prompt is computed at read time as `Will {symbol} nuke by {x}% by {date}?` using the latest monitored price for that market rather than a permanently stored question string.
- Pre-anchor legacy rows are migrated in place from real observed prices during initialization so existing `market_id` values and their derived liquidity deposit addresses survive deploys. Truly dead legacy rows with no observed price and no attached market state are pruned instead of being carried forward.
