# Context Log

## Repository Naming

- The repo name is `nuke.fm`.
- The durable repo-name metadata currently lives in the repo path, git remote config, and `uv.lock`; the local `.venv` only mirrors that name as generated workspace state.

## Prediction Market MVP

- The public market term is `nuke`, not `rug`.
- Each token has a rolling market series. When a market resolves, the next market is created immediately in `awaiting_liquidity`.
- Each market tracks its own ATH from its own funding time. Earlier market outcomes do not affect later ATH tracking.
- The trading model is an offchain backend-only weighted AMM between `LONG` and `SHORT`, with displayed USDC prices derived from reserves and weights.
- The frontend is read-only. Trading, balances, positions, deposits, and withdrawals are API-only.
- Market liquidity deposits are one-way USDC deposits into a market-specific address. They do not mint LP shares, cannot be withdrawn, and any remaining liquidity after settlement is swept to the platform as revenue.
- Liquidity seeding is explicitly defined to mint equal LONG and SHORT inventory while preserving the pre-deposit displayed price by retuning pool weights.

## First Deliverable Implementation

- The first shipped slice used a local SQLite catalog with FastAPI and Jinja templates. Current catalog discovery uses Bags token mints as the source of truth and hydrates exact mints through Jupiter.
- Current-market price, liquidity-address, reference-price, ATH, drawdown, and threshold fields remain explicitly `null` in the public API until the later AMM, liquidity, and settlement deliverables exist. They are not inferred or synthesized.
- The older Jupiter Bags gems discovery path was removed because Jupiter should not be the source of truth for which tokens are Bags tokens.

## Private API And Treasury

- Private API auth uses a one-time challenge string signed by the user's Solana wallet. API keys are then hashed at rest and used for later private requests.
- User deposit wallets are deterministically re-derived from a single master seed stored in `secret-tool`. The repo never persists per-user private keys to disk.
- The current deposit watcher credits users by reconciling monotonic increases in each dedicated USDC token-account balance. That is intentional for this delivery stage because the product has not started sweeping or trading from those deposit accounts yet.
- Withdrawal requests reserve funds immediately in the internal ledger via a hold entry. Broadcast and confirmation happen later through the operator CLI, and failed withdrawals release that hold back into the ledger.

## Weighted AMM And Settlement

- The weighted pool still uses Balancer-style weights as exponents. The MVP divergence is that liquidity deposits retune those exponent weights after equal LONG/SHORT inventory is added so displayed prices stay unchanged at deposit time.
- The sell API takes a requested share amount. The backend binary-searches the largest exact USDC redemption whose required share burn fits inside that request, and reports any tiny unfilled same-side remainder explicitly instead of creating hidden opposite-side dust.
- Market liquidity accounts are derived from the same master seed as user deposit accounts, but the HMAC input is domain-separated as `market:{market_id}` instead of `user:{user_id}`.
- Revenue sweep is split into two layers on purpose: the database records the full remaining internal market backing as platform revenue, while the on-chain sweep only moves the resolved market's dedicated USDC deposit account back to treasury because user trading stays offchain inside the shared treasury balance.

## Token Metrics And Sorting

- The live public token-list order now lives in `MarketStore.list_token_cards()`, not in the catalog layer, so the same sort path can serve both `/v1/public/tokens` and `/`.
- Token-level metrics are stored as snapshots in SQLite instead of being fetched during reads. That keeps the board deterministic and lets the operator refresh metrics explicitly with a CLI command.
- Bags pools is the board/catalog token-universe source. Jupiter token search backs exact-mint metadata hydration, token metric snapshots, and per-market chart snapshots.
- Underlying volume is stored from the current metric source snapshot, while displayed current market cap is derived from token supply and the latest canonical hourly reference price.
- Missing metrics stay `null` and are sorted last in both directions so absent data never dominates the board.
- The EC2 deploy should install a 10-minute catalog/metric refresh timer. Weekly seeding alone is not enough, because stale token snapshots make market-cap sorting look broken even when the comparator is correct.

## Weekly PM Seeding

- The weekly top-volume auto-seed is intentionally separate from on-chain liquidity reconciliation. `sync-market-liquidity` still means "observed USDC arrived on-chain", while the weekly seed books internal pool liquidity immediately and records matching treasury debt.
- That debt is explicit, not an implicit negative treasury balance. Operators pay it down later with a dedicated treasury-funding command after they top up treasury USDC.

## Frontend Positioning

- The web UI should read like a token-trader briefing, not a PM trading terminal. The primary user is a Bags trader who wants PM-derived risk context for the underlying token.
- The first screen should answer three questions quickly: is the PM signal live, what level matters, and how much supporting token context exists. Lower sections can hold mechanics and history.
- The main board should stay a minimalist token-row table. Its PM signal column should show implied move as `predicted/current - 1`, so upward implied moves are positive/green and downward implied moves are negative/red.
- After the top markets were seeded, the main board returned to showing initialized/live markets by default so the primary toggle remains "Show uninitialized" and the board matches the Clay preview control model.

## EC2 Deploy

- The public web and public JSON routes no longer instantiate `SolanaTreasury`. They serve already-stored market state and stay read-only at request time.
- The EC2 deploy path starts a private D-Bus plus GNOME keyring session inside the systemd service wrapper so the app can keep loading the Solana seeds from Secret Service instead of moving those seeds into `.env`.
- Deploy updates are intentionally built around a bare git repo and `post-receive` hook on the host so normal code pushes stay one explicit step and do not depend on GitHub webhooks or Actions.
- The EC2 host now terminates TLS with Caddy for `nukefm.xyz` and keeps uvicorn private on `127.0.0.1:8000`; proxy headers are trusted only from the local reverse proxy.
- `ops/ec2/sync-state.sh` must not restore the SQLite DB by default. Database restore is now an explicit `--with-db` action because a failed later secret import previously clobbered live state.
- `ingest` must stay a catalog/metric refresh command. Do not create on-chain market liquidity accounts there; `sync-market-liquidity` owns that side effect so low treasury SOL cannot block public board freshness.
- The board/API display token market cap from the latest `token_metrics_snapshots.underlying_market_cap_usd`, independent of prediction-market liquidity state. `market_snapshots` remain the settlement/reference-price series for active markets, not the token market-cap display source.
- SQLite write contention is expected on EC2 because timers, reads, and private trading share one DB. Keep incidental read paths read-only and use SQLite busy waiting for legitimate writes instead of treating brief writer overlap as an application failure.

## Product Positioning

- Public copy should explain nuke.fm as long-term forward pricing for Bags project shares, not as a short-horizon price alert or generic prediction-market terminal.
- The main conceptual split is spot versus forward price: spot is the current clearing price, while nuke.fm's implied price is the market's expiry forecast and can trade above or below spot.
- Short exposure is part of the product's value proposition because it turns bearish Bags-token views into visible prices instead of private or purely spot-selling behavior.
- The old `/about` page is intentionally replaced by `/how-it-works`; no compatibility redirect is required unless requested later.

## Live Data Dependencies

- Token discovery now uses the Bags pools API as the source of truth for eligible token mints. Jupiter Tokens v2 is only a hydrator for exact mint-address metadata/price/supply; do not switch discovery back to Jupiter token or pool feeds because token symbols are not unique.
- Settlement snapshots now use Jupiter 15-minute USD price candles instead of Bitquery. That removes billing as an operational dependency and keeps the snapshot job aligned with the same market-data family already used for token metrics.
- Settlement snapshots use finalized wall-clock hours. The first snapshot for a newly funded market is the hour ending at `floor(market_start)`, so a market opened mid-hour gets an immediate pre-open baseline instead of waiting for the next top-of-hour bucket.
- The rolling 24h settlement median is intentionally not clipped to `market_start`. Early snapshots should reflect the full trailing underlying-token median, including pre-market trading, so the series opens against a real 24h context instead of an artificially shortened window.
- Jupiter charts do not currently emit empty carry-forward candles for quiet periods. When a finalized hour has no candles in-range, the snapshot layer explicitly carries forward the last known price at or before that hour end.
- Market-liquidity account creation is now retried on Solana RPC `429` responses, and the bulk account-creation path prioritizes already-open markets first so the frontend-visible seeded markets recover before the long tail of awaiting-liquidity markets.
- Weekly auto-seeding ranks by the latest token-level underlying 24h volume snapshot, so awaiting-liquidity markets can be eligible before they have settlement snapshots while missing-volume rows stay ineligible.

## Token Detail Charting

- The token detail overlay chart uses its own `market_chart_snapshots` table and 5-minute EC2 timer instead of reusing hourly settlement snapshots. That separation is intentional: the chart is a trader-facing read, while hourly settlement snapshots remain the canonical resolution/reference path.
- Each chart row stores both the current token USD price and the current market-implied expiry price at the same bucketed timestamp so the frontend can render one aligned price overlay without stitching together mismatched histories at read time.
- Rolled active market series should be included in the token detail chart once their rows have been normalized to `implied_price_usd`; the user cares about easy prediction viewing more than preserving contract-series separation in the chart. Keep contract details available elsewhere, but do not hide older live predictions from the main chart solely because bounds/expiry/pool state differ.
- The token detail chart should describe the market line as a prediction/predicted price rather than using the acronym "PM"; the intended fast read is current spot price versus the predicted expiry price on one shared USD axis.

## Fixed-Anchor Market Lifecycle

- New markets are no longer created blindly during catalog ingest. They are created from a real observed token price during token-metric capture so every market row can stamp fixed anchors up front.
- Each market now stores `starting_price_usd`, `threshold_price_usd`, `range_floor_price_usd`, and `range_ceiling_price_usd` on the market row itself. The old ATH/drawdown threshold logic is no longer the active lifecycle rule.
- Only one active market per token is frontend-visible. When that visible market's monitored price moves outside its configured range, the frontend rolls to a new successor market while the older market stays active, tradable, and publicly inspectable through `hidden_active_markets`.
- The visible prompt is computed at read time as `What will {symbol} trade at by {date}?` rather than a permanently stored question string.
- Pre-anchor legacy rows are migrated in place from real observed prices during initialization so existing `market_id` values and their derived liquidity deposit addresses survive deploys. Truly dead legacy rows with no observed price and no attached market state are pruned; address-assigned but still unfunded rows are parked off-frontend and then reactivated in place later when a real price finally becomes available.

## Scalar LONG/SHORT Planning

- The planned scalar market should initialize at `50c LONG / 50c SHORT` by using a symmetric log-space payout range around the market starting price. With the default 10x range multiple, `min_price_usd = starting_price_usd / 10` and `max_price_usd = starting_price_usd * 10`.
- The planned rollover config should be named as a symmetric boundary, not as an upper-only threshold. `market_rollover_boundary_rate = 0.85` means rollover when the deterministic underlying-implied LONG rate from a stored 24h-median snapshot touches either `0.85` or `1 - 0.85`; no separate 24-hour consecutive-streak rule should be added because the rolling 24h median is the intended stability filter.
- Existing binary YES/NO market state should be destructively reset rather than migrated into scalar LONG/SHORT state, because current YES means downside/nuke exposure while scalar LONG means upside price exposure.
- Scalar market cap display should not ingest reported market cap directly. Use token supply and observed price to derive current and predicted market cap so the price basis stays consistent.
- The scalar implementation records `scalar_long_short_reset` in `app_metadata` after destructive reset so old `threshold_price_usd` columns left behind by SQLite do not cause repeated market-state deletion on every startup.
- Rollover transfers only AMM-owned complete sets: `floor(min(long_reserve, short_reserve) * market_rollover_liquidity_transfer_fraction)`. The successor is opened immediately if any liquidity transfers, even before a new on-chain market deposit address is assigned.
- Token metric capture ignores API-reported market cap. Jupiter token data should provide supply; current market cap is derived from the latest stored 24h-median price and supply, while predicted market cap is derived from the AMM-implied price and the same supply.

## LLM Trading Bot

- The LLM trader bot now lives as the `bots/trader` submodule pointing at `nukefm/nukefm-trader-bot`; the standalone Claude skill lives as `.claude/skills/nukefm-forecast-trader` pointing at `nukefm/nukefm-forecast-trader-skill`.
- Its fair-price source is an OpenRouter call to `moonshotai/kimi-k2.6` with the `openrouter:web_search` server tool enabled. The bot asks for a cited USD price forecast at market expiry, maps that forecast into the scalar LONG target, then buys LONG or SHORT through the private API inside risk caps.
- Missing or invalid forecasts intentionally produce no-trade records. Do not replace them with spot/reference-price fallbacks because that would change the bot's strategy semantics.
- The first live bot run showed Kimi/OpenRouter sometimes returned null, prose/non-JSON, or non-decimal forecast content despite prompt-only JSON instructions. Use OpenRouter structured outputs with numeric forecast fields for forecast calls; do not loosen parsing or synthesize a forecast when the schema is not satisfied.
- The PUNCH live forecast on 2026-04-26 exposed a source-hygiene issue: web search latched onto CoinMarketCap's `PUNCH` page for contract `NV2RYH...FSpump`, while the Bags market mint was `H1Ckn...BAGS`. Bot/skill forecasts must treat nuke.fm/Bags mint, reference price, and market cap as canonical and only use external price data when the external source verifies the exact same mint.
- Bot rationales are token-level state keyed by API account and Bags mint, independent of the trade endpoint. Bots should submit or update the rationale before trading so the public token page can show the latest thesis next to that bot's current marked value across all PM positions for the token.
- `bots/noise-trader/` is deliberately gitignored and local-only. It exists for controlled tiny random trades to exercise volume display, not as product code.
