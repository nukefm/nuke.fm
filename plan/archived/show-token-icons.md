# Show Token Icons On Board

## Status

Complete. Implemented in commit `53a658d`.

## Goal

Render each token's captured image URL on the main token board so rows are easier to scan visually.

## Completed Work

- Updated the main board token cell in `src/nukefm/templates/index.html` to render `token.image_url` inside the existing token link when present.
- Reused the existing `token-avatar` styling from the detail page and added only board-specific layout needed to keep the table compact.
- Kept missing token image data explicit by omission; no placeholder or fallback image was added.
- Left `MarketStore.list_token_cards()` unchanged because token cards already expose `image_url`.
- Updated `tests/test_app.py` to prove the board includes an image element for a token with an `image_url`.

## Validation

- `uv run --env-file .env pytest tests/test_app.py` could not run in the implementation worktree because `.env` was absent there.
- `uv run --env-file .env pytest` could not run in the implementation worktree because `.env` was absent there.
- `uv run pytest tests/test_app.py` passed in the implementation worktree.
- `uv run pytest` passed in the implementation worktree.
