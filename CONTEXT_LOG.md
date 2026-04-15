# Context Log

## Repository Naming

- The repo name is `nuke.fm`.
- The durable repo-name metadata currently lives in the repo path, git remote config, and `uv.lock`; the local `.venv` only mirrors that name as generated workspace state.

## Prediction Market MVP

- The public market term is `nuke`, not `rug`.
- Each token has a rolling market series. When a market resolves, the next market is created immediately in `awaiting_liquidity`.
- Each market tracks its own ATH from its own funding time. Earlier market outcomes do not affect later ATH tracking.
- The trading model is an offchain backend-only XYK AMM between `YES` and `NO`, with displayed USDC prices derived from AMM reserves.
- The frontend is read-only. Trading, balances, positions, deposits, and withdrawals are API-only.
- Market liquidity deposits are one-way USDC deposits into a market-specific address. They do not mint LP shares, cannot be withdrawn, and any remaining liquidity after settlement is swept to the platform as revenue.

## First Deliverable Implementation

- The first shipped slice uses a local SQLite catalog with FastAPI and Jinja templates. It ingests Bags launch-feed metadata, creates one current market per token, and renders that catalog through both HTML pages and `/v1/public` JSON.
- Current-market price, liquidity-address, reference-price, ATH, drawdown, and threshold fields remain explicitly `null` in the public API until the later AMM, liquidity, and settlement deliverables exist. They are not inferred or synthesized.
- The Bags launch-feed route is configurable through `config.json` as `bags_launch_feed_path` because the docs clearly expose the feed concept but the concrete path may shift over time.
