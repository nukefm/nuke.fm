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
        return [self._with_supply(pair) for pair in self._pairs_by_mint[token_mint]]

    @staticmethod
    def _with_supply(pair: DexScreenerPair) -> DexScreenerPair:
        if pair.token_supply is not None or pair.market_cap_usd is None or pair.price_usd is None:
            return pair
        return DexScreenerPair(
            pair_address=pair.pair_address,
            dex_id=pair.dex_id,
            price_usd=pair.price_usd,
            liquidity_usd=pair.liquidity_usd,
            volume_h24_usd=pair.volume_h24_usd,
            market_cap_usd=None,
            token_supply=pair.market_cap_usd / pair.price_usd,
            market_cap_kind="circulating",
        )


def app_settings(tmp_path: Path) -> Settings:
    database_path = tmp_path / "catalog.sqlite3"
    log_path = tmp_path / "logs" / "app.log"
    return Settings(
        app_name="nuke.fm",
        database_path=database_path,
        log_path=log_path,
        frontend_refresh_seconds=30,
        api_challenge_ttl_seconds=300,
        market_duration_days=90,
        market_price_range_multiple="10",
        market_rollover_boundary_rate="0.85",
        market_rollover_liquidity_transfer_fraction="0.80",
        solana_rpc_url="https://api.mainnet-beta.solana.com",
        solana_usdc_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        secret_tool_service="nuke.fm",
        deposit_master_seed_secret_name="deposit-master-seed",
        treasury_seed_secret_name="treasury-seed",
    )


class FakeCatalog:
    def initialize(self) -> None:
        pass


class FakeAccountStore:
    def initialize(self) -> None:
        pass


class StaticMarketStore:
    def __init__(self, tokens: list[dict]) -> None:
        self._tokens = tokens

    def initialize(self) -> None:
        pass

    def list_token_cards(self, *, sort_by: str | None = None, sort_direction: str = "desc") -> list[dict]:
        return self._tokens

    def get_token_detail(self, mint: str) -> dict | None:
        return next((token for token in self._tokens if token["mint"] == mint), None)


def token_fixture(*, mint: str, symbol: str, predicted_nuke_percent: str | None, predicted_nuke_fraction: str | None) -> dict:
    return {
        "mint": mint,
        "name": symbol.title(),
        "symbol": symbol,
        "image_url": None,
        "launched_at": None,
        "creator": None,
        "bags_token_url": f"https://bags.fm/{mint}",
        "current_market_chart": {"points": []},
        "hidden_active_markets": [],
        "past_markets": [],
        "current_market": {
            "id": 1,
            "state": "open",
            "question": f"What will {symbol} trade at?",
            "sequence_number": 1,
            "market_start": "2026-04-15T12:00:00+00:00",
            "expiry": "2026-07-14T12:00:00+00:00",
            "resolved_at": None,
            "liquidity_deposit_address": "market-deposit",
            "implied_price_usd": "1",
            "starting_price_usd": "1",
            "min_price_usd": "0.1",
            "max_price_usd": "10",
            "pm_volume_24h_usdc": "0",
            "total_liquidity_usdc": "10",
            "underlying_volume_24h_usd": "100",
            "underlying_market_cap_usd": "1000",
            "predicted_nuke_percent": predicted_nuke_percent,
            "predicted_nuke_fraction": predicted_nuke_fraction,
        },
    }


def test_public_api_and_frontend_render(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("nukefm.markets.utc_now", lambda: "2026-04-15T14:00:00+00:00")

    settings = app_settings(tmp_path)
    database_path = settings.database_path

    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="Mint111",
                name="Alpha",
                symbol="ALPHA",
                image_url="https://example.test/alpha.png",
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
                        volume_h24_usd=Decimal("1234.5"),
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
                captured_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                gamma_market_id,
                "2026-04-15T13:00:00+00:00",
                "0.000000000123",
                1,
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
                token_supply,
                market_cap_kind,
                source_pair_address,
                source_dex_id,
                source_price_usd,
                source_liquidity_usd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "Mint333",
                "2026-04-15T13:00:00+00:00",
                1,
                "0.000000000654",
                "0.000000000321",
                "2.609756097560975609756097561",
                "circulating",
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
                implied_price_usd
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                [gamma_market_id, "2026-04-15T13:05:00+00:00", "0.000000000123", "0.00000000045"],
                [gamma_market_id, "2026-04-15T13:10:00+00:00", "0.000000000140", "0.00000000055"],
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
                before_long_price,
                before_short_price,
                after_long_price,
                after_short_price,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                trader["id"],
                gamma_market_id,
                "long",
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
    assert gamma_token["bags_token_url"] == "https://bags.fm/Mint333"
    assert gamma_token["current_market"]["liquidity_deposit_address"] == "market-deposit-2"
    assert gamma_token["current_market"]["pm_volume_24h_usdc"] == "1"
    assert gamma_token["current_market"]["implied_price_usd"] == "0.00000000045"

    sorted_token_response = client.get("/v1/public/tokens?sort_by=market_liquidity&sort_direction=desc")
    assert sorted_token_response.status_code == 200
    assert [token["symbol"] for token in sorted_token_response.json()["tokens"]] == ["ALPHA", "GAMMA", "OMEGA"]

    page_response = client.get("/?sort_by=market_liquidity&sort_direction=desc")
    assert page_response.status_code == 200
    assert 'rel="icon" type="image/svg+xml" href="http://testserver/static/favicon.svg?v=20260426"' in page_response.text
    assert 'rel="stylesheet" href="http://testserver/static/app.css?v=' in page_response.text
    assert "Trading stays in the API." not in page_response.text
    assert 'option value="market_liquidity" selected' in page_response.text
    assert 'option value="desc" selected' in page_response.text
    assert 'class="sort-heading' not in page_response.text
    assert "Clear sort" not in page_response.text
    assert "$20.00" in page_response.text
    assert "$1.00" in page_response.text
    assert "$1,235" in page_response.text
    assert "$1,234.50" not in page_response.text
    assert "$0.00000000045" in page_response.text
    assert "265.85%" in page_response.text
    assert "265.85% from current price" not in page_response.text
    assert "from current price" in page_response.text
    assert "By 14 Jul" in page_response.text
    assert 'href="https://bags.fm/Mint333" target="_blank" rel="noopener">Bags token</a>' in page_response.text
    assert "Implied by predicted market cap" not in page_response.text
    assert "Implied move" in page_response.text
    assert "Predicted nuke %" not in page_response.text
    assert "Prediction liquidity" in page_response.text
    assert "Prediction volume" in page_response.text
    assert "Token volume" in page_response.text
    assert "Token mktcap" in page_response.text
    assert "Launch order" not in page_response.text
    assert 'option value="token"' not in page_response.text
    assert 'option value="implied_price"' not in page_response.text
    assert "Implied price" in page_response.text
    assert "Expiry" not in page_response.text
    assert "sort_by=expiry" not in page_response.text
    assert "State" in page_response.text
    assert "Live prediction" in page_response.text
    assert '<img class="token-avatar" src="https://example.test/alpha.png" alt="Alpha token image">' in page_response.text
    assert page_response.text.index("<span>ALPHA</span>") < page_response.text.index("<span>GAMMA</span>")
    assert "Scan which token markets are actionable right now." not in page_response.text
    assert "OMEGA" not in page_response.text
    assert "Show uninitialized" in page_response.text

    toggle_response = client.get("/?sort_by=market_liquidity&sort_direction=desc&show_uninitialized=1")
    assert toggle_response.status_code == 200
    assert "OMEGA" in toggle_response.text
    assert "Signal waiting on seed" in toggle_response.text
    assert "Hide uninitialized" in toggle_response.text

    default_sort_response = client.get("/?show_uninitialized=1")
    assert default_sort_response.status_code == 200
    assert 'option value="underlying_volume" selected' in default_sort_response.text

    removed_sort_response = client.get("/?sort_by=token&sort_direction=asc&show_uninitialized=1")
    assert removed_sort_response.status_code == 400

    asc_response = client.get("/?sort_by=state&sort_direction=asc&show_uninitialized=1")
    assert asc_response.status_code == 200
    assert asc_response.text.index("<span>ALPHA</span>") < asc_response.text.index("<span>GAMMA</span>")
    assert asc_response.text.index("<span>GAMMA</span>") < asc_response.text.index("<span>OMEGA</span>")

    detail_api_response = client.get("/v1/public/tokens/Mint333")
    assert detail_api_response.status_code == 200
    detail_api_market = detail_api_response.json()["current_market"]
    assert detail_api_market["reference_price_usd"] == "0.000000000123"
    assert detail_api_market["min_price_usd"] == "0.000000000045"
    assert detail_api_market["underlying_market_cap_usd"] == "0.000000000321"
    assert detail_api_market["pm_volume_24h_usdc"] == "1"
    assert detail_api_market["implied_price_usd"] == "0.00000000045"
    assert detail_api_market["predicted_nuke_percent"] == "265.85%"
    assert detail_api_market["question"] == "What will GAMMA trade at by 2026-07-14?"
    assert detail_api_response.json()["hidden_active_markets"] == []
    assert detail_api_response.json()["current_market_chart"] == {
        "market_id": gamma_market_id,
        "interval_minutes": 5,
        "points": [
            {
                "captured_at": "2026-04-15T13:05:00+00:00",
                "underlying_price_usd": "0.000000000123",
                "implied_price_usd": "0.00000000045",
            },
            {
                "captured_at": "2026-04-15T13:10:00+00:00",
                "underlying_price_usd": "0.00000000014",
                "implied_price_usd": "0.00000000055",
            },
        ],
    }

    detail_response = client.get("/tokens/Mint333")
    assert detail_response.status_code == 200
    assert "What will GAMMA trade at by 14 Jul?" in detail_response.text
    assert "What will GAMMA trade at by 2026-07-14?" not in detail_response.text
    assert "By 14 Jul" in detail_response.text
    assert "2026-07-14T12:00:00+00:00" not in detail_response.text
    assert "Live PM signal based on market price" in detail_response.text
    assert "5 minute snapshots of token price and PM implied price." in detail_response.text
    assert "Market lifecycle and Bags launch context." in detail_response.text
    assert "The PM is live. Compare" not in detail_response.text
    assert "Aligned 5 minute snapshots" not in detail_response.text
    assert "Lifecycle details and Bags launch context that stay useful" not in detail_response.text
    assert "Prediction 24h volume" in detail_response.text
    assert "Implied price" in detail_response.text
    assert "Token price vs PM implied price" in detail_response.text
    assert "Bags context" in detail_response.text
    assert "Bags mint" in detail_response.text
    assert 'href="https://bags.fm/Mint333" target="_blank" rel="noopener">Open on Bags</a>' in detail_response.text
    assert "Activation Gate" not in detail_response.text
    assert "Series mechanics" not in detail_response.text
    assert detail_response.text.count("market-deposit-2") == 3
    assert "snapshot-market-charts" not in detail_response.text
    assert "token-overlay-chart" in detail_response.text
    assert "cdn.jsdelivr.net/npm/chart.js" in detail_response.text
    assert '"implied_price_usd": "0.00000000055"' in detail_response.text
    assert '<p class="decision-value">$0.00000000045</p>' in detail_response.text
    assert "LONG Price" not in detail_response.text
    assert "SHORT Price" not in detail_response.text
    assert "LONG and SHORT skew" not in detail_response.text

    no_history_detail_response = client.get("/tokens/Mint111")
    assert no_history_detail_response.status_code == 200
    assert "The 5 minute snapshot job will populate this overlay shortly after the market is live." in no_history_detail_response.text
    assert "snapshot-market-charts" not in no_history_detail_response.text

    favicon_response = client.get("/static/favicon.svg")
    assert favicon_response.status_code == 200
    assert "<svg" in favicon_response.text

    about_response = client.get("/about")
    assert about_response.status_code == 200
    assert "Prediction-market signals for Bags tokens" in about_response.text
    assert "Read-only web, API trading" in about_response.text


def test_board_can_show_uninitialized_markets_after_reset(tmp_path: Path) -> None:
    settings = app_settings(tmp_path)
    database_path = settings.database_path

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
    assert "SEVEN" not in page_response.text
    assert "No initialized markets in view" in page_response.text
    assert "Show uninitialized" in page_response.text

    expanded_response = client.get("/?show_uninitialized=1")
    assert expanded_response.status_code == 200
    assert "SEVEN" in expanded_response.text
    assert "Signal waiting on seed" in expanded_response.text
    assert "No initialized markets in view" not in expanded_response.text
    assert "Hide uninitialized" in expanded_response.text


def test_predicted_nuke_values_render_sign_classes(tmp_path: Path) -> None:
    tokens = [
        token_fixture(
            mint="MintPositive",
            symbol="RISK",
            predicted_nuke_percent="12.34%",
            predicted_nuke_fraction="0.1234",
        ),
        token_fixture(
            mint="MintNegative",
            symbol="UP",
            predicted_nuke_percent="-56.78%",
            predicted_nuke_fraction="-0.5678",
        ),
        token_fixture(
            mint="MintPending",
            symbol="WAIT",
            predicted_nuke_percent=None,
            predicted_nuke_fraction=None,
        ),
    ]
    app = create_app(
        settings=app_settings(tmp_path),
        catalog=FakeCatalog(),
        account_store=FakeAccountStore(),
        market_store=StaticMarketStore(tokens),
    )
    client = TestClient(app)

    page_response = client.get("/")
    assert page_response.status_code == 200
    assert '<strong class="nuke-sign-positive">12.34%</strong>' in page_response.text
    assert '<strong class="nuke-sign-negative">-56.78%</strong>' in page_response.text
    assert "<strong>Pending</strong>" in page_response.text

    positive_detail_response = client.get("/tokens/MintPositive")
    assert positive_detail_response.status_code == 200
    assert '<p class="decision-value nuke-sign-positive">12.34%</p>' in positive_detail_response.text

    negative_detail_response = client.get("/tokens/MintNegative")
    assert negative_detail_response.status_code == 200
    assert '<p class="decision-value nuke-sign-negative">-56.78%</p>' in negative_detail_response.text

    pending_detail_response = client.get("/tokens/MintPending")
    assert pending_detail_response.status_code == 200
    assert '<p class="decision-value">Pending</p>' in pending_detail_response.text
