## Goal

Automatically seed `1.00 USDC` of initial liquidity into the top 100 current markets, ranked by each token's latest market cap, so those markets open without requiring a manual first depositor.

## Current State

- Markets already have dedicated public USDC liquidity deposit accounts.
- A market opens only when a credited liquidity deposit is observed through the normal reconciliation path.
- The treasury layer can already derive and fund market-associated token accounts and transfer USDC on-chain.
- The codebase does not yet store token market cap or support sorting by token metrics.

## Chosen Approach

- Implement auto-seeding as an explicit operator job, not as a side effect of public reads or normal API requests.
- Seed markets through the existing public-liquidity-account flow instead of inventing a parallel internal opening path.
- Rank candidate markets strictly by token market cap in descending order.
- Reuse the same token-metrics ingestion/storage/ranking machinery that will be added for the later todo covering sorting by market cap, volume, liquidity, and drawdown.
- Do not fall back to launch time or any other ordering when market-cap data is missing. Markets without market-cap data are skipped.

## Selection Rule

- Consider only current markets whose state is `awaiting_liquidity`.
- Join each candidate market to the latest stored token metrics.
- Sort by token market cap descending.
- Take the first 100 eligible markets.
- Exclude any market that has already been auto-seeded.
- Exclude any market that already has a credited liquidity deposit or an open pool.

## Implementation Plan

1. Add a small persistent bookkeeping table for platform auto-seeds so each market can be seeded at most once and failures remain visible.
2. Make the bookkeeping lifecycle explicit and idempotent:
   - record a pending/broadcast-intent row before sending funds
   - store the broadcast signature and timestamps once the transfer is accepted
   - only mark the auto-seed complete after the normal reconciliation path credits the deposit
   - never silently retry a market that already has a pending or broadcasted seed
3. Add a command dedicated to platform seeding, for example `seed-markets`, with config-driven defaults for:
   - seed amount in USDC
   - maximum number of markets to seed
4. Make that command ensure market liquidity accounts exist before ranking or transfer attempts so every selected market has a deposit ATA.
5. Make that command select eligible `awaiting_liquidity` markets using the latest token market-cap data, descending.
6. Add the missing treasury helper that transfers USDC from the platform treasury ATA to a market liquidity ATA.
7. Use the treasury wallet to transfer `1.00 USDC` from the platform treasury ATA to each selected market's liquidity deposit ATA.
8. After sending transfers, run the normal market-liquidity reconciliation path so those deposits are credited exactly like any external deposit.
9. Record per-market auto-seed status with enough detail to audit success or failure.
10. Keep retries explicit:
   - successful auto-seeds must never run twice for the same market
   - failed attempts may be retried deliberately
   - pending/broadcasted rows must block duplicate sends until the operator explicitly clears or finalizes them
   - missing market-cap data should remain a visible skip reason, not a hidden fallback
11. Add tests that cover:
   - ranking by market cap descending
   - skipping markets with missing market-cap data
   - skipping markets that already opened or were previously auto-seeded
   - skipping markets with a pending or broadcasted auto-seed
   - opening seeded markets through the normal credited-deposit path

## Dependency On Later Metrics Work

- This todo depends on the later token-metrics/sorting work for a canonical market-cap source and ranking query.
- The seeding job should call into that shared ranking path rather than reimplementing market-cap fetching or sorting locally.
- During implementation, if the sorting todo has not yet been integrated, the seeding work should first extract or introduce the shared token-metrics layer that both todos will use.

## Validation

- Run `pytest` with new coverage for seeding selection and reconciliation.
- Run a deterministic local flow with fake treasury balances and fake token metrics to confirm:
   - only the top 100 by market cap are selected
   - seeded markets open through the existing liquidity-credit mechanism
   - duplicate auto-seeding does not occur

## Trade-Offs

- This keeps one canonical liquidity-opening path, which is simpler and less error-prone than an internal shortcut.
- It introduces an operational dependency on fresh token market-cap data, but that dependency is already required for the sorting feature and should be shared rather than duplicated.

## Unresolved Questions

- None for planning.
