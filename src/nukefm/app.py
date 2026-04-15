from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .catalog import Catalog
from .config import load_settings
from .logging_utils import configure_logging


PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging(settings.log_path)

    catalog = Catalog(settings.database_path)
    catalog.initialize()

    app = FastAPI(title=settings.app_name)
    app.state.catalog = catalog
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/v1/public/tokens")
    def list_tokens() -> dict:
        return {"tokens": catalog.list_token_cards()}

    @app.get("/v1/public/tokens/{mint}")
    def token_detail(mint: str) -> dict:
        token = catalog.get_token_detail(mint)
        if token is None:
            raise HTTPException(status_code=404, detail="Token not found")
        return token

    @app.get("/", response_class=HTMLResponse)
    def market_list_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "tokens": catalog.list_token_cards(),
                "refresh_seconds": settings.frontend_refresh_seconds,
            },
        )

    @app.get("/tokens/{mint}", response_class=HTMLResponse)
    def token_page(request: Request, mint: str):
        token = catalog.get_token_detail(mint)
        if token is None:
            raise HTTPException(status_code=404, detail="Token not found")
        return TEMPLATES.TemplateResponse(
            request=request,
            name="token.html",
            context={
                "token": token,
                "refresh_seconds": settings.frontend_refresh_seconds,
            },
        )

    return app
