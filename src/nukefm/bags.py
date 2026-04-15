from __future__ import annotations

from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class BagsToken:
    mint: str
    name: str
    symbol: str
    image_url: str | None
    launched_at: str | None
    creator: str | None


class BagsClient:
    def __init__(self, *, base_url: str, feed_path: str, api_key: str) -> None:
        self._session = requests.Session()
        self._session.headers.update({"x-api-key": api_key})
        self._base_url = base_url
        self._feed_path = feed_path

    def list_tokens(self, *, limit: int = 100) -> list[BagsToken]:
        response = self._session.get(f"{self._base_url}{self._feed_path}", timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("success") is not True:
            raise RuntimeError(payload.get("error", "Bags feed request failed"))

        items = payload.get("response", [])
        return [self._parse_token(item) for item in items[:limit]]

    @staticmethod
    def _parse_token(item: dict) -> BagsToken:
        mint = item.get("tokenMint")
        if not mint:
            raise ValueError(f"Missing token mint in Bags feed item: {item}")

        return BagsToken(
            mint=mint,
            name=item.get("name") or mint,
            symbol=item.get("symbol") or mint[:8],
            image_url=item.get("image"),
            launched_at=item.get("createdAt"),
            creator=None,
        )
