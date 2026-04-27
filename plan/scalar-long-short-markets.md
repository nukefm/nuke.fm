# Scalar LONG/SHORT Markets

## Goal

Convert the current binary nuke market into one scalar price market lifecycle. The market should predict the token price at the market end date, expose that as an implied price and implied nuke percentage, and roll to a new market when the old scalar range stops being informative.

## Core Model

- Replace binary `yes`/`no` outcomes with scalar `long`/`short` outcomes everywhere they are exposed or persisted in newly migrated schema fields: pool reserves/weights, positions, trades, public/private API payloads, serializers, templates, chart labels, and tests.
- Do not maintain `yes`/`no` API compatibility. Requests should use `long` or `short`, and obsolete binary naming should be deleted rather than wrapped.
- Nuke existing binary market state instead of trying to migrate binary exposure into scalar exposure. The destructive migration should delete existing markets and all market-attached state, including pools, positions, trades, payouts, snapshots, chart snapshots, liquidity accounts/deposits, revenue sweeps, weekly seed events, and market treasury debt entries. Preserve token catalog rows and user account/deposit/withdrawal/auth state.
- Delete market-related user ledger entries (`market_buy_*`, `market_sell_*`, and `market_payout`) during the destructive reset so old binary trades do not continue affecting user balances after their positions are removed. Preserve user deposit, withdrawal hold/release, and non-market treasury funding ledger entries.
- Store `min_price_usd` and `max_price_usd` on every market. Compute them at market creation as `starting_price_usd / market_price_range_multiple` and `starting_price_usd * market_price_range_multiple`.
- Add `market_price_range_multiple` to config, defaulting to `10`. The name should make clear that the range is symmetric around the starting price in log space, not only an upside cap.
- Use the existing complete-set AMM shape, renamed around LONG/SHORT: `1 LONG + 1 SHORT = $1` of terminal collateral-backed payout.
- Initialize every market at `50c LONG / 50c SHORT`. The payout function is centered so the starting underlying price is the fair midpoint of the scalar range.
- Keep later liquidity deposits price-preserving by retuning weights after matched LONG/SHORT inventory is added.

## Log Payout Curve

- Use `long_rate = (ln(resolution_price_usd) - ln(min_price_usd)) / (ln(max_price_usd) - ln(min_price_usd))`.
- With symmetric default bounds, this is equivalent to `long_rate = 0.5 + ln(resolution_price_usd / starting_price_usd) / (2 * ln(market_price_range_multiple))`.
- Clamp `long_rate` to `[0, 1]`, then set `short_rate = 1 - long_rate`.
- With the default 10x symmetric range, the curve maps `0.1x` start to `0c`, about `0.316x` start to `25c`, `1x` start to `50c`, about `3.16x` start to `75c`, and `10x` start to `$1`.
- Use the inverse curve for display: `implied_price_usd = exp(ln(min_price_usd) + long_price * (ln(max_price_usd) - ln(min_price_usd)))`.

## Settlement

- Replace binary threshold settlement with market-end-based scalar settlement.
- Settlement price is the stored rolling 24h median reference price from `market_snapshots`. Use that same 24h median consistently for scalar settlement, rollover trigger checks, current observed price, and deterministic underlying-implied LONG rate calculations.
- At the market end date, resolve from the latest real stored 24h-median market snapshot at or before that time.
- If no valid stored snapshot exists at or before the market end date, do not synthesize a fallback from the starting price. Leave the market unresolved and log a clear warning so the operator can repair snapshots or explicitly void the market.
- Pay each account for both legs: `long_shares * long_rate + short_shares * short_rate`.
- Round each user payout down to integer atomic USDC, sum actual paid amounts, and sweep residual rounding dust with remaining backing as platform revenue. Never round user payouts up.
- Record payout rows with enough context to audit the scalar rates used at resolution.
- Clear both position columns after payout and sweep remaining old-market backing as platform revenue.

## Rollover

- Delete the old lower/upper rollover-range behavior as active logic.
- Compute the deterministic LONG payout implied by each stored 24h-median market snapshot using the same symmetric log curve.
- Add `market_rollover_boundary_rate` to config, defaulting to `0.85`. This config is symmetric: the upper trigger is `long_rate >= market_rollover_boundary_rate`, and the lower trigger is `long_rate <= 1 - market_rollover_boundary_rate`.
- Trigger successor creation immediately when a stored 24h-median snapshot touches either symmetric boundary. Do not require 24 consecutive qualifying snapshots; the 24h median already supplies the intended stability.
- With the default 10x symmetric price range, the `0.85` upper boundary corresponds to about `5.01x` the market creation price, and the derived `0.15` lower boundary corresponds to about `0.20x` the market creation price.
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

- Add public serialization fields for `long_price_usd`, `short_price_usd`, `implied_price_usd`, `predicted_market_cap_usd`, `predicted_nuke_percent`, `min_price_usd`, `max_price_usd`, and the market end date.
- Stop ingesting reported market cap from external APIs. Capture token price and supply instead, then derive market cap as `supply * price`.
- Use Jupiter Tokens v2 supply data for supply. Prefer `circSupply`; if `circSupply` is missing, use `totalSupply` only if the UI/API labels the result as fully diluted. If neither supply exists, keep current and predicted market cap fields `null`.
- Derive current market cap as `supply * current_24h_median_price` when both are available.
- Derive predicted market cap as `supply * implied_price` when both are available.
- Derive predicted nuke percent as `1 - predicted_market_cap / current_market_cap`. Allow it to go negative when the scalar market implies upside.
- Keep missing metric data explicit as `null`; do not synthesize current market cap, current price, or implied price.
- Update the main table columns to include predicted nuke %, implied price, and market end date before PM volume, PM liquidity, underlying volume, and underlying market cap.
- Remove old `dump_percentage` sorting. Supported board sorts should use only scalar/current fields that still exist after the model change, such as predicted nuke %, implied price, market end date, PM volume, PM liquidity, underlying volume, and derived underlying market cap. Preserve null-last sorting in both directions.
- Update token-detail copy and charts away from chance-of-nuke language toward scalar implied-price language.
- Store and render chart snapshots as market-implied price rather than binary chance percent.

## Tests

- Update LONG/SHORT quote, trade, position, and trade-history tests.
- Add scalar settlement tests for low price, middle price, and above-`max_price_usd` capped payout.
- Add rollover tests proving successor creation when one stored 24h-median snapshot touches the upper boundary and when one stored 24h-median snapshot touches the lower boundary.
- Add liquidity-transfer tests proving only AMM-owned complete sets move and old-market trader positions remain solvent.
- Add data-ingestion tests proving reported API market cap is ignored, market cap is derived as `supply * price`, and missing supply leaves market cap fields null.
- Add API/frontend tests for implied price, market-end table column, predicted nuke percent, scalar chart data, scalar-only sort fields, and removal of visible YES/NO wording.

## Validation

- Run `uv run --env-file .env pytest`.
- Run `uv run --env-file .env python -m nukefm serve --host 127.0.0.1 --port 8000` long enough to confirm startup, then stop it.
