from pathlib import Path

import pytest

from nukefm.bags import BagsToken
from nukefm.catalog import Catalog


@pytest.fixture
def catalog(tmp_path: Path) -> Catalog:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    catalog.initialize()
    return catalog


def test_ingest_creates_current_market(catalog: Catalog) -> None:
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
    assert token["current_market"]["state"] == "awaiting_liquidity"
    assert token["current_market"]["sequence_number"] == 1
    assert token["current_market"]["question"] == "Will ALPHA nuke by 90 days after this market opens?"


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

    first_market_id = catalog.get_token_detail("Mint222")["current_market"]["id"]
    catalog.resolve_market(first_market_id, "resolved_no", resolved_at="2026-04-15T11:00:00+00:00")

    token = catalog.get_token_detail("Mint222")
    assert token is not None
    assert token["current_market"]["sequence_number"] == 2
    assert token["current_market"]["state"] == "awaiting_liquidity"
    assert token["past_markets"][0]["state"] == "resolved_no"
