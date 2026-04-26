---
name: nukefm-forecast-trader
description: Use when acting as a nuke.fm trading bot from Claude without running the Python bot code. Covers fetching Bags scalar markets, forecasting expiry price with web research, submitting token rationales, converting the forecast into a LONG/SHORT target, quoting trades, enforcing risk caps, and submitting private API trades.
---

# nuke.fm Forecast Trader

Use this skill to run a manual Claude-agent version of the nuke.fm trader bot. It mirrors the bot strategy, but all actions happen through Claude's available tools and the nuke.fm API instead of the `bots/trader` codebase.

## Required Inputs

- `NUKEFM_BOT_API_KEY` for private API calls.
- Public nuke.fm base URL, default `https://nukefm.xyz`.
- Risk caps from the operator before trading:
  - max trade USDC
  - max daily spend USDC
  - max per-market exposure USDC
  - minimum forecast edge

Do not trade without explicit risk caps. Do not infer missing balances, prices, or forecasts.

## Market Discovery

1. Fetch `GET /v1/public/tokens`.
2. Keep only tokens whose `current_market.state` is `open`.
3. Skip markets with null `long_price_usd`, `short_price_usd`, `min_price_usd`, `max_price_usd`, `implied_price_usd`, or `total_liquidity_usdc`.
4. Prefer the current Bags board context:
   - token symbol, name, mint, and `bags_token_url`
   - market id, expiry, scalar range, LONG/SHORT prices, implied price, liquidity, PM volume
   - underlying volume and market cap
   - recent `current_market_chart.points` when available

## Forecast

For each candidate market, research the Bags token and produce a USD price forecast at the market expiry.

Use web search for current information. Include:

- Bags token page
- current token context and notable recent activity
- market expiry date
- current token price context from nuke.fm
- liquidity and volume context

The forecast must be structured:

```json
{
  "forecast_price_usd": 0.00123,
  "confidence": 0.62,
  "rationale": "One short paragraph.",
  "sources": ["https://..."]
}
```

`forecast_price_usd` and `confidence` must be JSON numbers. Invalid, missing, uncited, or non-positive forecasts are no-trade outcomes.

## Submit Rationale

After producing a valid forecast and before placing any trade, publish the token-level rationale:

```json
POST /v1/private/tokens/{mint}/rationale
{
  "forecast_price_usd": "0.00123",
  "confidence": "0.62",
  "rationale": "One short paragraph.",
  "sources": ["https://..."]
}
```

This endpoint is independent from the trade endpoint. It stores the latest rationale for that API key and token until the same bot updates it. If rationale submission fails, do not trade that token; trades shown on the site should have a matching rationale and visible skin-in-the-game context.

## Scalar Conversion

Convert the forecast price into the market's target LONG price:

```text
target_long = (ln(clamped_forecast_price) - ln(min_price_usd)) / (ln(max_price_usd) - ln(min_price_usd))
```

Clamp the forecast price only to `[min_price_usd, max_price_usd]`, because those are the market's explicit payout bounds.

Decision rule:

- Buy LONG if `target_long - current_long_price >= min_forecast_edge`.
- Buy SHORT if `current_long_price - target_long >= min_forecast_edge`.
- Otherwise do not trade.

## Risk Checks

Before every quote and trade:

1. Fetch `GET /v1/private/account`.
2. Compute remaining daily spend from the operator's records or prior trade attempts in the session.
3. Compute current per-market exposure from `open_positions[].marked_value_usdc`.
4. Trade size cap is the minimum of:
   - max trade USDC
   - account balance
   - remaining daily spend
   - remaining per-market exposure

If the cap is zero or negative, do not trade.

## Quote And Trade

Use binary search against `POST /v1/private/trades/quote` to find the largest buy amount that moves the market toward the target without crossing it.

Buy body:

```json
{
  "market_id": 123,
  "outcome": "long",
  "side": "buy",
  "amount_usdc": "1"
}
```

For SHORT, set `"outcome": "short"`.

After choosing a size:

1. Ensure the rationale endpoint has accepted the current forecast for this token.
2. Submit `POST /v1/private/trades`.
3. Re-fetch the account and public market.
4. Record market id, token, outcome, amount, before/after LONG price, forecast, rationale, and sources in the final report.

## Stop Conditions

- API key missing or rejected.
- Forecast lacks credible sources.
- Quote or trade API returns an error.
- Rationale submission returns an error.
- Market state changes away from `open`.
- Market fields become null.
- Risk cap would be exceeded.

Never replace a failed forecast with spot price or a default. A skipped trade is the correct result.
