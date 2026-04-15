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
