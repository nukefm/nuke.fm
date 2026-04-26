from decimal import Decimal
from pathlib import Path

import base58
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from nukefm.accounts import AccountStore
from nukefm.amounts import format_usdc_amount, parse_usdc_amount
from nukefm.app import create_app
from nukefm.bags import BagsToken
from nukefm.catalog import Catalog
from nukefm.config import Settings
from nukefm.dexscreener import DexScreenerPair
from nukefm.treasury import DepositAccountAddresses


class FakeTreasury:
    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def ensure_user_deposit_account(self, user_id: int) -> DepositAccountAddresses:
        return DepositAccountAddresses(
            owner_wallet_address=f"owner-{user_id}",
            token_account_address=f"deposit-{user_id}",
        )

    def ensure_market_liquidity_account(self, market_id: int) -> DepositAccountAddresses:
        return DepositAccountAddresses(
            owner_wallet_address=f"market-owner-{market_id}",
            token_account_address=f"market-deposit-{market_id}",
        )

    def set_balance(self, token_account_address: str, amount_atomic: int) -> None:
        self._balances[token_account_address] = amount_atomic

    def reconcile_deposits(self, account_store: AccountStore) -> list[dict]:
        credited = []
        for deposit_account in account_store.list_deposit_accounts():
            current_balance = self._balances.get(
                deposit_account["token_account_address"],
                deposit_account["observed_balance_atomic"],
            )
            observed_balance = deposit_account["observed_balance_atomic"]
            if current_balance < observed_balance:
                raise RuntimeError("Deposit balance cannot move backwards in the fake treasury.")
            if current_balance == observed_balance:
                continue
            credited.append(
                account_store.record_deposit_credit(
                    user_id=deposit_account["user_id"],
                    deposit_account_id=deposit_account["deposit_account_id"],
                    amount_atomic=current_balance - observed_balance,
                    observed_balance_after_atomic=current_balance,
                    credited_at="2026-04-15T12:30:00+00:00",
                )
            )
        return credited

    def process_withdrawals(self, account_store: AccountStore, *, limit: int) -> list[dict]:
        processed = []
        for withdrawal in account_store.list_withdrawals_by_state(("requested",), limit):
            account_store.mark_withdrawal_broadcasted(
                withdrawal_id=withdrawal["id"],
                destination_token_account_address=f"dest-ata-{withdrawal['id']}",
                broadcast_signature=f"sig-{withdrawal['id']}",
                broadcast_at="2026-04-15T12:40:00+00:00",
            )
            account_store.mark_withdrawal_completed(withdrawal["id"], "2026-04-15T12:41:00+00:00")
            processed.append({"withdrawal_id": withdrawal["id"], "state": "completed"})
        return processed

    def reconcile_market_liquidity(self, market_store) -> list[dict]:
        credited = []
        for deposit_account in market_store.list_market_liquidity_accounts():
            current_balance = self._balances.get(
                deposit_account["token_account_address"],
                deposit_account["observed_balance_atomic"],
            )
            observed_balance = deposit_account["observed_balance_atomic"]
            if current_balance < observed_balance:
                raise RuntimeError("Market liquidity balance cannot move backwards in the fake treasury.")
            if current_balance == observed_balance:
                continue
            credited.append(
                market_store.record_market_liquidity_credit(
                    market_id=deposit_account["market_id"],
                    amount_atomic=current_balance - observed_balance,
                    observed_balance_after_atomic=current_balance,
                    credited_at="2026-04-15T12:31:00+00:00",
                )
            )
        return credited

    def sweep_market_revenue(self, market_store, *, limit: int) -> list[dict]:
        processed = []
        for sweep in market_store.list_pending_revenue_sweeps(limit=limit):
            market_store.mark_revenue_sweep_completed(
                market_id=sweep["market_id"],
                destination_token_account_address="treasury-ata",
                onchain_amount_atomic=0,
                broadcast_signature=f"sweep-{sweep['market_id']}",
                completed_at="2026-04-15T18:00:00+00:00",
            )
            processed.append({"market_id": sweep["market_id"], "state": "completed"})
        return processed


class FakeDexScreenerClient:
    def __init__(self, pairs_by_mint: dict[str, list[DexScreenerPair]]) -> None:
        self._pairs_by_mint = pairs_by_mint

    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        return self._pairs_by_mint[token_mint]


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_name="nuke.fm",
        database_path=tmp_path / "catalog.sqlite3",
        log_path=tmp_path / "logs" / "app.log",
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


def _bootstrap_private_client(client: TestClient) -> tuple[str, str]:
    signing_key = SigningKey.generate()
    wallet_address = base58.b58encode(signing_key.verify_key.encode()).decode("utf-8")

    challenge_response = client.post("/v1/auth/challenge", json={"wallet_address": wallet_address})
    challenge = challenge_response.json()
    signature = signing_key.sign(challenge["challenge_message"].encode("utf-8")).signature
    signature_base58 = base58.b58encode(signature).decode("utf-8")

    api_key_response = client.post(
        "/v1/auth/api-key",
        json={
            "wallet_address": wallet_address,
            "challenge_id": challenge["challenge_id"],
            "signature": signature_base58,
        },
    )
    return wallet_address, api_key_response.json()["api_key"]


def test_private_auth_deposits_and_withdrawals(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    catalog = Catalog(settings.database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="Mint444",
                name="Delta",
                symbol="DELTA",
                image_url=None,
                launched_at=None,
                creator=None,
            )
        ]
    )

    account_store = AccountStore(settings.database_path)
    account_store.initialize()
    treasury = FakeTreasury()
    app = create_app(settings=settings, catalog=catalog, account_store=account_store, treasury=treasury)
    client = TestClient(app)
    app.state.market_store.capture_token_metrics(
        FakeDexScreenerClient(
            {
                "Mint444": [
                    DexScreenerPair(
                        pair_address="delta-pair",
                        dex_id="raydium",
                        price_usd=Decimal("1.2"),
                        liquidity_usd=Decimal("100"),
                        volume_h24_usd=Decimal("10"),
                        market_cap_usd=Decimal("1200"),
                    )
                ]
            }
        ),
        captured_at="2026-04-15T12:00:00+00:00",
    )

    wallet_address, api_key = _bootstrap_private_client(client)
    headers = {"X-API-Key": api_key}

    unauthorized_response = client.get("/v1/private/account")
    assert unauthorized_response.status_code == 401

    account_response = client.get("/v1/private/account", headers=headers)
    assert account_response.status_code == 200
    assert account_response.json()["wallet_address"] == wallet_address
    assert account_response.json()["account_balance_usdc"] == "0"

    deposit_address_response = client.get("/v1/private/account/deposit-address", headers=headers)
    assert deposit_address_response.status_code == 200
    deposit_address = deposit_address_response.json()["deposit_address"]
    assert deposit_address == "deposit-1"

    app.state.market_store.ensure_missing_market_liquidity_accounts(treasury)
    token_list_response = client.get("/v1/public/tokens")
    market_id = token_list_response.json()["tokens"][0]["current_market"]["id"]
    market_deposit_address = token_list_response.json()["tokens"][0]["current_market"]["liquidity_deposit_address"]
    assert market_deposit_address == f"market-deposit-{market_id}"
    treasury.set_balance(market_deposit_address, parse_usdc_amount("20"))
    market_credited = treasury.reconcile_market_liquidity(app.state.market_store)
    assert market_credited[0]["amount_usdc"] == "20"

    treasury.set_balance(deposit_address, parse_usdc_amount("12.5"))
    credited = treasury.reconcile_deposits(account_store)
    assert credited[0]["amount_usdc"] == "12.5"

    updated_account_response = client.get("/v1/private/account", headers=headers)
    assert updated_account_response.json()["account_balance_usdc"] == "12.5"

    buy_quote_response = client.post(
        "/v1/private/trades/quote",
        headers=headers,
        json={"market_id": market_id, "outcome": "long", "side": "buy", "amount_usdc": "3"},
    )
    assert buy_quote_response.status_code == 200
    assert buy_quote_response.json()["share_amount"] != "0"

    buy_trade_response = client.post(
        "/v1/private/trades",
        headers=headers,
        json={"market_id": market_id, "outcome": "long", "side": "buy", "amount_usdc": "3"},
    )
    assert buy_trade_response.status_code == 200
    assert buy_trade_response.json()["amount_usdc"] == "3"

    rationale_response = client.post(
        "/v1/private/tokens/Mint444/rationale",
        headers=headers,
        json={
            "forecast_price_usd": "1.6",
            "confidence": "0.72",
            "rationale": "Liquidity is thin, but the Bags tape still has enough bid support for a higher expiry print.",
            "sources": ["https://bags.fm/Mint444"],
        },
    )
    assert rationale_response.status_code == 200
    assert rationale_response.json()["rationale"].startswith("Liquidity is thin")
    assert rationale_response.json()["position_value_usdc"] != "0"

    public_detail_response = client.get("/v1/public/tokens/Mint444")
    assert public_detail_response.json()["rationales"] == [rationale_response.json()]

    positions_response = client.get("/v1/private/account/positions", headers=headers)
    assert positions_response.status_code == 200
    assert positions_response.json()["positions"][0]["long_shares"] != "0"

    sell_quote_response = client.post(
        "/v1/private/trades/quote",
        headers=headers,
        json={"market_id": market_id, "outcome": "long", "side": "sell", "share_amount": "1"},
    )
    assert sell_quote_response.status_code == 200
    assert sell_quote_response.json()["amount_usdc"] != "0"
    assert sell_quote_response.json()["requested_share_amount"] == "1"

    sell_trade_response = client.post(
        "/v1/private/trades",
        headers=headers,
        json={"market_id": market_id, "outcome": "long", "side": "sell", "share_amount": "1"},
    )
    assert sell_trade_response.status_code == 200
    assert sell_trade_response.json()["amount_usdc"] != "0"
    assert sell_trade_response.json()["requested_share_amount"] == "1"
    realized_sell_amount_usdc = sell_trade_response.json()["amount_usdc"]

    trades_response = client.get("/v1/private/account/trades", headers=headers)
    assert trades_response.status_code == 200
    assert len(trades_response.json()["trades"]) == 2

    deposits_response = client.get("/v1/private/account/deposits", headers=headers)
    assert deposits_response.status_code == 200
    assert deposits_response.json()["deposits"][0]["amount_usdc"] == "12.5"

    withdrawal_response = client.post(
        "/v1/private/withdrawals",
        headers=headers,
        json={"destination_wallet_address": wallet_address, "amount_usdc": "2.25"},
    )
    assert withdrawal_response.status_code == 200
    assert withdrawal_response.json()["amount_usdc"] == "2.25"

    held_balance_response = client.get("/v1/private/account", headers=headers)
    expected_held_balance = format_usdc_amount(
        parse_usdc_amount("12.5")
        - parse_usdc_amount("3")
        + parse_usdc_amount(realized_sell_amount_usdc)
        - parse_usdc_amount("2.25")
    )
    assert held_balance_response.json()["account_balance_usdc"] == expected_held_balance
    assert held_balance_response.json()["pending_withdrawal_usdc"] == "2.25"

    processed = treasury.process_withdrawals(account_store, limit=10)
    assert processed[0]["state"] == "completed"

    withdrawals_response = client.get("/v1/private/account/withdrawals", headers=headers)
    assert withdrawals_response.status_code == 200
    assert withdrawals_response.json()["withdrawals"][0]["state"] == "completed"

    settled_account_response = client.get("/v1/private/account", headers=headers)
    assert settled_account_response.json()["account_balance_usdc"] == expected_held_balance
    assert settled_account_response.json()["pending_withdrawal_usdc"] == "0"

    portfolio_response = client.get("/v1/private/account/portfolio", headers=headers)
    assert portfolio_response.status_code == 200
    assert len(portfolio_response.json()["trade_history"]) == 2
