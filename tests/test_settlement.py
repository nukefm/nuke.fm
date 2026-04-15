from decimal import Decimal

from nukefm.settlement import JupiterChartsSettlementPriceClient


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_jupiter_charts_returns_median_close_within_window() -> None:
    client = JupiterChartsSettlementPriceClient()
    captured: dict[str, object] = {}

    def fake_get(url: str, *, params: dict, timeout: int):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "candles": [
                    {"time": 1_744_678_800, "close": 5},
                    {"time": 1_744_679_700, "close": 100},
                    {"time": 1_744_680_600, "close": 3},
                    {"time": 1_744_681_500, "close": 9},
                    {"time": 1_744_682_400, "close": 11},
                ]
            }
        )

    client._session.get = fake_get  # type: ignore[method-assign]

    median_price = client.get_rolling_median_price(
        "MintA",
        start_at="2025-04-15T01:00:00+00:00",
        end_at="2025-04-15T02:00:00+00:00",
    )

    assert median_price == Decimal("9")
    assert captured["url"].endswith("/MintA")
    assert captured["params"] == {
        "interval": "15_MINUTE",
        "to": 1_744_682_400_000,
        "candles": 4,
        "type": "price",
        "quote": "usd",
    }
    assert captured["timeout"] == 30


def test_jupiter_charts_requires_candles_in_range() -> None:
    client = JupiterChartsSettlementPriceClient()

    def fake_get(url: str, *, params: dict, timeout: int):
        return FakeResponse({"candles": [{"time": 1_744_700_000, "close": 5}]})

    client._session.get = fake_get  # type: ignore[method-assign]

    try:
        client.get_rolling_median_price(
            "MintA",
            start_at="2025-04-15T01:00:00+00:00",
            end_at="2025-04-15T02:00:00+00:00",
        )
    except ValueError as error:
        assert "No settlement prices returned" in str(error)
    else:
        raise AssertionError("Expected missing in-range candles to fail.")
