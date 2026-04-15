## Goal

Make every dollar-denominated value in the HTML UI render with a `$` prefix instead of appearing as a bare number, while keeping very small prices readable for memecoin-style values that contain many leading zeroes.

## Current State

- The public pages in [`src/nukefm/templates/index.html`](/home/pimania/dev/bagrug/src/nukefm/templates/index.html) and [`src/nukefm/templates/token.html`](/home/pimania/dev/bagrug/src/nukefm/templates/token.html) render numeric strings directly.
- Those numeric strings come from backend serializers in [`src/nukefm/markets.py`](/home/pimania/dev/bagrug/src/nukefm/markets.py).
- UI activity summaries in `MarketStore._recent_activity()` also embed raw numeric strings followed by `USDC`, so the inconsistency is not limited to Jinja templates.
- The raw API payloads already use plain numeric strings and should remain machine-friendly.

## Chosen Approach

- Treat this as an HTML presentation concern, not a public-API change.
- Add one shared USD-display formatter for templates and UI-facing summary copy so the display rule lives in one place.
- Keep serialization of numeric values unchanged for JSON consumers.
- Apply the formatter consistently anywhere the user is reading a dollar amount on the board or token page.

## Dollar Formatting Rules

- Prefix all displayed dollar-denominated values with `$`.
- Keep missing data explicit. Do not turn `null` or absent values into `$0`.
- Use ordinary trimmed decimal formatting for normal-sized values.
- Add a dedicated tiny-price formatting rule for price-like values so memecoin numbers remain readable without scientific notation.

## Tiny-Price Readability Rule

- For very small price values, keep fixed-decimal formatting rather than switching to scientific notation.
- Preserve the leading zero run, then show a concise but meaningful number of significant digits after the first non-zero digit.
- The intended outcome is readable values like:
  - `$0.00001234`
  - `$0.0000004567`
- This tiny-price rule should apply to price-like fields:
  - PM YES/NO prices
  - reference price
  - threshold price
  - ATH price
- It does not need to apply to large aggregate figures such as:
  - market cap
  - volume
  - PM liquidity

## Implementation Plan

1. Add a shared frontend USD formatter, likely registered as a Jinja helper/filter in [`src/nukefm/app.py`](/home/pimania/dev/bagrug/src/nukefm/app.py), so templates do not each hand-roll their own `$` prefixing logic.
2. Give that formatter two modes:
   - general USD display for aggregate dollar values
   - tiny-price-aware USD display for price fields
3. Update [`src/nukefm/templates/index.html`](/home/pimania/dev/bagrug/src/nukefm/templates/index.html) to use the shared formatter for all dollar-denominated fields on the board.
4. Update [`src/nukefm/templates/token.html`](/home/pimania/dev/bagrug/src/nukefm/templates/token.html) to use the same formatter for all dollar-denominated fields on the token page.
5. Refactor UI-facing activity summaries in [`src/nukefm/markets.py`](/home/pimania/dev/bagrug/src/nukefm/markets.py) so they use the same formatting convention instead of embedding bare values plus `USDC`.
6. Leave non-dollar values unchanged:
   - drawdown fractions
   - timestamps
   - addresses
   - sequence numbers

## Validation

- Add or update `pytest` assertions in [`tests/test_app.py`](/home/pimania/dev/bagrug/tests/test_app.py) to confirm rendered HTML shows `$`-prefixed values.
- Add coverage for tiny-price rendering so very small price strings remain readable and do not degrade into awkward long raw decimals or scientific notation.
- Run `uv run --env-file .env pytest`.

## Trade-Offs

- Keeping the change in the presentation layer avoids churning the public API, but it means UI activity copy and template values both need to route through the same formatter to stay truly consistent.
- Tiny-price formatting adds a second display rule, but that is better than pretending memecoin-scale prices fit the same formatting shape as larger dollar figures.

## Unresolved Questions

- None for planning.

## Status

- Completed on the coordinator branch after integrating the dedicated worktree commit.
- Added a shared UI-only USD formatter for the public pages.
- Prefixed public HTML dollar values with `$` while keeping public JSON numeric fields unchanged.
- Applied readable tiny-price rendering for very small price values.
- Updated recent-activity copy to use the same dollar display convention.
- Validation completed with `uv run pytest tests/test_app.py` and `uv run pytest` in the dedicated worktree.
