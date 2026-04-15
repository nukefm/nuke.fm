## Goal

Replace the current ATH/drawdown-based market lifecycle with a fixed-anchor market model where:

- each market is created with a starting underlying price
- each market's nuke threshold is fixed up front at a configurable fraction of that starting price
- the currently frontend-visible market rolls forward to a newly created successor market when the underlying price moves outside a configurable range around that market's starting price
- older rolled markets remain active, tradable, and later resolvable through the API, but are hidden from the frontend

## Status

Completed on `master` in:

- `42909b1` `Implement fixed threshold market rollover`
- `eae07ad` `Backfill legacy market lifecycle anchors`

Validation completed with:

- `uv run pytest tests/test_catalog.py tests/test_markets.py tests/test_app.py tests/test_private_api.py tests/test_cli.py`
- `uv run pytest`

The frontend should present the market prompt as:

- `Will {symbol} nuke by {x}% by {date}?`

where `{x}%` is derived from the current observed price versus that market's fixed threshold price.

## Current State

- [`src/nukefm/catalog.py`](/home/pimania/dev/bagrug/src/nukefm/catalog.py) assumes there can be only one active market per token.
- [`src/nukefm/markets.py`](/home/pimania/dev/bagrug/src/nukefm/markets.py) currently uses hourly reference snapshots to track:
  - ATH
  - drawdown
  - a threshold derived from ATH
- Market resolution currently means:
  - resolve `YES` when price later falls below an ATH-derived threshold
  - otherwise resolve `NO` at expiry
- Public token serialization currently exposes one `current_market` plus `past_markets`, which means an older still-open market would disappear from both frontend and API detail shapes if a second active market were introduced.

## Chosen Approach

- Remove the ATH/drawdown threshold model from active behavior.
- Give every market fixed lifecycle anchors at creation time.
- Separate market tradability/state from frontend visibility so more than one unresolved market can exist for the same token without confusing the board UI.
- Keep only one frontend-visible current market per token.
- Keep older rolled markets accessible through the public API and tradable/resolvable through the existing market-id-based private APIs.

## Fixed Price Anchors Per Market

Each market should persist explicit fixed price anchors on the market row:

- `starting_price_usd`
- `threshold_price_usd`
- `range_floor_price_usd`
- `range_ceiling_price_usd`

These are created once and do not float afterward.

Default formulas should be configurable in `config.json`:

- `threshold_price_usd = starting_price_usd * market_resolution_threshold_fraction`
- `range_floor_price_usd = starting_price_usd * market_rollover_lower_bound_fraction`
- `range_ceiling_price_usd = starting_price_usd * market_rollover_upper_bound_fraction`

With the requested default behavior, the config-backed defaults are:

- `market_resolution_threshold_fraction = 0.10`
- `market_rollover_lower_bound_fraction = 0.25`
- `market_rollover_upper_bound_fraction = 4.0`

This replaces the old single `market_threshold_fraction` config key. Do not keep the old key for backward compatibility.

## Canonical Price Requirement

- Market creation now requires a real observed underlying price at creation time.
- Do not create a market with null or guessed anchors.
- If the canonical creation price cannot be obtained, market creation should fail loudly rather than silently creating a half-defined market.

## Monitoring And Resolution Rule

- Store ongoing observed price history for markets in snapshot rows.
- Simplify the snapshot meaning so the monitored price series is the only lifecycle input that matters for:
  - current price display
  - threshold breach checks
  - range-exit checks
- Remove ATH/drawdown behavior from the active resolution path.

Resolution rule:

1. A market resolves `YES` on the first monitored snapshot at or below that market's fixed `threshold_price_usd` before expiry.
2. A market resolves `NO` at expiry if no such threshold breach occurred.

This means the old ATH, ATH timestamp, and drawdown-derived threshold are no longer the operative logic and should be removed from the active behavior.

## Successor Market Rule

- Only the one frontend-visible active market for a token may trigger successor creation.
- When that market's monitored price moves outside its fixed range:
  - below `range_floor_price_usd`
  - or above `range_ceiling_price_usd`
- immediately create a successor market for that token.

The successor market should:

- get a new sequence number
- get its own fresh `starting_price_usd`
- compute its own fixed threshold and range from that new start price
- become the new frontend-visible current market

The old market should:

- remain in its existing active state (`awaiting_liquidity`, `open`, or `halted`)
- remain tradable if it was tradable before
- remain eligible for normal later resolution
- become hidden from the frontend

Old rolled markets must not themselves keep spawning additional successors after they are hidden. Only the currently frontend-visible market is allowed to roll the displayed series forward.

## Frontend Visibility Model

- Add an explicit frontend-visibility flag to markets instead of overloading the existing state machine.
- Keep state semantics focused on tradability/resolution.
- Keep visibility semantics focused on which market appears on:
  - `/`
  - `/tokens/{mint}`

Recommended minimum fields:

- `is_frontend_visible`
- optional audit linkage such as `superseded_by_market_id` and/or `superseded_at` if that materially simplifies tracing

The important rule is semantic separation:

- state answers whether the market is open, halted, awaiting liquidity, or resolved
- visibility answers whether the market is the current frontend market for that token

## Public API And UI Shape

- The board and HTML token page should show only the frontend-visible market.
- Public token detail JSON should keep exposing that visible market as `current_market`.
- Add a separate collection for unresolved but frontend-hidden markets, for example `hidden_active_markets`, so those markets remain publicly inspectable through the API.
- Keep `past_markets` for resolved/void history only.

Because the display prompt now changes as price changes, the frontend headline should be computed dynamically at serialization/render time rather than stored as a permanently static question string.

## UI Question Format

Render the visible market prompt as:

- `Will {symbol} nuke by {x}% by {date}?`

Where:

- `{date}` is the market expiry date
- `{x}%` is the remaining drop from the current observed price to this market's fixed threshold price

Recommended calculation:

- `remaining_drop_fraction = 1 - (threshold_price_usd / current_observed_price_usd)`

Serialize a UI-facing field for this prompt instead of making templates reconstruct it ad hoc.

To keep UI and lifecycle semantics aligned, derive `current_observed_price_usd` from the same monitored market-price series used for threshold and range checks. At creation time, the new market's `starting_price_usd` is also its initial observed price.

## Configuration

Keep lifecycle coefficients in `config.json`, not as hardcoded constants:

- `market_duration_days` (existing)
- `market_resolution_threshold_fraction`
- `market_rollover_lower_bound_fraction`
- `market_rollover_upper_bound_fraction`

If implementation needs any additional explicit lifecycle knobs, they should also be added to `config.json` rather than embedded directly in code.

## Code Structure Direction

- Stop using `Catalog._ensure_current_market()` as a blind "make sure there is one active market" helper.
- Refactor market creation into one lifecycle-aware path that can:
  - fetch the canonical creation price
  - stamp fixed anchors
  - assign visibility
  - create successor markets deliberately
- Keep token ingestion in `Catalog`, but move price-dependent market-series creation into the lifecycle code that already owns monitoring and resolution semantics.

## Implementation Plan

1. Extend the market schema to store fixed anchors and frontend visibility metadata.
2. Replace the old threshold-fraction config key with explicit configurable lifecycle fractions in [`config.json`](/home/pimania/dev/bagrug/config.json) and [`src/nukefm/config.py`](/home/pimania/dev/bagrug/src/nukefm/config.py).
3. Refactor market creation so new markets are created through a lifecycle-aware path that requires a real creation price and stamps:
   - starting price
   - fixed threshold
   - fixed range floor
   - fixed range ceiling
   - frontend visibility
4. Simplify market snapshot semantics so the stored price series supports:
   - current observed price
   - threshold breach checks
   - range exit checks
   and no longer relies on ATH/drawdown behavior.
5. Rewrite `resolve_markets()` in [`src/nukefm/markets.py`](/home/pimania/dev/bagrug/src/nukefm/markets.py) to resolve against the fixed threshold rule.
6. Add successor-market creation when the frontend-visible market exits its configured range, and hide the superseded market from the frontend without closing it.
7. Refactor public market-selection queries so:
   - list endpoints pick the frontend-visible market
   - token detail also returns `hidden_active_markets`
8. Update the frontend and public serializer to expose the dynamic display prompt:
   - `Will {symbol} nuke by {x}% by {date}?`
9. Remove or replace obsolete UI fields and copy that still describe:
   - ATH
   - drawdown
   - floating ATH-based threshold logic
10. Update tests across:
   - market creation
   - range-triggered successor creation
   - hidden-active-market API visibility
   - fixed-threshold resolution
   - frontend-visible market selection

## Validation

- Add or update `pytest` coverage in:
  - [`tests/test_catalog.py`](/home/pimania/dev/bagrug/tests/test_catalog.py)
  - [`tests/test_markets.py`](/home/pimania/dev/bagrug/tests/test_markets.py)
  - [`tests/test_app.py`](/home/pimania/dev/bagrug/tests/test_app.py)
- Cover at minimum:
  - initial market creation stamps fixed anchors from config-backed coefficients
  - successor market is created when price leaves the configured range
  - superseded market remains active but frontend-hidden
  - public token detail exposes hidden active markets
  - fixed-threshold `YES` resolution
  - expiry-based `NO` resolution
  - frontend question text uses the dynamic `{x}%` wording
- Run `uv run --env-file .env pytest`.

## Trade-Offs

- This is a more invasive refactor than a threshold tweak because the repo currently assumes one active market per token. The clean fix is to separate frontend visibility from state instead of inventing a misleading pseudo-state.
- The dynamic frontend prompt becomes a computed display field rather than a permanently static stored question. That is necessary because `{x}%` changes with price.
- Requiring a real creation price makes the lifecycle stricter, but it prevents silently malformed markets and matches the instruction not to hide missing data.

## Unresolved Questions

- None for planning.
