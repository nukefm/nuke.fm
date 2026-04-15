import sys

from nukefm import __main__


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
            resolution_threshold_fraction,
            rollover_lower_bound_fraction,
            rollover_upper_bound_fraction,
        ) -> None:
            captured["market_store_init"] = {
                "database_path": database_path,
                "market_duration_days": market_duration_days,
                "resolution_threshold_fraction": resolution_threshold_fraction,
                "rollover_lower_bound_fraction": rollover_lower_bound_fraction,
                "rollover_upper_bound_fraction": rollover_upper_bound_fraction,
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
                "market_resolution_threshold_fraction": "0.10",
                "market_rollover_lower_bound_fraction": "0.25",
                "market_rollover_upper_bound_fraction": "4.0",
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
