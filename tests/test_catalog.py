from pathlib import Path
from decimal import Decimal

import pytest

from nukefm.bags import BagsToken
from nukefm.catalog import Catalog
from nukefm.dexscreener import DexScreenerPair
from nukefm.markets import MarketStore


@pytest.fixture
def catalog(tmp_path: Path) -> Catalog:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    catalog.initialize()
    return catalog


class FakeDexScreenerClient:
    def __init__(self, pairs_by_mint: dict[str, list[DexScreenerPair]]) -> None:
        self._pairs_by_mint = pairs_by_mint

    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        return self._pairs_by_mint[token_mint]


def test_ingest_needs_market_lifecycle_to_create_visible_market(catalog: Catalog) -> None:
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="Mint111",
                name="Alpha",
                symbol="ALPHA",
                image_url=None,
                launched_at="2026-04-15T10:00:00+00:00",
                creator="Creator111",
            )
        ]
    )

    token = catalog.get_token_detail("Mint111")
    assert token is not None
    assert token["current_market"] is None

    market_store = MarketStore(catalog._database_path)
    market_store.initialize()
    market_store.capture_token_metrics(
        FakeDexScreenerClient(
            {
                "Mint111": [
                    DexScreenerPair(
                        pair_address="alpha-pair",
                        dex_id="raydium",
                        price_usd=Decimal("1.5"),
                        liquidity_usd=Decimal("100"),
                        volume_h24_usd=Decimal("10"),
                        market_cap_usd=Decimal("1000"),
                    )
                ]
            }
        ),
        captured_at="2026-04-15T10:05:00+00:00",
    )

    token = catalog.get_token_detail("Mint111")
    assert token is not None
    assert token["current_market"]["state"] == "awaiting_liquidity"
    assert token["current_market"]["sequence_number"] == 1
    assert token["current_market"]["starting_price_usd"] == "1.5"
    assert token["current_market"]["threshold_price_usd"] == "0.15"


def test_resolving_market_rolls_the_series_forward(catalog: Catalog) -> None:
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="Mint222",
                name="Beta",
                symbol="BETA",
                image_url=None,
                launched_at=None,
                creator=None,
            )
        ]
    )

    market_store = MarketStore(catalog._database_path)
    market_store.initialize()
    market_store.capture_token_metrics(
        FakeDexScreenerClient(
            {
                "Mint222": [
                    DexScreenerPair(
                        pair_address="beta-pair",
                        dex_id="raydium",
                        price_usd=Decimal("2"),
                        liquidity_usd=Decimal("100"),
                        volume_h24_usd=Decimal("10"),
                        market_cap_usd=Decimal("2000"),
                    )
                ]
            }
        ),
        captured_at="2026-04-15T10:00:00+00:00",
    )

    first_market_id = catalog.get_token_detail("Mint222")["current_market"]["id"]
    catalog.resolve_market(first_market_id, "resolved_no", resolved_at="2026-04-15T11:00:00+00:00")

    token = catalog.get_token_detail("Mint222")
    assert token is not None
    assert token["current_market"] is None
    assert token["past_markets"][0]["state"] == "resolved_no"
