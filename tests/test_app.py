from pathlib import Path
from decimal import Decimal

from fastapi.testclient import TestClient

from nukefm.app import create_app
from nukefm.accounts import AccountStore
from nukefm.bags import BagsToken
from nukefm.catalog import Catalog
from nukefm.config import Settings
from nukefm.database import connect_database
from nukefm.dexscreener import DexScreenerPair
from nukefm.markets import MarketStore
from nukefm.treasury import DepositAccountAddresses


class FakeTreasury:
    def ensure_market_liquidity_account(self, market_id: int) -> DepositAccountAddresses:
        return DepositAccountAddresses(
            owner_wallet_address=f"market-owner-{market_id}",
            token_account_address=f"market-deposit-{market_id}",
        )


class FakeDexScreenerClient:
    def __init__(self, pairs_by_mint: dict[str, list[DexScreenerPair]]) -> None:
        self._pairs_by_mint = pairs_by_mint

    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        return self._pairs_by_mint[token_mint]


def test_public_api_and_frontend_render(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("nukefm.markets.utc_now", lambda: "2026-04-15T14:00:00+00:00")

    database_path = tmp_path / "catalog.sqlite3"
    log_path = tmp_path / "logs" / "app.log"
    settings = Settings(
        app_name="nuke.fm",
        database_path=database_path,
        log_path=log_path,
        frontend_refresh_seconds=30,
        api_challenge_ttl_seconds=300,
        market_duration_days=90,
        market_resolution_threshold_fraction="0.10",
        market_rollover_lower_bound_fraction="0.25",
        market_rollover_upper_bound_fraction="4.0",
        solana_rpc_url="https://api.mainnet-beta.solana.com",
        solana_usdc_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        secret_tool_service="nuke.fm",
        deposit_master_seed_secret_name="deposit-master-seed",
        treasury_seed_secret_name="treasury-seed",
    )

    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="Mint111",
                name="Alpha",
                symbol="ALPHA",
                image_url=None,
                launched_at="2026-04-14T12:00:00+00:00",
                creator=None,
            ),
            BagsToken(
                mint="Mint333",
                name="Gamma",
                symbol="GAMMA",
                image_url=None,
                launched_at="2026-04-15T12:00:00+00:00",
                creator=None,
            ),
            BagsToken(
                mint="Mint555",
                name="Omega",
                symbol="OMEGA",
                image_url=None,
                launched_at="2026-04-16T12:00:00+00:00",
                creator=None,
            )
        ]
    )

    market_store = MarketStore(database_path)
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
                ],
                "Mint333": [
                    DexScreenerPair(
                        pair_address="gamma-pair",
                        dex_id="raydium",
                        price_usd=Decimal("0.00000000045"),
                        liquidity_usd=Decimal("80"),
                        volume_h24_usd=Decimal("4"),
                        market_cap_usd=Decimal("0.000000000321"),
                    )
                ],
                "Mint555": [
                    DexScreenerPair(
                        pair_address="omega-pair",
                        dex_id="raydium",
                        price_usd=Decimal("3"),
                        liquidity_usd=Decimal("60"),
                        volume_h24_usd=Decimal("2"),
                        market_cap_usd=Decimal("900"),
                    )
                ],
            }
        ),
        captured_at="2026-04-15T12:15:00+00:00",
    )
    treasury = FakeTreasury()
    market_store.ensure_missing_market_liquidity_accounts(treasury)

    alpha_market_id = market_store.get_token_detail("Mint111")["current_market"]["id"]
    gamma_market_id = market_store.get_token_detail("Mint333")["current_market"]["id"]
    market_store.record_market_liquidity_credit(
        market_id=alpha_market_id,
        amount_atomic=20_000_000,
        observed_balance_after_atomic=20_000_000,
        credited_at="2026-04-15T12:30:00+00:00",
    )
    market_store.record_market_liquidity_credit(
        market_id=gamma_market_id,
        amount_atomic=5_000_000,
        observed_balance_after_atomic=5_000_000,
        credited_at="2026-04-15T12:31:00+00:00",
    )

    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO market_snapshots (
                market_id,
                snapshot_hour,
                reference_price_usd,
                pair_count,
                ath_price_usd,
                ath_timestamp,
                drawdown_fraction,
                threshold_price_usd,
                captured_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                gamma_market_id,
                "2026-04-15T13:00:00+00:00",
                "0.000000000123",
                1,
                "0.000000000987",
                "2026-04-15T13:00:00+00:00",
                "0.75",
                "0.000000000045",
                "2026-04-15T13:00:00+00:00",
            ],
        )
        connection.execute(
            """
            INSERT INTO token_metrics_snapshots (
                token_mint,
                captured_at,
                pair_count,
                underlying_volume_h24_usd,
                underlying_market_cap_usd,
                source_pair_address,
                source_dex_id,
                source_price_usd,
                source_liquidity_usd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "Mint333",
                "2026-04-15T13:00:00+00:00",
                1,
                "0.000000000654",
                "0.000000000321",
                "pair-1",
                "dex-1",
                "0.000000000123",
                "1.234567",
            ],
        )
        connection.executemany(
            """
            INSERT INTO market_chart_snapshots (
                market_id,
                captured_at,
                underlying_price_usd,
                chance_of_outcome_percent
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                [gamma_market_id, "2026-04-15T13:05:00+00:00", "0.000000000123", "50"],
                [gamma_market_id, "2026-04-15T13:10:00+00:00", "0.000000000140", "61.25"],
            ],
        )

    account_store = AccountStore(database_path)
    account_store.initialize()
    trader = account_store.ensure_user("11111111111111111111111111111111")

    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO market_trades (
                user_id,
                market_id,
                outcome,
                side,
                cash_amount_atomic,
                share_amount_atomic,
                before_yes_price,
                before_no_price,
                after_yes_price,
                after_no_price,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                trader["id"],
                gamma_market_id,
                "yes",
                "buy",
                1_000_000,
                1_000_000,
                "0.5",
                "0.5",
                "0.5",
                "0.5",
                "2026-04-15T13:05:00+00:00",
            ],
        )

    app = create_app(settings=settings, catalog=catalog, market_store=market_store)
    client = TestClient(app)

    token_response = client.get("/v1/public/tokens")
    assert token_response.status_code == 200
    assert [token["symbol"] for token in token_response.json()["tokens"]] == ["OMEGA", "GAMMA", "ALPHA"]
    gamma_token = next(token for token in token_response.json()["tokens"] if token["symbol"] == "GAMMA")
    assert gamma_token["current_market"]["liquidity_deposit_address"] == "market-deposit-2"
    assert gamma_token["current_market"]["pm_volume_24h_usdc"] == "1"
    assert gamma_token["current_market"]["chance_of_outcome_percent"] == "50%"

    sorted_token_response = client.get("/v1/public/tokens?sort_by=market_liquidity&sort_direction=desc")
    assert sorted_token_response.status_code == 200
    assert [token["symbol"] for token in sorted_token_response.json()["tokens"]] == ["ALPHA", "GAMMA", "OMEGA"]

    page_response = client.get("/?sort_by=market_liquidity&sort_direction=desc")
    assert page_response.status_code == 200
    assert 'rel="icon" type="image/svg+xml" href="http://testserver/static/favicon.svg"' in page_response.text
    assert 'option value="market_liquidity" selected' in page_response.text
    assert 'option value="desc" selected' in page_response.text
    assert "$20" in page_response.text
    assert "Predicted nuke %" in page_response.text
    assert "PM liquidity" in page_response.text
    assert "PM volume" in page_response.text
    assert "Underlying volume" in page_response.text
    assert "Underlying mktcap" in page_response.text
    assert "63.41% from current price" in page_response.text
    assert page_response.text.index("<span>ALPHA</span>") < page_response.text.index("<span>GAMMA</span>")
    assert "Scan which token markets are actionable right now." not in page_response.text
    assert "OMEGA" not in page_response.text
    assert "Show uninitialized" in page_response.text

    toggle_response = client.get("/?sort_by=market_liquidity&sort_direction=desc&show_uninitialized=1")
    assert toggle_response.status_code == 200
    assert "OMEGA" in toggle_response.text
    assert "Hide uninitialized" in toggle_response.text

    detail_api_response = client.get("/v1/public/tokens/Mint333")
    assert detail_api_response.status_code == 200
    detail_api_market = detail_api_response.json()["current_market"]
    assert detail_api_market["reference_price_usd"] == "0.000000000123"
    assert detail_api_market["threshold_price_usd"] == "0.000000000045"
    assert detail_api_market["underlying_market_cap_usd"] == "0.000000000321"
    assert detail_api_market["pm_volume_24h_usdc"] == "1"
    assert detail_api_market["chance_of_outcome_percent"] == "50%"
    assert detail_api_market["remaining_drop_percent"] == "63.41%"
    assert detail_api_market["question"] == "Will GAMMA nuke by 63.41% by 2026-07-14?"
    assert detail_api_response.json()["hidden_active_markets"] == []
    assert detail_api_response.json()["current_market_chart"] == {
        "market_id": gamma_market_id,
        "interval_minutes": 5,
        "points": [
            {
                "captured_at": "2026-04-15T13:05:00+00:00",
                "underlying_price_usd": "0.000000000123",
                "chance_of_outcome_percent": "50",
            },
            {
                "captured_at": "2026-04-15T13:10:00+00:00",
                "underlying_price_usd": "0.00000000014",
                "chance_of_outcome_percent": "61.25",
            },
        ],
    }

    detail_response = client.get("/tokens/Mint333")
    assert detail_response.status_code == 200
    assert "Will GAMMA nuke by 63.41% by 2026-07-14?" in detail_response.text
    assert "PM 24h volume" in detail_response.text
    assert "Chance of nuke" in detail_response.text
    assert "Token price vs chance of nuke" in detail_response.text
    assert "snapshot-market-charts" not in detail_response.text
    assert "token-overlay-chart" in detail_response.text
    assert "cdn.jsdelivr.net/npm/chart.js" in detail_response.text
    assert '"chance_of_outcome_percent": "61.25"' in detail_response.text
    assert '<p class="decision-value">50%</p>' in detail_response.text
    assert "$0.000000000123" in detail_response.text
    assert "$0.000000000321" in detail_response.text
    assert "Latest trade was a buy of nuke exposure for $1." in detail_response.text
    assert "YES Price" not in detail_response.text
    assert "NO Price" not in detail_response.text
    assert "YES and NO skew" not in detail_response.text

    no_history_detail_response = client.get("/tokens/Mint111")
    assert no_history_detail_response.status_code == 200
    assert "The 5 minute snapshot job will populate this overlay shortly after the market is live." in no_history_detail_response.text
    assert "snapshot-market-charts" not in no_history_detail_response.text

    favicon_response = client.get("/static/favicon.svg")
    assert favicon_response.status_code == 200
    assert "<svg" in favicon_response.text


def test_board_toggle_stays_visible_when_all_markets_are_uninitialized(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    log_path = tmp_path / "logs" / "app.log"
    settings = Settings(
        app_name="nuke.fm",
        database_path=database_path,
        log_path=log_path,
        frontend_refresh_seconds=30,
        api_challenge_ttl_seconds=300,
        market_duration_days=90,
        market_resolution_threshold_fraction="0.10",
        market_rollover_lower_bound_fraction="0.25",
        market_rollover_upper_bound_fraction="4.0",
        solana_rpc_url="https://api.mainnet-beta.solana.com",
        solana_usdc_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        secret_tool_service="nuke.fm",
        deposit_master_seed_secret_name="deposit-master-seed",
        treasury_seed_secret_name="treasury-seed",
    )

    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="Mint777",
                name="Seven",
                symbol="SEVEN",
                image_url=None,
                launched_at="2026-04-17T12:00:00+00:00",
                creator=None,
            )
        ]
    )

    market_store = MarketStore(database_path)
    market_store.initialize()
    market_store.capture_token_metrics(
        FakeDexScreenerClient(
            {
                "Mint777": [
                    DexScreenerPair(
                        pair_address="seven-pair",
                        dex_id="raydium",
                        price_usd=Decimal("2"),
                        liquidity_usd=Decimal("50"),
                        volume_h24_usd=Decimal("5"),
                        market_cap_usd=Decimal("700"),
                    )
                ]
            }
        ),
        captured_at="2026-04-17T12:01:00+00:00",
    )
    app = create_app(settings=settings, catalog=catalog, market_store=market_store)
    client = TestClient(app)

    page_response = client.get("/")
    assert page_response.status_code == 200
    assert "No initialized markets in view" in page_response.text
    assert "Show uninitialized" in page_response.text
    assert 'option value="underlying_market_cap" selected' in page_response.text
