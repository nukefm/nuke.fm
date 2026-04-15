from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol
import math

import requests


JUPITER_CHARTS_URL = "https://datapi.jup.ag/v2/charts"
FIFTEEN_MINUTE_SECONDS = 15 * 60


class SettlementPriceClient(Protocol):
    def get_rolling_median_price(self, token_mint: str, *, start_at: str, end_at: str) -> Decimal:
        ...


class JupiterChartsSettlementPriceClient(SettlementPriceClient):
    def __init__(self, *, base_url: str = JUPITER_CHARTS_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Origin": "https://jup.ag",
                "Referer": "https://jup.ag/",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
                ),
            }
        )

    def get_rolling_median_price(self, token_mint: str, *, start_at: str, end_at: str) -> Decimal:
        start_time = datetime.fromisoformat(start_at)
        end_time = datetime.fromisoformat(end_at)
        duration_seconds = max((end_time - start_time).total_seconds(), 0)
        candle_count = max(1, math.ceil(duration_seconds / FIFTEEN_MINUTE_SECONDS))
        payload = self._fetch_candles(token_mint, end_time=end_time, candle_count=candle_count)

        candle_prices: list[Decimal] = []
        for candle in payload.get("candles") or []:
            candle_time = datetime.fromtimestamp(candle["time"], tz=end_time.tzinfo)
            if candle_time < start_time or candle_time > end_time:
                continue
            candle_prices.append(Decimal(str(candle["close"])))

        if candle_prices:
            return self._median_decimal(candle_prices)

        last_known_price = self._last_known_price(token_mint, end_time=end_time)
        if last_known_price is not None:
            return last_known_price

        raise ValueError(f"No settlement prices returned for {token_mint} between {start_at} and {end_at}.")

    def _fetch_candles(self, token_mint: str, *, end_time: datetime, candle_count: int) -> dict:
        response = self._session.get(
            f"{self._base_url}/{token_mint}",
            params={
                "interval": "15_MINUTE",
                "to": int(end_time.timestamp() * 1000),
                "candles": candle_count,
                "type": "price",
                "quote": "usd",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _last_known_price(self, token_mint: str, *, end_time: datetime) -> Decimal | None:
        payload = self._fetch_candles(token_mint, end_time=end_time, candle_count=1)
        candles = payload.get("candles") or []
        if not candles:
            return None
        return Decimal(str(candles[-1]["close"]))

    @staticmethod
    def _median_decimal(values: list[Decimal]) -> Decimal:
        ordered_values = sorted(values)
        middle_index = len(ordered_values) // 2
        if len(ordered_values) % 2 == 1:
            return ordered_values[middle_index]
        return (ordered_values[middle_index - 1] + ordered_values[middle_index]) / Decimal("2")
