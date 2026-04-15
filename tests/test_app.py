from pathlib import Path

from fastapi.testclient import TestClient

from nukefm.app import create_app
from nukefm.bags import BagsToken
from nukefm.catalog import Catalog
from nukefm.config import Settings


def test_public_api_and_frontend_render(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    log_path = tmp_path / "logs" / "app.log"
    settings = Settings(
        app_name="nuke.fm",
        database_path=database_path,
        log_path=log_path,
        frontend_refresh_seconds=30,
        api_challenge_ttl_seconds=300,
        bags_base_url="https://public-api-v2.bags.fm/api/v1",
        bags_launch_feed_path="/token-launch/feed",
        bags_api_key=None,
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
                mint="Mint333",
                name="Gamma",
                symbol="GAMMA",
                image_url=None,
                launched_at="2026-04-15T12:00:00+00:00",
                creator=None,
            )
        ]
    )

    app = create_app(settings=settings, catalog=catalog, treasury=object())
    client = TestClient(app)

    token_response = client.get("/v1/public/tokens")
    assert token_response.status_code == 200
    assert token_response.json()["tokens"][0]["symbol"] == "GAMMA"

    page_response = client.get("/")
    assert page_response.status_code == 200
    assert "Read the market board. Trade somewhere else." in page_response.text

    detail_response = client.get("/tokens/Mint333")
    assert detail_response.status_code == 200
    assert "Will GAMMA nuke by 90 days after this market opens?" in detail_response.text
