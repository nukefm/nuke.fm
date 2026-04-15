## Goal

Replace the current spot-price-based settlement path with a manipulation-resistant rule that measures market ATH and drawdown from a rolling 24-hour median USD price. The median series should become the canonical settlement reference used for ATH tracking, threshold calculation, drawdown reporting, and `resolved_yes` / `resolved_no` decisions.

## Current State

- `src/nukefm/markets.py` currently captures one hourly liquidity-weighted spot reference price per open market.
- The same hourly snapshot row stores ATH, drawdown, and threshold.
- `resolve_markets` resolves `YES` when the latest hourly reference price falls below the threshold after the ATH timestamp.
- `src/nukefm/dexscreener.py` only provides current pair state, which is sufficient for spot snapshots but not for a defensible 24-hour median of historical prices.

## Chosen Approach

- Use historical Solana DEX trade data as the settlement source for this median-based rule.
- Treat Bitquery `DEXTradeByTokens` historical queries as the primary data source because they support time-windowed historical trade retrieval and median-style aggregation over trade prices.
- Keep Bags token discovery unchanged.
- Treat the Bags `GET /solana/bags/pools` endpoint as optional supporting metadata for canonical pool identification / allowlisting if needed during implementation.
- Do not use the Jupiter holders endpoint for this work because it does not provide price history.

## Settlement Rule

- For each market, compute an hourly settlement point from the median USD trade price observed in the trailing 24 hours ending at that hour.
- Clip the lookback window to `max(market_start, snapshot_hour - 24h)` so a market never uses pre-open trades in its own settlement series.
- Use that rolling 24-hour median price as the market's canonical `reference_price_usd`.
- Compute `ath_price_usd` as the highest recorded median reference price since `market_start`.
- Compute `threshold_price_usd` as `ath_price_usd * market_threshold_fraction`.
- Compute `drawdown_fraction` from the current median reference price versus the ATH.
- Resolve `YES` only when an hourly median reference price after the ATH timestamp falls at or below the threshold before expiry.
- Resolve `NO` at expiry otherwise.

## Implementation Plan

1. Add a historical price provider abstraction dedicated to settlement/reference pricing instead of reusing the current DexScreener spot client.
2. Implement a Bitquery-backed provider that can fetch trade prices for a token across a requested time window and normalize the returned rows into the internal settlement format.
3. Decide the exact market filter once implementation starts:
   - Prefer token-mint-based historical trade queries.
   - Add a pool allowlist from the Bags pools endpoint only if token-wide queries pull in clearly irrelevant venues or noisy outliers.
4. Introduce storage for raw historical settlement inputs if needed for replay/auditability.
   - The minimum acceptable version is to persist the derived hourly median market snapshot.
   - If Bitquery row volume and schema are manageable, persist the raw hourly trade inputs used to derive each median so settlement remains inspectable.
5. Refactor snapshot capture so hourly market snapshots are derived from the trailing 24-hour trade window instead of a single point-in-time DexScreener poll.
6. Keep the existing market-facing snapshot fields and API/template shape intact where possible, but change their semantics to median-based values.
7. Fix `resolve_markets` so it resolves from any qualifying median-derived hourly snapshot before expiry instead of only the latest snapshot row.
8. Make the resolution query inspect the historical snapshot series for each unresolved market and resolve `YES` on the first qualifying post-ATH breach.
9. Update docs and config:
   - document that settlement reference prices are 24-hour rolling medians
   - add any required Bitquery configuration and credentials handling
   - remove or narrow wording that still describes hourly spot snapshots as the settlement primitive
10. Extend tests to cover:
   - ATH tracking from median prices
   - drawdown and threshold calculation from median prices
   - clipping the first 24 hours to `market_start`
   - a brief manipulated price move that should not trip resolution because the 24-hour median stays above threshold
   - a market that briefly breaches threshold in an earlier hourly snapshot and must still resolve `YES` even if a later snapshot recovers
   - a sustained collapse that does trip resolution

## Validation

- Run `pytest` with updated market-settlement tests.
- Run a local snapshot/resolution flow against deterministic fake historical trade data.
- Verify public token/card serialization still exposes the same fields, now populated from median-based snapshots.

## Trade-Offs

- This is materially more robust than the current hourly spot snapshot, but it is less responsive to very fast crashes because it intentionally smooths over short-lived price moves.
- It introduces a new historical data dependency for settlement. That is acceptable here because the existing DexScreener client cannot provide the historical window needed for a real 24-hour median.

## Unresolved Questions

- None for planning. During implementation, the only practical decision to finalize is whether token-wide Bitquery trade queries are clean enough on their own or whether the Bags pools endpoint should be used as a venue allowlist.
