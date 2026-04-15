## Goal

Add a single overlaid chart to the token detail page that shows:

- the underlying token USD price
- the prediction market's implied probability of `YES` ("nuke")

The chart should be visually clean, easy to read, and aligned on one shared time axis while using separate y-axes for the token price and implied probability.

## Current State

- [`src/nukefm/templates/token.html`](/home/pimania/dev/bagrug/src/nukefm/templates/token.html) renders point-in-time values only. The token page has no chart payload and no chart component.
- [`src/nukefm/app.py`](/home/pimania/dev/bagrug/src/nukefm/app.py) passes through `MarketStore.get_token_detail()` directly to the template.
- [`src/nukefm/markets.py`](/home/pimania/dev/bagrug/src/nukefm/markets.py) currently exposes:
  - current PM YES/NO prices from the live pool
  - hourly settlement snapshots in `market_snapshots`
  - token metric snapshots in `token_metrics_snapshots`
- The existing hourly settlement snapshots are too coarse for the requested PM chart cadence and should remain focused on settlement/reference logic rather than UI charting.
- The repo already uses explicit operator-run commands for data refresh jobs instead of request-time fetching or background loops inside the web app.

## Chosen Approach

- Add a dedicated 5-minute chart snapshot path for current markets instead of reusing `market_snapshots`.
- Each chart point should capture both series at the same timestamp so the overlay uses one clean x-axis:
  - token USD price
  - PM implied probability
- Treat PM implied probability as the current `YES` price, rendered as a percentage rather than displaying separate YES/NO price lines.
- Keep charting scoped to the current market lifecycle only. The token page should show the current series rather than stitching together older resolved markets.
- Use `Chart.js` directly from the server-rendered page without introducing a frontend build step.
- Keep missing history explicit. If there are not enough stored points yet, render a clear empty or sparse-state chart section rather than inventing backfill.

## Data Model

- Add a dedicated table for 5-minute chart history, owned by `MarketStore`.
- Each row should be keyed by market and capture timestamp and store:
  - `market_id`
  - `captured_at`
  - `implied_probability`
  - `underlying_price_usd`
- This table is intentionally separate from:
  - `market_snapshots`, which remains the hourly settlement/reference history
  - `token_metrics_snapshots`, which remains the broader token-metrics snapshot path for sorting and tape metrics

## Data Capture

- Add a dedicated CLI/operator command for chart capture, for example `snapshot-market-charts`.
- That command should snapshot only current actionable series (`open`, and `halted` if the existing UX still wants paused-series history visible).
- For each eligible market:
  - load the live pool and derive the current `YES` price as implied probability
  - fetch the current underlying token USD price from the Jupiter token source already used elsewhere in the repo
  - write one aligned chart point
- Keep this as an operator-scheduled job. Running it every 5 minutes is operational policy, not request-time behavior.

## Page Rendering

- Extend `MarketStore.get_token_detail()` to include a compact serialized chart payload for the current market.
- Pass that payload through [`src/nukefm/app.py`](/home/pimania/dev/bagrug/src/nukefm/app.py) to the template without widening unrelated layers.
- Add one chart section to [`src/nukefm/templates/token.html`](/home/pimania/dev/bagrug/src/nukefm/templates/token.html):
  - one line for token price
  - one line for implied probability
  - shared time axis
  - left y-axis for price
  - right y-axis for probability
- Configure the chart to read as a trader briefing rather than a generic dashboard widget:
  - restrained palette
  - probability shown in percent
  - axis labels that clearly distinguish token price from PM signal

## Implementation Plan

1. Add schema support for 5-minute market chart points.
2. Add a `MarketStore` capture method for chart history that snapshots:
   - PM implied probability from the live pool
   - underlying token USD price from Jupiter
3. Add a CLI command in [`src/nukefm/__main__.py`](/home/pimania/dev/bagrug/src/nukefm/__main__.py) to run that capture path explicitly.
4. Add a serializer in `MarketStore.get_token_detail()` that returns the current market's ordered chart series.
5. Add the overlaid `Chart.js` token-page chart in [`src/nukefm/templates/token.html`](/home/pimania/dev/bagrug/src/nukefm/templates/token.html).
6. Add the minimal CSS needed in [`src/nukefm/static/app.css`](/home/pimania/dev/bagrug/src/nukefm/static/app.css) so the chart section fits the existing visual language.
7. Update docs if needed so the runtime command list includes the new chart snapshot job and clarifies that 5-minute chart history depends on that operator cadence.

## Validation

- Add `pytest` coverage for:
  - 5-minute chart-point persistence
  - chart-point serialization on token detail
  - token-page rendering when chart data exists
  - token-page rendering when chart data is missing or sparse
- Run `uv run --env-file .env pytest`.

## Trade-Offs

- This adds one more snapshot job, but it keeps chart history explicit and avoids mixing UI cadence requirements into settlement logic.
- The overlaid chart will use live token spot prices rather than the hourly rolling-median settlement reference. That is the right trade-off for readability and responsiveness on the token page, but it means the chart is not a direct visualization of settlement mechanics.
- Using `Chart.js` adds a small browser dependency, but it is much simpler and cleaner than building a custom dual-axis chart by hand inside the current server-rendered stack.

## Unresolved Questions

- None for planning.
