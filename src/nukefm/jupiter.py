from __future__ import annotations

from decimal import Decimal
from time import monotonic, sleep

import requests

from .bags import BagsToken
from .dexscreener import DexScreenerPair


class JupiterTokensClient:
    def __init__(self, *, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._last_request_started_at = 0.0
        self._exact_token_rows_by_mint: dict[str, dict | None] = {}
        self._session.headers.update(
            {
                "accept": "application/json",
                "origin": "https://jup.ag",
                "referer": "https://jup.ag/",
                "user-agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
                ),
            }
        )

    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        row = self._search_exact_token(token_mint)
        if row is None:
            return []

        stats_24h = row.get("stats24h") or {}
        buy_volume = stats_24h.get("buyVolume")
        sell_volume = stats_24h.get("sellVolume")
        total_volume = None
        if buy_volume is not None or sell_volume is not None:
            total_volume = Decimal(str(buy_volume or 0)) + Decimal(str(sell_volume or 0))

        price_usd = row.get("usdPrice")
        token_supply = self._decimal_from_value(row.get("circSupply"))
        market_cap_kind = "circulating"
        if token_supply is None:
            token_supply = self._decimal_from_value(row.get("totalSupply"))
            market_cap_kind = "fully_diluted" if token_supply is not None else None

        pool = row.get("graduatedPool") or (row.get("firstPool") or {}).get("id") or token_mint
        launchpad = row.get("launchpad")
        liquidity = row.get("liquidity")

        return [
            DexScreenerPair(
                pair_address=pool,
                dex_id=None if launchpad is None else str(launchpad),
                price_usd=None if price_usd is None else Decimal(str(price_usd)),
                liquidity_usd=Decimal(str(liquidity or 0)),
                volume_h24_usd=total_volume,
                market_cap_usd=None,
                token_supply=token_supply,
                market_cap_kind=market_cap_kind,
            )
        ]

    def get_token_metadata(self, token_mint: str) -> BagsToken | None:
        row = self._search_exact_token(token_mint)
        if row is None:
            return None

        return BagsToken(
            mint=token_mint,
            name=row.get("name") or token_mint,
            symbol=row.get("symbol") or token_mint[:8],
            image_url=row.get("icon"),
            launched_at=(row.get("firstPool") or {}).get("createdAt") or row.get("createdAt"),
            creator=row.get("dev"),
        )

    def _search_exact_token(self, token_mint: str) -> dict | None:
        if token_mint in self._exact_token_rows_by_mint:
            return self._exact_token_rows_by_mint[token_mint]

        response = None
        for attempt in range(6):
            elapsed_seconds = monotonic() - self._last_request_started_at
            if elapsed_seconds < 1.1:
                sleep(1.1 - elapsed_seconds)
            self._last_request_started_at = monotonic()
            response = self._session.get(
                f"{self._base_url}/search",
                params={"query": token_mint, "limit": 3},
                timeout=15,
            )
            if response.status_code != 429:
                break

            retry_after = response.headers.get("retry-after")
            backoff_seconds = 5 if retry_after is None else max(int(retry_after), 1)
            sleep(backoff_seconds * (attempt + 1))

        if response is None:
            raise RuntimeError("Jupiter token metrics request did not execute.")

        response.raise_for_status()

        for row in response.json():
            if row.get("id") != token_mint:
                continue
            self._exact_token_rows_by_mint[token_mint] = row
            return row

        self._exact_token_rows_by_mint[token_mint] = None
        return None

    @staticmethod
    def _decimal_from_value(value: object) -> Decimal | None:
        return None if value is None else Decimal(str(value))
