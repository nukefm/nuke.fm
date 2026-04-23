from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from time import monotonic, sleep

import requests

from .bags import BagsToken
from .dexscreener import DexScreenerPair


@dataclass(frozen=True)
class _JupiterGem:
    token: BagsToken
    pair: DexScreenerPair


class JupiterTokensClient:
    def __init__(self, *, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._last_request_started_at = 0.0
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


class JupiterGemsClient:
    def __init__(self, *, base_url: str, min_market_cap_usd: Decimal = Decimal("10000")) -> None:
        self._base_url = base_url.rstrip("/")
        self._min_market_cap_usd = min_market_cap_usd
        self._session = requests.Session()
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
        self._gems_by_mint: dict[str, _JupiterGem] | None = None

    def list_tokens(self, *, limit: int = 100) -> list[BagsToken]:
        gems = sorted(
            self._load_gems_by_mint().values(),
            key=lambda gem: gem.pair.market_cap_usd or Decimal("0"),
            reverse=True,
        )
        return [gem.token for gem in gems[:limit]]

    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        gem = self._load_gems_by_mint().get(token_mint)
        return [] if gem is None else [gem.pair]

    def _load_gems_by_mint(self) -> dict[str, _JupiterGem]:
        if self._gems_by_mint is None:
            response = self._session.post(
                f"{self._base_url}/pools/gems",
                json={
                    bucket: {
                        "launchpads": ["bags.fun"],
                        "minMcap": float(self._min_market_cap_usd),
                    }
                    for bucket in ("recent", "aboutToGraduate", "graduated")
                },
                timeout=30,
            )
            response.raise_for_status()
            self._gems_by_mint = self._parse_gems_payload(response.json())
        return self._gems_by_mint

    def _parse_gems_payload(self, payload: dict) -> dict[str, _JupiterGem]:
        gems_by_mint: dict[str, _JupiterGem] = {}
        for bucket in ("recent", "aboutToGraduate", "graduated"):
            for pool in (payload.get(bucket) or {}).get("pools", []):
                gem = self._parse_gem_pool(pool)
                if gem is None:
                    continue

                previous_gem = gems_by_mint.get(gem.token.mint)
                if previous_gem is None or (gem.pair.market_cap_usd or Decimal("0")) > (
                    previous_gem.pair.market_cap_usd or Decimal("0")
                ):
                    gems_by_mint[gem.token.mint] = gem
        return gems_by_mint

    def _parse_gem_pool(self, pool: dict) -> _JupiterGem | None:
        base_asset = pool.get("baseAsset") or {}
        mint = base_asset.get("id")
        market_cap_usd = self._decimal_from_value(base_asset.get("mcap"))
        if mint is None or market_cap_usd is None or market_cap_usd < self._min_market_cap_usd:
            return None

        created_at = (
            (base_asset.get("firstPool") or {}).get("createdAt")
            or base_asset.get("createdAt")
            or pool.get("createdAt")
        )
        liquidity = base_asset.get("liquidity") if base_asset.get("liquidity") is not None else pool.get("liquidity")
        token = BagsToken(
            mint=mint,
            name=base_asset.get("name") or mint,
            symbol=base_asset.get("symbol") or mint[:8],
            image_url=base_asset.get("icon"),
            launched_at=created_at,
            creator=base_asset.get("dev"),
        )
        pair = DexScreenerPair(
            pair_address=pool.get("id") or mint,
            dex_id=pool.get("dex") or pool.get("type"),
            price_usd=self._decimal_from_value(base_asset.get("usdPrice")),
            liquidity_usd=self._decimal_from_value(liquidity),
            volume_h24_usd=self._decimal_from_value(pool.get("volume24h")),
            market_cap_usd=market_cap_usd,
        )
        return _JupiterGem(token=token, pair=pair)

    @staticmethod
    def _decimal_from_value(value: object) -> Decimal | None:
        return None if value is None else Decimal(str(value))
