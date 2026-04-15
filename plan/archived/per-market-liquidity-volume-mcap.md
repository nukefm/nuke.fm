## Goal

Ensure each market shown in the public UI clearly displays the three supporting market-context numbers that matter most:

- PM liquidity
- PM trading volume over the last 24 hours
- underlying token market cap

These values should be visible per market rather than scattered or partially missing across the board and token-detail views.

## Current State

- [`src/nukefm/markets.py`](/home/pimania/dev/bagrug/src/nukefm/markets.py) already serializes:
  - PM liquidity as `total_liquidity_usdc`
  - underlying token market cap as `underlying_market_cap_usd`
- The public UI currently shows those inconsistently:
  - liquidity appears in some places
  - underlying market cap appears in some places
  - PM trading volume is not exposed on the public UI
- PM trades are stored in `market_trades`, so PM volume is derivable from first-party ledger data rather than from an external API.

## Chosen Approach

- Add one explicit serialized field for PM 24-hour trading volume on each market payload.
- Compute PM 24-hour trading volume from `market_trades.cash_amount_atomic` filtered to the trailing 24 hours for that market.
- Use SQL aggregation for the market set being rendered so this does not become an N+1 query path on the board.
- Present PM liquidity, PM 24-hour volume, and underlying market cap as one coherent per-market metric group on the frontend.
- Keep the work scoped to current markets shown on the frontend; resolved market history does not need a separate redesign.

## Metric Definitions

- `PM liquidity`: the internal market pool liquidity already exposed as `total_liquidity_usdc`
- `PM 24h trading volume`: the sum of `market_trades.cash_amount_atomic` for trades in that market whose `created_at` falls within the trailing 24 hours from render time / serialization time
- `underlying token market cap`: the latest stored token market cap snapshot already exposed as `underlying_market_cap_usd`

## Data Semantics

- PM 24-hour trading volume is a real zero when a market has had no trades in the trailing 24-hour window.
- This is not a fallback or synthetic value; it is directly derivable from the internal trade ledger.
- The field should be exposed on the public market payload because it is genuine market state, not merely view formatting.

## Implementation Plan

1. Add a compact SQL aggregation path in [`src/nukefm/markets.py`](/home/pimania/dev/bagrug/src/nukefm/markets.py) that returns trailing-24-hour PM volume per market.
2. Extend market serialization to include `pm_volume_24h_usdc`.
3. Reuse that same serialized field for both:
   - the board cards in [`src/nukefm/templates/index.html`](/home/pimania/dev/bagrug/src/nukefm/templates/index.html)
   - the token detail page in [`src/nukefm/templates/token.html`](/home/pimania/dev/bagrug/src/nukefm/templates/token.html)
4. Refactor the per-market UI layout so PM liquidity, PM 24h volume, and underlying market cap read as one consistent trio instead of appearing in unrelated sections or copy fragments.
5. Keep this restructuring minimal and avoid duplicating the same three values multiple times in the same market view.

## Validation

- Add or update `pytest` coverage in [`tests/test_markets.py`](/home/pimania/dev/bagrug/tests/test_markets.py) for PM 24-hour volume serialization.
- Add or update frontend assertions in [`tests/test_app.py`](/home/pimania/dev/bagrug/tests/test_app.py) so the rendered market views visibly contain the metric trio.
- Run `uv run --env-file .env pytest`.

## Trade-Offs

- Computing PM 24-hour volume at read time keeps the data model simple and accurate without introducing another snapshot table.
- Time-windowed volume means the number will naturally drift as old trades age out of the window, but that matches the requested semantics and is more useful than cumulative volume for the UI.

## Unresolved Questions

- None for planning.

## Status

- Completed on the coordinator branch after integrating the dedicated worktree commit.
- Added `pm_volume_24h_usdc` to serialized market payloads using trailing-24-hour aggregation from `market_trades`.
- Surfaced PM liquidity, PM 24h volume, and underlying token market cap as a consistent per-market trio on the board and token page.
- Validation completed with `uv run pytest tests/test_markets.py tests/test_app.py` and `uv run pytest`.
