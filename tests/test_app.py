from pathlib import Path

from fastapi.testclient import TestClient

from nukefm.app import create_app
from nukefm.bags import BagsToken
from nukefm.catalog import Catalog


def test_public_api_and_frontend_render(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    log_path = tmp_path / "logs" / "app.log"
    config_path = Path("config.json")
    original_config = config_path.read_text()

    try:
        config_path.write_text(
            (
                '{\n'
                '  "app_name": "nuke.fm",\n'
                f'  "database_path": "{database_path}",\n'
                f'  "log_path": "{log_path}",\n'
                '  "frontend_refresh_seconds": 30,\n'
                '  "bags_base_url": "https://public-api-v2.bags.fm/api/v1",\n'
                '  "bags_launch_feed_path": "/token-launch/feed"\n'
                '}'
            )
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

        app = create_app()
        client = TestClient(app)

        token_response = client.get("/v1/public/tokens")
        assert token_response.status_code == 200
        assert token_response.json()["tokens"][0]["symbol"] == "GAMMA"

        page_response = client.get("/")
        assert page_response.status_code == 200
        assert "Rolling token markets without a trading UI" in page_response.text

        detail_response = client.get("/tokens/Mint333")
        assert detail_response.status_code == 200
        assert "Will GAMMA nuke by 90 days after this market opens?" in detail_response.text
    finally:
        config_path.write_text(original_config)
