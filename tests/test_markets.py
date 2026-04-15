from decimal import Decimal
from pathlib import Path

from nukefm.accounts import AccountStore
from nukefm.bags import BagsToken
from nukefm.catalog import Catalog
from nukefm.database import connect_database
from nukefm.markets import MarketStore
from nukefm.treasury import DepositAccountAddresses


class FakeTreasury:
    def ensure_market_liquidity_account(self, market_id: int) -> DepositAccountAddresses:
        return DepositAccountAddresses(
            owner_wallet_address=f"market-owner-{market_id}",
            token_account_address=f"market-deposit-{market_id}",
        )

    def sweep_market_revenue(self, market_store: MarketStore, *, limit: int) -> list[dict]:
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


class FakeSettlementPriceClient:
    def __init__(self, snapshots: list[Decimal]) -> None:
        self._snapshots = snapshots
        self._index = 0
        self.calls: list[dict[str, str]] = []

    def get_rolling_median_price(self, token_mint: str, *, start_at: str, end_at: str) -> Decimal:
        self.calls.append({"token_mint": token_mint, "start_at": start_at, "end_at": end_at})
        snapshot = self._snapshots[self._index]
        self._index += 1
        return snapshot


def test_snapshot_resolution_and_rollover(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="Mint555",
                name="Omega",
                symbol="OMG",
                image_url=None,
                launched_at=None,
                creator=None,
            )
        ]
    )

    account_store = AccountStore(database_path)
    account_store.initialize()
    user = account_store.ensure_user("11111111111111111111111111111112")

    market_store = MarketStore(database_path)
    market_store.initialize()
    treasury = FakeTreasury()
    market_store.ensure_missing_market_liquidity_accounts(treasury)

    market_id = market_store.list_token_cards()[0]["current_market"]["id"]
    market_store.record_market_liquidity_credit(
        market_id=market_id,
        amount_atomic=20_000_000,
        observed_balance_after_atomic=20_000_000,
        credited_at="2026-04-14T13:30:00+00:00",
    )

    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO ledger_entries (
                user_id,
                entry_type,
                amount_atomic,
                reference_type,
                reference_id,
                note,
                created_at
            )
            VALUES (?, 'test_credit', ?, 'test', 'seed', 'Seeded test account balance.', ?)
            """,
            [user["id"], 30_000_000, "2026-04-15T12:01:00+00:00"],
        )

    buy_trade = market_store.execute_trade(
        user_id=user["id"],
        market_id=market_id,
        outcome="yes",
        side="buy",
        amount_atomic=5_000_000,
    )
    assert buy_trade["share_amount"] != "0"

    price_client = FakeSettlementPriceClient(
        [
            Decimal("10"),
            Decimal("0.4"),
            Decimal("0.8"),
        ]
    )

    market_store.capture_hourly_snapshots(price_client, captured_at="2026-04-15T13:00:00+00:00")
    market_store.capture_hourly_snapshots(price_client, captured_at="2026-04-15T14:00:00+00:00")
    market_store.capture_hourly_snapshots(price_client, captured_at="2026-04-15T15:00:00+00:00")

    assert price_client.calls == [
        {
            "token_mint": "Mint555",
            "start_at": "2026-04-14T13:30:00+00:00",
            "end_at": "2026-04-15T13:00:00+00:00",
        },
        {
            "token_mint": "Mint555",
            "start_at": "2026-04-14T14:00:00+00:00",
            "end_at": "2026-04-15T14:00:00+00:00",
        },
        {
            "token_mint": "Mint555",
            "start_at": "2026-04-14T15:00:00+00:00",
            "end_at": "2026-04-15T15:00:00+00:00",
        },
    ]

    with connect_database(database_path) as connection:
        snapshot_rows = connection.execute(
            """
            SELECT snapshot_hour, reference_price_usd, ath_price_usd, drawdown_fraction, threshold_price_usd
            FROM market_snapshots
            WHERE market_id = ?
            ORDER BY snapshot_hour ASC
            """,
            [market_id],
        ).fetchall()

    assert [
        (
            row["snapshot_hour"],
            row["reference_price_usd"],
            row["ath_price_usd"],
            row["drawdown_fraction"],
            row["threshold_price_usd"],
        )
        for row in snapshot_rows
    ] == [
        ("2026-04-15T13:00:00+00:00", "10", "10", "0", "0.50"),
        ("2026-04-15T14:00:00+00:00", "0.4", "10", "0.96", "0.50"),
        ("2026-04-15T15:00:00+00:00", "0.8", "10", "0.92", "0.50"),
    ]

    resolved = market_store.resolve_markets(
        catalog=catalog,
        treasury=treasury,
        resolved_at="2026-04-15T15:05:00+00:00",
    )

    assert resolved == [{"market_id": market_id, "state": "resolved_yes", "resolved_at": "2026-04-15T14:00:00+00:00"}]

    token = market_store.get_token_detail("Mint555")
    assert token is not None
    assert token["past_markets"][0]["state"] == "resolved_yes"
    assert token["current_market"]["sequence_number"] == 2
    assert token["current_market"]["liquidity_deposit_address"] == "market-deposit-2"
    assert market_store.list_pending_revenue_sweeps(limit=10) == []
    assert account_store.get_available_balance_atomic(user["id"]) > 30_000_000
