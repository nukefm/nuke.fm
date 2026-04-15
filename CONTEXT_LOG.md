# Context Log

## Prediction Market MVP

- The recommended v1 market asks whether a Bags token will be rugged within 90 days of launch.
- The recommended settlement rule defines `rugged` as a drawdown of more than 95% from post-launch ATH within 90 days, measured from hourly liquidity-weighted price snapshots across tracked Solana pairs.
- The product is specified as custodial for the MVP, with Solana USDC deposits and withdrawals onchain and all trading, pricing, and settlement on an internal ledger.
- The recommended trading model is a platform-run binary market maker instead of a user order book because one market per token creates a long tail of thin markets.
- The spec was intentionally rewritten to remove arbitrary product constraints such as position caps, cooldowns, and fixed treasury rules so review stays focused on the core market design and MVP flows.
