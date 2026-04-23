from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BagsToken:
    mint: str
    name: str
    symbol: str
    image_url: str | None
    launched_at: str | None
    creator: str | None
