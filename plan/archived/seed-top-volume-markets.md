# Seed Top Volume Markets

## Status

Complete. Implemented in commit `a32000a`.

## Goal

Initialize the highest-activity visible markets by debt-funded seed liquidity, using the latest stored underlying-token 24h volume as the ranking signal.

## Completed Work

- Replaced the market-cap-ranked weekly seeding path in `MarketStore` with a volume-ranked path using `token_metrics_snapshots.underlying_volume_h24_usd`.
- Renamed touched code, tests, and documentation from market-cap targeting to volume targeting instead of keeping obsolete compatibility wrappers.
- Changed the `seed-weekly-liquidity` CLI default `--top` from `10` to `4`.
- Updated the EC2 weekly seed service to call `seed-weekly-liquidity --top 4 --amount-usdc 1`.
- Preserved weekly idempotency behavior so a market already seeded in the same week is skipped.
- Preserved treasury-debt accounting behavior so each debt-funded seed creates an explicit treasury debt entry.
- Excluded markets whose latest underlying 24h volume is missing.
- Kept initialization on the existing seed event and pool liquidity path rather than directly editing SQLite market rows.

## Assumptions

- "Volume" means underlying token 24h volume from the latest token metric snapshot, not prediction-market trade volume.
- "Initialize" means applying debt-funded seed liquidity through the existing `market_liquidity_seed_events` / pool liquidity path, not making manual on-chain deposits.

## Validation

- `uv run --env-file .env pytest tests/test_markets.py tests/test_cli.py` could not run in the implementation worktree because `.env` was absent there.
- `uv run pytest tests/test_markets.py tests/test_cli.py` passed in the implementation worktree.
- `uv run pytest` passed in the implementation worktree.
