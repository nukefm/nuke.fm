# Seed Top Volume Markets

## Goal

Initialize the highest-activity visible markets by debt-funded seed liquidity, using the latest stored underlying-token 24h volume as the ranking signal.

## Implementation Plan

- Replace the current market-cap-ranked weekly seeding path in `MarketStore` with a volume-ranked path using `token_metrics_snapshots.underlying_volume_h24_usd`.
- Rename touched code, tests, and documentation from market-cap targeting to volume targeting instead of keeping obsolete compatibility wrappers.
- Change the `seed-weekly-liquidity` CLI default `--top` from `10` to `4`.
- Update the EC2 weekly seed service to call `seed-weekly-liquidity --top 4 --amount-usdc 1`.
- Preserve the existing weekly idempotency behavior: a market already seeded in the same week is skipped.
- Preserve the existing treasury-debt accounting behavior: every debt-funded seed creates an explicit treasury debt entry that can be paid down later.
- Exclude markets whose latest underlying 24h volume is missing. Do not synthesize or default missing volume.
- Do not directly edit SQLite market rows during implementation; market initialization must continue through the seed event and pool liquidity path so related bookkeeping remains consistent.

## Assumptions

- "Volume" means underlying token 24h volume from the latest token metric snapshot, not prediction-market trade volume.
- "Initialize" means applying debt-funded seed liquidity through the existing `market_liquidity_seed_events` / pool liquidity path, not making manual on-chain deposits.

## Validation

- Update tests to prove top-volume ordering, missing-volume exclusion, top-4 CLI default behavior, and repeat-run idempotency.
- Run targeted tests with `uv run --env-file .env pytest tests/test_markets.py tests/test_cli.py`.
- Run the full suite with `uv run --env-file .env pytest`.
