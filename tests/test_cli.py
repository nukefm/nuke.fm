import sys

from nukefm import __main__
from nukefm.bags import BagsToken


def test_serve_enables_local_proxy_headers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_load_settings():
        return type("Settings", (), {"log_path": "logs/test.log"})()

    def fake_configure_logging(log_path) -> None:
        captured["log_path"] = log_path

    def fake_run(app, **kwargs) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(__main__, "load_settings", fake_load_settings)
    monkeypatch.setattr(__main__, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(__main__.uvicorn, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["nukefm", "serve", "--host", "127.0.0.1", "--port", "8000"])

    __main__.main()

    assert captured["log_path"] == "logs/test.log"
    assert captured["app"] == "nukefm.app:create_app"
    assert captured["kwargs"] == {
        "factory": True,
        "host": "127.0.0.1",
        "port": 8000,
        "proxy_headers": True,
        "forwarded_allow_ips": "127.0.0.1",
    }


def test_snapshot_market_charts_runs_chart_capture(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCatalog:
        def __init__(self, database_path) -> None:
            captured["catalog_database_path"] = database_path

        def initialize(self) -> None:
            captured["catalog_initialized"] = True

    class FakeAccountStore:
        def __init__(self, database_path) -> None:
            captured["account_database_path"] = database_path

        def initialize(self) -> None:
            captured["account_initialized"] = True

    class FakeMarketStore:
        def __init__(
            self,
            database_path,
            *,
            market_duration_days,
            market_price_range_multiple,
            market_rollover_boundary_rate,
            market_rollover_liquidity_transfer_fraction,
        ) -> None:
            captured["market_store_init"] = {
                "database_path": database_path,
                "market_duration_days": market_duration_days,
                "market_price_range_multiple": market_price_range_multiple,
                "market_rollover_boundary_rate": market_rollover_boundary_rate,
                "market_rollover_liquidity_transfer_fraction": market_rollover_liquidity_transfer_fraction,
            }

        def initialize(self) -> None:
            captured["market_initialized"] = True

        def capture_market_chart_snapshots(self, client) -> list[dict]:
            captured["chart_client"] = client
            return [{"market_id": 7}]

    def fake_load_settings():
        return type(
            "Settings",
            (),
            {
                "log_path": "logs/test.log",
                "database_path": "data/test.sqlite3",
                "market_duration_days": 90,
                "market_price_range_multiple": "10",
                "market_rollover_boundary_rate": "0.85",
                "market_rollover_liquidity_transfer_fraction": "0.80",
                "jupiter_tokens_base_url": "https://jup.test/tokens",
            },
        )()

    def fake_configure_logging(log_path) -> None:
        captured["log_path"] = log_path

    def fake_jupiter_tokens_client(*, base_url):
        captured["jupiter_base_url"] = base_url
        return "fake-jupiter-client"

    monkeypatch.setattr(__main__, "load_settings", fake_load_settings)
    monkeypatch.setattr(__main__, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(__main__, "Catalog", FakeCatalog)
    monkeypatch.setattr(__main__, "AccountStore", FakeAccountStore)
    monkeypatch.setattr(__main__, "MarketStore", FakeMarketStore)
    monkeypatch.setattr(__main__, "JupiterTokensClient", fake_jupiter_tokens_client)
    monkeypatch.setattr(sys, "argv", ["nukefm", "snapshot-market-charts"])

    __main__.main()

    assert captured["log_path"] == "logs/test.log"
    assert captured["catalog_initialized"] is True
    assert captured["account_initialized"] is True
    assert captured["market_initialized"] is True
    assert captured["jupiter_base_url"] == "https://jup.test/tokens"
    assert captured["chart_client"] == "fake-jupiter-client"


def test_sync_token_metrics_uses_jupiter_tokens_client(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCatalog:
        def __init__(self, database_path) -> None:
            captured["catalog_database_path"] = database_path

        def initialize(self) -> None:
            captured["catalog_initialized"] = True

    class FakeAccountStore:
        def __init__(self, database_path) -> None:
            captured["account_database_path"] = database_path

        def initialize(self) -> None:
            captured["account_initialized"] = True

    class FakeMarketStore:
        def __init__(
            self,
            database_path,
            *,
            market_duration_days,
            market_price_range_multiple,
            market_rollover_boundary_rate,
            market_rollover_liquidity_transfer_fraction,
        ) -> None:
            captured["market_database_path"] = database_path

        def initialize(self) -> None:
            captured["market_initialized"] = True

        def capture_token_metrics(self, client, **kwargs) -> list[dict]:
            captured["metrics_client"] = client
            captured["metrics_kwargs"] = kwargs
            return [{"mint": "MintTop"}]

    def fake_load_settings():
        return type(
            "Settings",
            (),
            {
                "log_path": "logs/test.log",
                "database_path": "data/test.sqlite3",
                "market_duration_days": 90,
                "market_price_range_multiple": "10",
                "market_rollover_boundary_rate": "0.85",
                "market_rollover_liquidity_transfer_fraction": "0.80",
                "jupiter_tokens_base_url": "https://tokens.test/v2",
            },
        )()

    def fake_configure_logging(log_path) -> None:
        captured["log_path"] = log_path

    def fake_jupiter_tokens_client(*, base_url):
        captured["tokens_base_url"] = base_url
        return "fake-tokens-client"

    monkeypatch.setattr(__main__, "load_settings", fake_load_settings)
    monkeypatch.setattr(__main__, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(__main__, "Catalog", FakeCatalog)
    monkeypatch.setattr(__main__, "AccountStore", FakeAccountStore)
    monkeypatch.setattr(__main__, "MarketStore", FakeMarketStore)
    monkeypatch.setattr(__main__, "JupiterTokensClient", fake_jupiter_tokens_client)
    monkeypatch.setattr(sys, "argv", ["nukefm", "sync-token-metrics"])

    __main__.main()

    assert captured["log_path"] == "logs/test.log"
    assert captured["catalog_initialized"] is True
    assert captured["account_initialized"] is True
    assert captured["market_initialized"] is True
    assert captured["tokens_base_url"] == "https://tokens.test/v2"
    assert captured["metrics_client"] == "fake-tokens-client"
    assert captured["metrics_kwargs"] == {}


def test_seed_weekly_liquidity_defaults_to_top_four_volume_markets(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCatalog:
        def __init__(self, database_path) -> None:
            captured["catalog_database_path"] = database_path

        def initialize(self) -> None:
            captured["catalog_initialized"] = True

    class FakeAccountStore:
        def __init__(self, database_path) -> None:
            captured["account_database_path"] = database_path

        def initialize(self) -> None:
            captured["account_initialized"] = True

    class FakeMarketStore:
        def __init__(
            self,
            database_path,
            *,
            market_duration_days,
            market_price_range_multiple,
            market_rollover_boundary_rate,
            market_rollover_liquidity_transfer_fraction,
        ) -> None:
            captured["market_database_path"] = database_path

        def initialize(self) -> None:
            captured["market_initialized"] = True

        def seed_top_markets_by_underlying_volume(self, *, amount_atomic: int, limit: int) -> list[dict]:
            captured["seed_amount_atomic"] = amount_atomic
            captured["seed_limit"] = limit
            return [{"market_id": 1}]

        def get_outstanding_treasury_debt_usdc(self) -> str:
            return "1"

    def fake_load_settings():
        return type(
            "Settings",
            (),
            {
                "log_path": "logs/test.log",
                "database_path": "data/test.sqlite3",
                "market_duration_days": 90,
                "market_price_range_multiple": "10",
                "market_rollover_boundary_rate": "0.85",
                "market_rollover_liquidity_transfer_fraction": "0.80",
            },
        )()

    def fake_configure_logging(log_path) -> None:
        captured["log_path"] = log_path

    monkeypatch.setattr(__main__, "load_settings", fake_load_settings)
    monkeypatch.setattr(__main__, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(__main__, "Catalog", FakeCatalog)
    monkeypatch.setattr(__main__, "AccountStore", FakeAccountStore)
    monkeypatch.setattr(__main__, "MarketStore", FakeMarketStore)
    monkeypatch.setattr(sys, "argv", ["nukefm", "seed-weekly-liquidity"])

    __main__.main()

    assert captured["log_path"] == "logs/test.log"
    assert captured["catalog_initialized"] is True
    assert captured["account_initialized"] is True
    assert captured["market_initialized"] is True
    assert captured["seed_amount_atomic"] == 1_000_000
    assert captured["seed_limit"] == 4


def test_ingest_uses_bags_mints_and_jupiter_hydration(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCatalog:
        def __init__(self, database_path) -> None:
            captured["catalog_database_path"] = database_path

        def initialize(self) -> None:
            captured["catalog_initialized"] = True

        def ingest_tokens(self, tokens) -> int:
            captured["ingested_tokens"] = tokens
            return len(tokens)

    class FakeAccountStore:
        def __init__(self, database_path) -> None:
            captured["account_database_path"] = database_path

        def initialize(self) -> None:
            captured["account_initialized"] = True

    class FakeMarketStore:
        def __init__(
            self,
            database_path,
            *,
            market_duration_days,
            market_price_range_multiple,
            market_rollover_boundary_rate,
            market_rollover_liquidity_transfer_fraction,
        ) -> None:
            captured["market_database_path"] = database_path

        def initialize(self) -> None:
            captured["market_initialized"] = True

        def capture_token_metrics(self, client, **kwargs) -> list[dict]:
            captured["metrics_client"] = client
            captured["metrics_kwargs"] = kwargs
            return [{"mint": "MintTop"}]

    def fake_load_settings():
        return type(
            "Settings",
            (),
            {
                "log_path": "logs/test.log",
                "database_path": "data/test.sqlite3",
                "market_duration_days": 90,
                "market_price_range_multiple": "10",
                "market_rollover_boundary_rate": "0.85",
                "market_rollover_liquidity_transfer_fraction": "0.80",
                "bags_api_base_url": "https://bags.test/api/v1",
                "bags_api_key": "bags-key",
                "jupiter_tokens_base_url": "https://tokens.test/v2",
            },
        )()

    def fake_configure_logging(log_path) -> None:
        captured["log_path"] = log_path

    def fake_jupiter_tokens_client(*, base_url):
        captured["tokens_base_url"] = base_url
        return "fake-jupiter-client"

    class FakeBagsClient:
        def __init__(self, *, base_url, api_key, metadata_client) -> None:
            captured["bags_base_url"] = base_url
            captured["bags_api_key"] = api_key
            captured["bags_metadata_client"] = metadata_client

        def list_tokens(self, *, limit: int):
            captured["bags_limit"] = limit
            return [
                BagsToken("MintA", "Token A", "TKNA", None, None, None),
                BagsToken("MintB", "Token B", "TKNB", None, None, None),
            ]

    monkeypatch.setattr(__main__, "load_settings", fake_load_settings)
    monkeypatch.setattr(__main__, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(__main__, "Catalog", FakeCatalog)
    monkeypatch.setattr(__main__, "AccountStore", FakeAccountStore)
    monkeypatch.setattr(__main__, "MarketStore", FakeMarketStore)
    monkeypatch.setattr(__main__, "JupiterTokensClient", fake_jupiter_tokens_client)
    monkeypatch.setattr(__main__, "BagsClient", FakeBagsClient)
    monkeypatch.setattr(sys, "argv", ["nukefm", "ingest", "--limit", "2"])

    __main__.main()

    assert captured["tokens_base_url"] == "https://tokens.test/v2"
    assert captured["bags_base_url"] == "https://bags.test/api/v1"
    assert captured["bags_api_key"] == "bags-key"
    assert captured["bags_metadata_client"] == "fake-jupiter-client"
    assert captured["bags_limit"] == 2
    assert [token.mint for token in captured["ingested_tokens"]] == ["MintA", "MintB"]
    assert captured["metrics_client"] == "fake-jupiter-client"
    assert captured["metrics_kwargs"] == {"token_mints": ["MintA", "MintB"]}
