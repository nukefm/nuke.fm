from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .accounts import AccountStore, AuthenticatedUser
from .auth import AuthService
from .catalog import Catalog
from .config import load_settings
from .logging_utils import configure_logging
from .treasury import SolanaTreasury


PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


class ChallengeRequest(BaseModel):
    wallet_address: str


class ApiKeyExchangeRequest(BaseModel):
    wallet_address: str
    challenge_id: str
    signature: str


class WithdrawalCreateRequest(BaseModel):
    destination_wallet_address: str
    amount_usdc: str


def _extract_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str | None:
    if x_api_key:
        return x_api_key
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None


def _require_authenticated_user(
    request: Request,
    raw_api_key: Annotated[str | None, Depends(_extract_api_key)],
) -> AuthenticatedUser:
    user = request.app.state.auth_service.authenticate_api_key(raw_api_key)
    if user is None:
        raise HTTPException(status_code=401, detail="Valid API key required.")
    return user


def _resolve_treasury(request: Request) -> SolanaTreasury:
    treasury = getattr(request.app.state, "treasury", None)
    if treasury is None:
        treasury = SolanaTreasury(
            rpc_url=request.app.state.settings.solana_rpc_url,
            usdc_mint=request.app.state.settings.solana_usdc_mint,
            secret_tool_service=request.app.state.settings.secret_tool_service,
            deposit_master_seed_secret_name=request.app.state.settings.deposit_master_seed_secret_name,
            treasury_seed_secret_name=request.app.state.settings.treasury_seed_secret_name,
        )
        request.app.state.treasury = treasury
    return treasury


def create_app(
    *,
    settings=None,
    catalog: Catalog | None = None,
    account_store: AccountStore | None = None,
    auth_service: AuthService | None = None,
    treasury: SolanaTreasury | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_path)

    catalog = catalog or Catalog(settings.database_path)
    catalog.initialize()
    account_store = account_store or AccountStore(settings.database_path)
    account_store.initialize()
    auth_service = auth_service or AuthService(
        app_name=settings.app_name,
        challenge_ttl_seconds=settings.api_challenge_ttl_seconds,
        account_store=account_store,
    )

    app = FastAPI(title=settings.app_name)
    app.state.catalog = catalog
    app.state.account_store = account_store
    app.state.auth_service = auth_service
    app.state.treasury = treasury
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

    @app.post("/v1/auth/challenge")
    def create_auth_challenge(body: ChallengeRequest) -> dict:
        return auth_service.create_challenge(body.wallet_address)

    @app.post("/v1/auth/api-key")
    def create_api_key(body: ApiKeyExchangeRequest) -> dict:
        return auth_service.exchange_api_key(
            wallet_address=body.wallet_address,
            challenge_id=body.challenge_id,
            signature=body.signature,
        )

    @app.get("/v1/private/account")
    def private_account(user: Annotated[AuthenticatedUser, Depends(_require_authenticated_user)]) -> dict:
        return account_store.get_account_overview(user.user_id)

    @app.get("/v1/private/account/deposit-address")
    def private_account_deposit_address(
        user: Annotated[AuthenticatedUser, Depends(_require_authenticated_user)],
        request: Request,
    ) -> dict:
        deposit_account = account_store.get_deposit_account(user.user_id)
        if deposit_account is None or deposit_account["ata_initialized_at"] is None:
            treasury = _resolve_treasury(request)
            deposit_addresses = treasury.ensure_user_deposit_account(user.user_id)
            deposit_account = account_store.ensure_deposit_account(
                user.user_id,
                deposit_addresses.owner_wallet_address,
                deposit_addresses.token_account_address,
            )
            account_store.mark_deposit_account_initialized(user.user_id)

        return {
            "deposit_address": deposit_account["token_account_address"],
            "deposit_owner_wallet_address": deposit_account["owner_wallet_address"],
            "observed_balance_usdc": deposit_account["observed_balance_usdc"],
            "ata_initialized_at": deposit_account["ata_initialized_at"],
        }

    @app.get("/v1/private/account/deposits")
    def private_account_deposits(
        user: Annotated[AuthenticatedUser, Depends(_require_authenticated_user)],
    ) -> dict:
        return {"deposits": account_store.list_deposits(user.user_id)}

    @app.get("/v1/private/account/withdrawals")
    def private_account_withdrawals(
        user: Annotated[AuthenticatedUser, Depends(_require_authenticated_user)],
    ) -> dict:
        return {"withdrawals": account_store.list_withdrawals(user.user_id)}

    @app.get("/v1/private/account/portfolio")
    def private_account_portfolio(
        user: Annotated[AuthenticatedUser, Depends(_require_authenticated_user)],
    ) -> dict:
        account = account_store.get_account_overview(user.user_id)
        return {
            "wallet_address": account["wallet_address"],
            "open_positions": account["open_positions"],
            "trade_history": account["trade_history"],
        }

    @app.get("/v1/private/account/positions")
    def private_account_positions(
        user: Annotated[AuthenticatedUser, Depends(_require_authenticated_user)],
    ) -> dict:
        return {"positions": account_store.get_account_overview(user.user_id)["open_positions"]}

    @app.get("/v1/private/account/trades")
    def private_account_trades(
        user: Annotated[AuthenticatedUser, Depends(_require_authenticated_user)],
    ) -> dict:
        return {"trades": account_store.get_account_overview(user.user_id)["trade_history"]}

    @app.post("/v1/private/withdrawals")
    def create_withdrawal(
        body: WithdrawalCreateRequest,
        user: Annotated[AuthenticatedUser, Depends(_require_authenticated_user)],
    ) -> dict:
        from .amounts import parse_usdc_amount

        try:
            amount_atomic = parse_usdc_amount(body.amount_usdc)
            withdrawal = account_store.create_withdrawal_request(
                user.user_id,
                body.destination_wallet_address,
                amount_atomic,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return withdrawal

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
