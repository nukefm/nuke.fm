from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class DexScreenerPair:
    pair_address: str
    dex_id: str | None
    price_usd: Decimal | None
    liquidity_usd: Decimal | None
    volume_h24_usd: Decimal | None
    market_cap_usd: Decimal | None
    token_supply: Decimal | None = None
    market_cap_kind: str | None = None


class DexScreenerPairClient(Protocol):
    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        ...
