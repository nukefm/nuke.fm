from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import requests
from loguru import logger


@dataclass(frozen=True)
class BagsToken:
    mint: str
    name: str
    symbol: str
    image_url: str | None
    launched_at: str | None
    creator: str | None


class BagsTokenMetadataClient(Protocol):
    def get_token_metadata(self, token_mint: str) -> BagsToken | None:
        ...


class BagsClient:
    def __init__(
        self,
        *,
        base_url: str,
        metadata_client: BagsTokenMetadataClient,
        api_key: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._metadata_client = metadata_client
        self._session = requests.Session()
        if api_key:
            self._session.headers.update({"x-api-key": api_key})

    def list_tokens(self, *, limit: int = 100) -> list[BagsToken]:
        tokens: list[BagsToken] = []
        for token_mint in self.list_token_mints()[:limit]:
            token = self._metadata_client.get_token_metadata(token_mint)
            if token is None:
                logger.warning("Skipping Bags token {} because Jupiter returned no exact mint match.", token_mint)
                continue

            tokens.append(token)
        return tokens

    def list_token_mints(self) -> list[str]:
        response = self._session.get(
            f"{self._base_url}/solana/bags/pools",
            params={"onlyMigrated": "false"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(f"Bags pools request failed: {payload.get('error')}")

        token_mints: list[str] = []
        seen_mints: set[str] = set()
        for row in payload.get("response") or []:
            token_mint = row.get("tokenMint")
            if not token_mint:
                raise ValueError(f"Bags pools row is missing tokenMint: {row}")
            if token_mint in seen_mints:
                continue

            seen_mints.add(token_mint)
            token_mints.append(token_mint)

        if not token_mints:
            raise ValueError("Bags pools response did not include any token mints.")
        return token_mints
