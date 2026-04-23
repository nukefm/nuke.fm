# Show Token Icons On Board

## Goal

Render each token's captured image URL on the main token board so rows are easier to scan visually.

## Implementation Plan

- Update the main board token cell in `src/nukefm/templates/index.html` to render `token.image_url` inside the existing token link when present.
- Reuse the existing `token-avatar` styling from the detail page and add only the board-specific layout needed to keep the table compact.
- Do not create a placeholder or fallback image when `image_url` is missing. Missing token image data should remain explicit by omission rather than being hidden by synthetic UI.
- Keep `MarketStore.list_token_cards()` unchanged unless implementation proves the serialized token card is missing `image_url`; it already appears to expose the field.
- Add or update an app-level test in `tests/test_app.py` proving the board includes an image element for a token with an `image_url`.

## Validation

- Run targeted app tests with `uv run --env-file .env pytest tests/test_app.py`.
- Run the full suite with `uv run --env-file .env pytest`.
