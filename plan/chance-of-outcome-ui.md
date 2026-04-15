## Goal

Remove raw `YES` / `NO` price display from the public UI and instead present a single more intuitive probability-style read: the market's chance of the stated outcome.

For these markets, that means showing the chance that the token will nuke rather than showing paired YES/NO prices.

## Current State

- [`src/nukefm/templates/index.html`](/home/pimania/dev/bagrug/src/nukefm/templates/index.html) still renders raw `YES` / `NO` prices in the board-card PM copy.
- [`src/nukefm/templates/token.html`](/home/pimania/dev/bagrug/src/nukefm/templates/token.html) still renders raw `YES` / `NO` prices in:
  - the top summary card
  - the signal map section
  - surrounding explanatory copy
- [`src/nukefm/markets.py`](/home/pimania/dev/bagrug/src/nukefm/markets.py) already has the underlying value needed for this because the current `YES` price is the probability of the market's stated outcome.

## Chosen Approach

- Keep the internal market mechanics unchanged. The AMM, trade APIs, and position logic still use `yes` and `no` because that distinction is real in execution.
- Change only the public-facing presentation for the board and token page.
- Add one explicit UI-facing field for the chance of the stated outcome so templates do not need to reconstruct it themselves.
- Present that value as a percentage and describe it as the `chance of` the outcome, not as `implied probability`.

## Display Rules

- The market question remains the canonical source of what the outcome means.
- Since the question is phrased as `Will {symbol} nuke ...`, the `YES` side should be shown as the:
  - `Chance of nuke`
  - or equivalent `chance of` wording that reads naturally in the surrounding UI copy
- Remove raw paired YES/NO price display from the public UI entirely.
- Update any copy that currently references `YES / NO skew` so it instead talks about the chance of the outcome.

## Implementation Plan

1. Extend public market serialization in [`src/nukefm/markets.py`](/home/pimania/dev/bagrug/src/nukefm/markets.py) with a UI-facing chance-of-outcome field derived from the `YES` price.
2. Format that field for public-page use as a percentage-style value rather than a decimal price.
3. Update [`src/nukefm/templates/index.html`](/home/pimania/dev/bagrug/src/nukefm/templates/index.html) so board cards show the chance of the market outcome instead of `YES x / NO y`.
4. Update [`src/nukefm/templates/token.html`](/home/pimania/dev/bagrug/src/nukefm/templates/token.html) so:
   - the summary card uses the chance-of-outcome read
   - the signal map uses that same concept instead of separate YES and NO rows
   - the surrounding explanatory copy avoids technical AMM language
5. Keep trade and quote payloads unchanged where raw yes/no semantics are still required for API correctness.
6. Align this field with the token-page chart todo so the PM line represents the same chance-of-outcome concept the rest of the page uses.

## Validation

- Add or update `pytest` assertions in [`tests/test_app.py`](/home/pimania/dev/bagrug/tests/test_app.py) to confirm the public pages no longer render raw YES/NO price labels.
- Add assertions that the rendered UI instead shows the chance-of-outcome terminology and value.
- Run `uv run --env-file .env pytest`.

## Trade-Offs

- This makes the public UI clearer for token traders, while the backend still keeps the true yes/no execution model where it belongs.
- Exposing both raw prices and chance-of-outcome on the same public page would create duplicated concepts, so the public UI should commit to the simpler representation.

## Unresolved Questions

- None for planning.
