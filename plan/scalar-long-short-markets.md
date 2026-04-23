# Scalar LONG/SHORT Markets

## Goal

Convert the current binary nuke market into one scalar price market lifecycle. The market should predict the token price at expiry, expose that as an implied price and implied nuke percentage, and roll to a new market when the old scalar range stops being informative.

## Core Model

- Replace binary `yes`/`no` outcomes with scalar `long`/`short` outcomes everywhere they are exposed or persisted in newly migrated schema fields: pool reserves/weights, positions, trades, public/private API payloads, serializers, templates, chart labels, and tests.
- Do not maintain `yes`/`no` API compatibility. Requests should use `long` or `short`, and obsolete binary naming should be deleted rather than wrapped.
- Store `max_price_usd` on every market. Compute it at market creation as `starting_price_usd * market_max_price_multiple`.
- Add `market_max_price_multiple` to config, defaulting to `10`.
- Use the existing complete-set AMM shape, renamed around LONG/SHORT: `1 LONG + 1 SHORT = $1` of terminal collateral-backed payout.
- Set initial AMM price from the deterministic payout rate implied by the market starting price, not a hard-coded 50/50 price.
- Keep later liquidity deposits price-preserving by retuning weights after matched LONG/SHORT inventory is added.

## Log Payout Curve

- Use `long_rate = ln(1 + resolution_price_usd / starting_price_usd) / ln(1 + max_price_usd / starting_price_usd)`.
- Clamp `long_rate` to `[0, 1]`, then set `short_rate = 1 - long_rate`.
- With the default `max_price_usd = starting_price_usd * 10`, the curve maps `0x` start to `0c`, `1x` start to about `28.9c`, `2x` start to about `45.8c`, `5x` start to about `74.7c`, about `6.68x` start to `85c`, and `10x` start to `$1`.
- Use the inverse curve for display: `implied_price_usd = starting_price_usd * (exp(long_price * ln(1 + max_price_usd / starting_price_usd)) - 1)`.
- Do not add a lower lifecycle bound for this todo. The payout lower bound is already `price = 0`, where LONG pays `0` and SHORT pays `1`. A lower rollover rule would be a separate product decision about collapsed markets, not a solvency requirement.

## Settlement

- Replace binary threshold settlement with expiry-based scalar settlement.
- At expiry, resolve from the latest stored market snapshot price at or before expiry.
- Pay each account for both legs: `long_shares * long_rate + short_shares * short_rate`.
- Record payout rows with enough context to audit the scalar rates used at resolution.
- Clear both position columns after payout and sweep remaining old-market backing as platform revenue.

## Rollover

- Delete the old lower/upper rollover-range behavior as active logic.
- Compute the deterministic LONG payout implied by each observed underlying snapshot using the same log curve.
- Trigger successor creation only when the deterministic underlying-implied LONG payout remains above `market_rollover_long_rate_threshold` for 24 consecutive hours.
- Add `market_rollover_long_rate_threshold` to config, defaulting to `0.85`.
- With the default 10x max-price multiple, the `0.85` rollover threshold corresponds to about `6.68x` the market creation price.
- When the trigger fires, create a successor market using the latest observed underlying price as its `starting_price_usd`.
- Keep the old market active, tradable, and resolvable through the API, but hide it from the main frontend once superseded.

## Liquidity Transfer

- Move only AMM-owned complete sets from the old market to the new market. Do not move trader-held share collateral out of the old market.
- Add `market_rollover_liquidity_transfer_fraction` to config, defaulting to `0.80`.
- Compute the transfer amount as `floor(min(old_long_reserve_atomic, old_short_reserve_atomic) * transfer_fraction)`.
- The `min` is intentional because only matched LONG/SHORT units form complete neutral liquidity sets. If the AMM is imbalanced, one-sided excess inventory stays in the old market.
- The `floor` is required because reserves are stored in integer atomic units.
- Remove the transfer amount from old LONG reserves, old SHORT reserves, old cash backing, and old total liquidity. Add the same amount as matched liquidity in the successor market.
- Retune old-market weights so removing matched liquidity does not introduce an artificial price jump. Seed or retune the new market so its initial displayed LONG price equals the new starting price's deterministic log-payout rate.

## Frontend And API

- Add public serialization fields for `long_price_usd`, `short_price_usd`, `implied_price_usd`, `predicted_market_cap_usd`, `predicted_nuke_percent`, `max_price_usd`, and the expiry date.
- Derive predicted market cap as `current_market_cap * implied_price / current_observed_price` when both current market cap and current observed price are available.
- Derive predicted nuke percent as `1 - predicted_market_cap / current_market_cap`. Allow it to go negative when the scalar market implies upside.
- Keep missing metric data explicit as `null`; do not synthesize current market cap, current price, or implied price.
- Update the main table columns to include predicted nuke %, implied price, and expiry before PM volume, PM liquidity, underlying volume, and underlying market cap.
- Update token-detail copy and charts away from chance-of-nuke language toward scalar implied-price language.
- Store and render chart snapshots as market-implied price rather than binary chance percent.

## Tests

- Update LONG/SHORT quote, trade, position, and trade-history tests.
- Add scalar settlement tests for low price, middle price, and above-`max_price_usd` capped payout.
- Add rollover tests proving no successor before 24 hours above the deterministic LONG threshold and successor creation after 24 consecutive hours above it.
- Add liquidity-transfer tests proving only AMM-owned complete sets move and old-market trader positions remain solvent.
- Add API/frontend tests for implied price, expiry table column, predicted nuke percent, scalar chart data, and removal of visible YES/NO wording.

## Validation

- Run `uv run --env-file .env pytest`.
- Run `uv run --env-file .env python -m nukefm serve --host 127.0.0.1 --port 8000` long enough to confirm startup, then stop it.
