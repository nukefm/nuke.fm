from __future__ import annotations

from decimal import Decimal
from typing import Protocol

import requests


BITQUERY_GRAPHQL_URL = "https://streaming.bitquery.io/graphql"


class SettlementPriceClient(Protocol):
    def get_rolling_median_price(self, token_mint: str, *, start_at: str, end_at: str) -> Decimal:
        ...


class BitquerySettlementPriceClient(SettlementPriceClient):
    def __init__(self, *, api_key: str, base_url: str = BITQUERY_GRAPHQL_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
        )

    def get_rolling_median_price(self, token_mint: str, *, start_at: str, end_at: str) -> Decimal:
        response = self._session.post(
            self._base_url,
            json={
                "query": """
                    query RollingMedianPrice($token: String!, $start: DateTime!, $end: DateTime!) {
                      Solana(dataset: combined) {
                        DEXTradeByTokens(
                          limit: { count: 1 }
                          where: {
                            Block: { Time: { since: $start, till: $end } }
                            Transaction: { Result: { Success: true } }
                            Trade: {
                              Currency: { MintAddress: { is: $token } }
                              PriceAsymmetry: { lt: 0.01 }
                            }
                          }
                        ) {
                          median_price: median(of: Trade_PriceInUSD)
                        }
                      }
                    }
                """,
                "variables": {
                    "token": token_mint,
                    "start": start_at,
                    "end": end_at,
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(payload["errors"])

        rows = payload.get("data", {}).get("Solana", {}).get("DEXTradeByTokens") or []
        if not rows:
            raise ValueError(f"No settlement prices returned for {token_mint} between {start_at} and {end_at}.")

        median_price = rows[0].get("median_price")
        if median_price is None:
            raise ValueError(f"No median settlement price returned for {token_mint} between {start_at} and {end_at}.")
        return Decimal(str(median_price))
