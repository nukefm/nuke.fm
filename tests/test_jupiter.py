from decimal import Decimal

from nukefm.jupiter import JupiterGemsClient


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.posts: list[dict] = []

    def post(self, url: str, *, json: dict, timeout: int) -> FakeResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(self._payload)


def test_jupiter_gems_client_parses_and_dedupes_bags_market_metrics() -> None:
    payload = {
        "recent": {
            "pools": [
                {
                    "id": "low-pool",
                    "dex": "bags.fun",
                    "createdAt": "2026-04-23T01:00:00Z",
                    "volume24h": 50,
                    "baseAsset": {
                        "id": "MintLow",
                        "name": "Low",
                        "symbol": "LOW",
                        "icon": "https://example.test/low.png",
                        "dev": "CreatorLow",
                        "circSupply": 1000000000,
                        "usdPrice": 0.000012,
                        "liquidity": 5,
                    },
                },
                {
                    "id": "ignored-pool",
                    "baseAsset": {
                        "id": "MintTiny",
                        "name": "Tiny",
                        "symbol": "TINY",
                        "circSupply": 1000000000,
                        "usdPrice": 0.000009999,
                    },
                },
            ]
        },
        "aboutToGraduate": {
            "pools": [
                {
                    "id": "high-pool",
                    "type": "bags.fun",
                    "volume24h": 1250.5,
                    "baseAsset": {
                        "id": "MintHigh",
                        "name": "High",
                        "symbol": "HIGH",
                        "firstPool": {"createdAt": "2026-04-22T01:00:00Z"},
                        "circSupply": 1000000000,
                        "usdPrice": "0.00005",
                        "liquidity": 250,
                    },
                }
            ]
        },
        "graduated": {
            "pools": [
                {
                    "id": "older-high-pool",
                    "type": "meteora-damm-v2",
                    "volume24h": 900,
                    "baseAsset": {
                        "id": "MintHigh",
                        "name": "High",
                        "symbol": "HIGH",
                        "circSupply": 1000000000,
                        "usdPrice": "0.000045",
                        "liquidity": 200,
                    },
                }
            ]
        },
    }
    client = JupiterGemsClient(base_url="https://datapi.test/v1", min_market_cap_usd=Decimal("10000"))
    client._session = FakeSession(payload)

    assert [token.mint for token in client.list_tokens(limit=10)] == ["MintHigh", "MintLow"]

    high_pair = client.list_token_pairs("MintHigh")[0]
    assert high_pair.pair_address == "high-pool"
    assert high_pair.market_cap_usd == Decimal("50000")
    assert high_pair.token_supply == Decimal("1000000000")
    assert high_pair.market_cap_kind == "circulating"
    assert high_pair.volume_h24_usd == Decimal("1250.5")
    assert high_pair.price_usd == Decimal("0.00005")
    assert high_pair.liquidity_usd == Decimal("250")
    assert client.list_token_pairs("MintTiny") == []
    assert len(client._session.posts) == 1
    assert client._session.posts[0]["json"]["recent"] == {
        "launchpads": ["bags.fun"],
        "minMcap": 10000.0,
    }
