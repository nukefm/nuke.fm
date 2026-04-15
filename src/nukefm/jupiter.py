from __future__ import annotations

from decimal import Decimal

import requests

from .dexscreener import DexScreenerPair


class JupiterTokensClient:
    def __init__(self, *, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"accept": "application/json"})

    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        response = self._session.get(
            f"{self._base_url}/search",
            params={"query": token_mint, "limit": 3},
            timeout=15,
        )
        response.raise_for_status()

        for row in response.json():
            if row.get("id") != token_mint:
                continue

            stats_24h = row.get("stats24h") or {}
            buy_volume = stats_24h.get("buyVolume")
            sell_volume = stats_24h.get("sellVolume")
            total_volume = None
            if buy_volume is not None or sell_volume is not None:
                total_volume = Decimal(str(buy_volume or 0)) + Decimal(str(sell_volume or 0))

            price_usd = row.get("usdPrice")
            market_cap_usd = row.get("mcap")
            if market_cap_usd is None:
                market_cap_usd = row.get("fdv")

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
                    market_cap_usd=None if market_cap_usd is None else Decimal(str(market_cap_usd)),
                )
            ]

        return []
