from decimal import Decimal

from nukefm.bags import BagsClient, BagsToken
from nukefm.jupiter import JupiterTokensClient


class FakeResponse:
    status_code = 200

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


class FakeSession:
    def __init__(self, payloads_by_query: dict[str, object]) -> None:
        self._payloads_by_query = payloads_by_query
        self.gets: list[dict] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, *, params: dict, timeout: int) -> FakeResponse:
        self.gets.append({"url": url, "params": params, "timeout": timeout})
        query = params.get("query")
        payload = self._payloads_by_query[query] if query is not None else self._payloads_by_query["pools"]
        return FakeResponse(payload)


def test_jupiter_tokens_client_matches_exact_mint_for_metadata_and_metrics() -> None:
    payloads = {
        "MintExact": [
            {
                "id": "SymbolOnlyCollision",
                "name": "Wrong",
                "symbol": "EXACT",
                "usdPrice": "9",
            },
            {
                "id": "MintExact",
                "name": "Exact Token",
                "symbol": "EXACT",
                "icon": "https://example.test/exact.png",
                "dev": "Creator",
                "circSupply": 1000000000,
                "usdPrice": "0.00005",
                "liquidity": 250,
                "stats24h": {"buyVolume": "700", "sellVolume": "300"},
                "launchpad": "bags.fun",
                "graduatedPool": "exact-pool",
                "firstPool": {"createdAt": "2026-04-22T01:00:00Z"},
            },
        ],
    }
    client = JupiterTokensClient(base_url="https://tokens.test/v2")
    client._session = FakeSession(payloads)

    token = client.get_token_metadata("MintExact")
    assert token is not None
    assert token.mint == "MintExact"
    assert token.name == "Exact Token"
    assert token.symbol == "EXACT"
    assert token.image_url == "https://example.test/exact.png"
    assert token.launched_at == "2026-04-22T01:00:00Z"
    assert token.creator == "Creator"

    pair = client.list_token_pairs("MintExact")[0]
    assert pair.pair_address == "exact-pool"
    assert pair.dex_id == "bags.fun"
    assert pair.price_usd == Decimal("0.00005")
    assert pair.liquidity_usd == Decimal("250")
    assert pair.volume_h24_usd == Decimal("1000")
    assert pair.market_cap_usd is None
    assert pair.token_supply == Decimal("1000000000")
    assert pair.market_cap_kind == "circulating"
    assert all(call["params"]["query"] == "MintExact" for call in client._session.gets)


def test_bags_client_uses_bags_mints_then_hydrates_from_jupiter() -> None:
    bags_session = FakeSession(
        {
            "pools": {
                "success": True,
                "response": [
                    {"tokenMint": "MintA", "dbcPoolKey": "PoolA"},
                    {"tokenMint": "MintA", "dbcPoolKey": "DuplicatePoolA"},
                    {"tokenMint": "MintB", "dbcPoolKey": "PoolB"},
                ],
            }
        }
    )

    class FakeMetadataClient:
        def __init__(self) -> None:
            self.mints: list[str] = []

        def get_token_metadata(self, token_mint: str):
            self.mints.append(token_mint)
            if token_mint == "MintB":
                return None
            return BagsToken(
                mint=token_mint,
                name="Alpha",
                symbol="ALPHA",
                image_url=None,
                launched_at=None,
                creator=None,
            )

    metadata_client = FakeMetadataClient()
    client = BagsClient(
        base_url="https://bags.test/api/v1",
        api_key="test-key",
        metadata_client=metadata_client,
    )
    client._session = bags_session

    tokens = client.list_tokens(limit=10)

    assert [token.mint for token in tokens] == ["MintA"]
    assert metadata_client.mints == ["MintA", "MintB"]
    assert bags_session.gets == [
        {
            "url": "https://bags.test/api/v1/solana/bags/pools",
            "params": {"onlyMigrated": "false"},
            "timeout": 30,
        }
    ]
