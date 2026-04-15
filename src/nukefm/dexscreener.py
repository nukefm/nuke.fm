from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import requests


@dataclass(frozen=True)
class DexScreenerPair:
    pair_address: str
    dex_id: str | None
    price_usd: Decimal | None
    liquidity_usd: Decimal
    volume_h24_usd: Decimal | None
    market_cap_usd: Decimal | None


class DexScreenerPairClient(Protocol):
    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        ...


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
            liquidity = row.get("liquidity") or {}
            liquidity_usd = liquidity.get("usd")
            pair_address = row.get("pairAddress")
            if liquidity_usd in (None, 0) or pair_address is None:
                continue
            volume = row.get("volume") or {}
            market_cap_usd = row.get("marketCap")
            price_usd = row.get("priceUsd")
            valid_pairs.append(
                DexScreenerPair(
                    pair_address=pair_address,
                    dex_id=row.get("dexId"),
                    price_usd=None if price_usd is None else Decimal(str(price_usd)),
                    liquidity_usd=Decimal(str(liquidity_usd)),
                    volume_h24_usd=None if volume.get("h24") is None else Decimal(str(volume["h24"])),
                    market_cap_usd=None if market_cap_usd is None else Decimal(str(market_cap_usd)),
                )
            )
        return valid_pairs
