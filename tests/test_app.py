from pathlib import Path

from fastapi.testclient import TestClient

from nukefm.app import create_app
from nukefm.bags import BagsToken
from nukefm.catalog import Catalog
from nukefm.config import Settings
from nukefm.markets import MarketStore
from nukefm.treasury import DepositAccountAddresses


class FakeTreasury:
    def ensure_market_liquidity_account(self, market_id: int) -> DepositAccountAddresses:
        return DepositAccountAddresses(
            owner_wallet_address=f"market-owner-{market_id}",
            token_account_address=f"market-deposit-{market_id}",
        )


def test_public_api_and_frontend_render(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    log_path = tmp_path / "logs" / "app.log"
    settings = Settings(
        app_name="nuke.fm",
        database_path=database_path,
        log_path=log_path,
        frontend_refresh_seconds=30,
        api_challenge_ttl_seconds=300,
        market_duration_days=90,
        market_threshold_fraction="0.05",
        bags_base_url="https://public-api-v2.bags.fm/api/v1",
        bags_launch_feed_path="/token-launch/feed",
        bags_api_key=None,
        dexscreener_base_url="https://api.dexscreener.com",
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

    app = create_app(settings=settings, catalog=catalog, market_store=market_store)
    client = TestClient(app)

    token_response = client.get("/v1/public/tokens")
    assert token_response.status_code == 200
    assert [token["symbol"] for token in token_response.json()["tokens"]] == ["OMEGA", "GAMMA", "ALPHA"]
    gamma_token = next(token for token in token_response.json()["tokens"] if token["symbol"] == "GAMMA")
    assert gamma_token["current_market"]["liquidity_deposit_address"] == "market-deposit-2"

    sorted_token_response = client.get("/v1/public/tokens?sort_by=market_liquidity&sort_direction=desc")
    assert sorted_token_response.status_code == 200
    assert [token["symbol"] for token in sorted_token_response.json()["tokens"]] == ["ALPHA", "GAMMA", "OMEGA"]

    page_response = client.get("/?sort_by=market_liquidity&sort_direction=desc")
    assert page_response.status_code == 200
    assert 'option value="market_liquidity" selected' in page_response.text
    assert 'option value="desc" selected' in page_response.text
    assert page_response.text.index("<p class=\"symbol-badge\">ALPHA</p>") < page_response.text.index(
        "<p class=\"symbol-badge\">GAMMA</p>"
    )
    assert "Scan which token markets are actionable right now." in page_response.text
    assert "OMEGA" not in page_response.text
    assert "Show uninitialized" in page_response.text

    toggle_response = client.get("/?sort_by=market_liquidity&sort_direction=desc&show_uninitialized=1")
    assert toggle_response.status_code == 200
    assert "OMEGA" in toggle_response.text
    assert "Hide uninitialized" in toggle_response.text

    detail_response = client.get("/tokens/Mint333")
    assert detail_response.status_code == 200
    assert "Will GAMMA nuke by 90 days after this market opens?" in detail_response.text


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
        market_threshold_fraction="0.05",
        bags_base_url="https://public-api-v2.bags.fm/api/v1",
        bags_launch_feed_path="/token-launch/feed",
        bags_api_key=None,
        dexscreener_base_url="https://api.dexscreener.com",
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
    app = create_app(settings=settings, catalog=catalog, market_store=market_store)
    client = TestClient(app)

    page_response = client.get("/")
    assert page_response.status_code == 200
    assert "No initialized markets in view" in page_response.text
    assert "Show uninitialized" in page_response.text
    assert 'option value="underlying_market_cap" selected' in page_response.text
