## Goal

Allow current markets to be sorted both ascending and descending by:

- market liquidity
- dump percentage
- underlying token volume
- underlying token market cap

The public JSON token list and the read-only frontend should both support the same sorting behavior.

## Current State

- `MarketStore.list_token_cards()` is the live path used by the public API and frontend, and it currently returns current markets in a fixed default order based on launch time.
- The public API endpoint `/v1/public/tokens` exposes no sorting controls.
- The frontend market board at `/` renders whatever order `list_token_cards()` returns.
- The codebase already exposes market liquidity and drawdown on the current market object.
- The codebase does not yet store or expose token-level market cap or token-level trading volume.

## Chosen Approach

- Add a shared token-metrics snapshot layer and use it as the single source for:
  - sorting current markets by underlying volume and market cap
  - selecting top markets by market cap for the auto-seeding todo
- Use DexScreener token-pairs data for current token metrics because the official API already exposes `volume`, `liquidity`, and `marketCap` on token pair responses.
- Keep sorting orchestration thin:
  - `MarketStore` should accept a concise sort specification
  - token-metrics capture should be handled elsewhere
  - API/frontend should pass sort controls through without duplicating ranking logic

## Metric Definitions

- `market liquidity`: the internal nuke.fm market pool liquidity already exposed as `current_market.total_liquidity_usdc`
- `dump percentage`: `current_market.drawdown_fraction`
- `underlying volume`: sum of `volume.h24` across the token's current DexScreener pairs with usable data
- `underlying market cap`: `marketCap` taken from the token's most-liquid current DexScreener pair with a non-null market-cap field

These rules keep token-level metrics explicit and deterministic:

- summing volume across pairs reflects whole-token trading activity
- market cap is not summed across pairs because that would double-count the same asset
- most-liquid-pair market cap is the canonical market-cap source

## Missing-Data Behavior

- Do not invent or backfill missing token metrics.
- If a token lacks the metric required for the requested sort key, expose that metric as `null`.
- Keep `null` values visibly distinct and order them last for both ascending and descending sorts, so missing data does not quietly dominate the list.

## Implementation Plan

1. Extend the DexScreener client model to parse the additional fields needed for token metrics:
   - pair liquidity
   - 24h volume
   - market cap
2. Add storage for token metric snapshots keyed by token mint and capture time.
3. Add a token-metrics capture job that fetches current pair data for tracked tokens and derives:
   - summed 24h underlying volume
   - canonical underlying market cap from the most-liquid pair
4. Expose the latest stored token metrics on each token card/current market payload without widening irrelevant layers unnecessarily.
5. Add an explicit CLI/operator entrypoint for refreshing token metrics, for example `sync-token-metrics`, because metrics freshness in this repo is driven by periodic commands rather than background mutation during reads.
6. Refactor `MarketStore.list_token_cards()` to accept a compact sort spec, for example:
   - sort field
   - direction
7. Implement sortable query logic for:
   - market liquidity up/down
   - dump percentage up/down
   - underlying volume up/down
   - underlying market cap up/down
8. Add public API support so `/v1/public/tokens` accepts sorting controls and returns the ordered list.
9. Add frontend controls on the market board so users can choose the sort key and direction without changing the token-card layout more than necessary.
10. Preserve the current sort choice across the page's timed refresh by round-tripping it through query parameters.
11. Keep one default order when no sort is supplied. The existing launch-time order is acceptable as the unsorted default.
12. Reuse the same ranking path for the auto-seeding todo so "top 100 by market cap" does not reimplement token-metric selection.

## Validation

- Add tests for token-metric aggregation rules:
  - volume summed across pairs
  - market cap taken from the most-liquid pair
- Add API tests for all supported sort keys and both directions.
- Add frontend tests or response assertions confirming sort controls round-trip and the rendered order changes accordingly.
- Verify missing metric values remain `null` and sort to the end explicitly.

## Trade-Offs

- This introduces one more data-refresh job, but it keeps the metric source shared and avoids scattering market-cap/volume lookups through API and UI code.
- Using the most-liquid pair for market cap is an explicit heuristic, but it is much cleaner than mixing or summing pair-level market caps.

## Sources

- DexScreener token-pairs API exposes `volume`, `liquidity`, and `marketCap`: https://docs.dexscreener.com/api/reference

## Unresolved Questions

- None for planning.

## Status

- Completed on the coordinator branch after integrating the dedicated worktree commit.
- Added DexScreener-backed token metric snapshots plus the `sync-token-metrics` CLI command.
- Routed current-market sorting through `MarketStore.list_token_cards()` for the public API and index page.
- Added null-last sorting for liquidity, dump percentage, underlying volume, and underlying market cap.
- Preserved sort selection through query parameters on the auto-refreshing board.
- Validation completed with `uv run --env-file .env pytest`.
