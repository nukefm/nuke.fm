from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import requests


@dataclass(frozen=True)
class DexScreenerPair:
    pair_address: str
    dex_id: str | None
    price_usd: Decimal
    liquidity_usd: Decimal


class DexScreenerClient:
    def __init__(self, *, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()

    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        response = self._session.get(
            f"{self._base_url}/token-pairs/v1/solana/{token_mint}",
            timeout=15,
        )
        response.raise_for_status()

        valid_pairs: list[DexScreenerPair] = []
        for row in response.json():
            price_usd = row.get("priceUsd")
            liquidity = row.get("liquidity") or {}
            liquidity_usd = liquidity.get("usd")
            pair_address = row.get("pairAddress")
            if price_usd is None or liquidity_usd in (None, 0) or pair_address is None:
                continue
            valid_pairs.append(
                DexScreenerPair(
                    pair_address=pair_address,
                    dex_id=row.get("dexId"),
                    price_usd=Decimal(str(price_usd)),
                    liquidity_usd=Decimal(str(liquidity_usd)),
                )
            )
        return valid_pairs
