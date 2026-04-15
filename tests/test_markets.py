from decimal import Decimal
from pathlib import Path

from nukefm.accounts import AccountStore
from nukefm.amounts import parse_usdc_amount
from nukefm.bags import BagsToken
from nukefm.catalog import Catalog
from nukefm.dexscreener import DexScreenerPair
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


class FakeDexScreenerClient:
    def __init__(self, pairs_by_mint: dict[str, list[DexScreenerPair]]) -> None:
        self._pairs_by_mint = pairs_by_mint
        self.calls: list[str] = []

    def list_token_pairs(self, token_mint: str) -> list[DexScreenerPair]:
        self.calls.append(token_mint)
        return self._pairs_by_mint.get(token_mint, [])


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


def test_token_metrics_capture_and_sorting(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="MintA",
                name="Alpha",
                symbol="ALPHA",
                image_url=None,
                launched_at="2026-04-13T10:00:00+00:00",
                creator=None,
            ),
            BagsToken(
                mint="MintB",
                name="Beta",
                symbol="BETA",
                image_url=None,
                launched_at="2026-04-14T10:00:00+00:00",
                creator=None,
            ),
            BagsToken(
                mint="MintC",
                name="Gamma",
                symbol="GAMMA",
                image_url=None,
                launched_at="2026-04-15T10:00:00+00:00",
                creator=None,
            ),
        ]
    )

    market_store = MarketStore(database_path)
    market_store.initialize()
    treasury = FakeTreasury()
    market_store.ensure_missing_market_liquidity_accounts(treasury)

    alpha_market_id = market_store.get_token_detail("MintA")["current_market"]["id"]
    beta_market_id = market_store.get_token_detail("MintB")["current_market"]["id"]

    market_store.record_market_liquidity_credit(
        market_id=alpha_market_id,
        amount_atomic=5_000_000,
        observed_balance_after_atomic=5_000_000,
        credited_at="2026-04-15T11:00:00+00:00",
    )
    market_store.record_market_liquidity_credit(
        market_id=beta_market_id,
        amount_atomic=10_000_000,
        observed_balance_after_atomic=10_000_000,
        credited_at="2026-04-15T11:05:00+00:00",
    )

    market_store.capture_hourly_snapshots(
        FakeSettlementPriceClient(
            [
                Decimal("2"),
                Decimal("4"),
            ]
        ),
        captured_at="2026-04-15T12:00:00+00:00",
    )
    market_store.capture_hourly_snapshots(
        FakeSettlementPriceClient(
            [
                Decimal("1"),
                Decimal("3"),
            ]
        ),
        captured_at="2026-04-15T13:00:00+00:00",
    )

    captured_metrics = market_store.capture_token_metrics(
        FakeDexScreenerClient(
            {
                "MintA": [
                    DexScreenerPair(
                        pair_address="alpha-1",
                        dex_id="raydium",
                        price_usd=Decimal("0.5"),
                        liquidity_usd=Decimal("300"),
                        volume_h24_usd=Decimal("100"),
                        market_cap_usd=Decimal("1000"),
                    ),
                    DexScreenerPair(
                        pair_address="alpha-2",
                        dex_id="orca",
                        price_usd=Decimal("0.4"),
                        liquidity_usd=Decimal("100"),
                        volume_h24_usd=Decimal("20"),
                        market_cap_usd=None,
                    ),
                ],
                "MintB": [
                    DexScreenerPair(
                        pair_address="beta-1",
                        dex_id="raydium",
                        price_usd=Decimal("1.2"),
                        liquidity_usd=Decimal("200"),
                        volume_h24_usd=Decimal("50"),
                        market_cap_usd=Decimal("5000"),
                    ),
                    DexScreenerPair(
                        pair_address="beta-2",
                        dex_id="orca",
                        price_usd=Decimal("1.3"),
                        liquidity_usd=Decimal("400"),
                        volume_h24_usd=Decimal("200"),
                        market_cap_usd=Decimal("8000"),
                    ),
                ],
            }
        ),
        captured_at="2026-04-15T13:30:00+00:00",
    )

    assert [row["mint"] for row in captured_metrics] == ["MintC", "MintB", "MintA"]
    assert captured_metrics[0]["underlying_volume_usd"] is None
    assert captured_metrics[1]["underlying_volume_usd"] == "250"
    assert captured_metrics[1]["underlying_market_cap_usd"] == "8000"
    assert captured_metrics[2]["underlying_volume_usd"] == "120"

    alpha_card = market_store.get_token_detail("MintA")
    assert alpha_card is not None
    assert alpha_card["current_market"]["total_liquidity_usdc"] == "5"
    assert alpha_card["current_market"]["underlying_volume_24h_usd"] == "120"
    assert alpha_card["current_market"]["underlying_market_cap_usd"] == "1000"

    beta_card = market_store.get_token_detail("MintB")
    assert beta_card is not None
    assert beta_card["current_market"]["drawdown_fraction"] == "0.25"
    assert beta_card["current_market"]["underlying_volume_24h_usd"] == "250"
    assert beta_card["current_market"]["underlying_market_cap_usd"] == "8000"

    gamma_card = market_store.get_token_detail("MintC")
    assert gamma_card is not None
    assert gamma_card["current_market"]["total_liquidity_usdc"] is None
    assert gamma_card["current_market"]["underlying_volume_24h_usd"] is None
    assert gamma_card["current_market"]["underlying_market_cap_usd"] is None

    expected_orders = {
        ("market_liquidity", "asc"): ["MintA", "MintB", "MintC"],
        ("market_liquidity", "desc"): ["MintB", "MintA", "MintC"],
        ("dump_percentage", "asc"): ["MintB", "MintA", "MintC"],
        ("dump_percentage", "desc"): ["MintA", "MintB", "MintC"],
        ("underlying_volume", "asc"): ["MintA", "MintB", "MintC"],
        ("underlying_volume", "desc"): ["MintB", "MintA", "MintC"],
        ("underlying_market_cap", "asc"): ["MintA", "MintB", "MintC"],
        ("underlying_market_cap", "desc"): ["MintB", "MintA", "MintC"],
    }
    for (sort_by, sort_direction), expected_mints in expected_orders.items():
        sorted_cards = market_store.list_token_cards(sort_by=sort_by, sort_direction=sort_direction)
        assert [card["mint"] for card in sorted_cards] == expected_mints


def test_weekly_auto_seed_targets_top_market_caps_once_per_week(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint=f"Mint{index:02d}",
                name=f"Token {index}",
                symbol=f"T{index:02d}",
                image_url=None,
                launched_at=f"2026-04-{index + 1:02d}T10:00:00+00:00",
                creator=None,
            )
            for index in range(12)
        ]
    )

    market_store = MarketStore(database_path)
    market_store.initialize()
    market_store.capture_token_metrics(
        FakeDexScreenerClient(
            {
                f"Mint{index:02d}": [
                    DexScreenerPair(
                        pair_address=f"pair-{index}",
                        dex_id="raydium",
                        price_usd=Decimal("1"),
                        liquidity_usd=Decimal("100"),
                        volume_h24_usd=Decimal("10"),
                        market_cap_usd=None if index == 11 else Decimal(str(index + 1)),
                    )
                ]
                for index in range(12)
            }
        ),
        captured_at="2026-04-15T12:00:00+00:00",
    )

    seeded_markets = market_store.seed_top_markets_by_market_cap(
        amount_atomic=parse_usdc_amount("1"),
        limit=10,
        recorded_at="2026-04-15T13:00:00+00:00",
    )

    assert [row["mint"] for row in seeded_markets] == [
        "Mint10",
        "Mint09",
        "Mint08",
        "Mint07",
        "Mint06",
        "Mint05",
        "Mint04",
        "Mint03",
        "Mint02",
        "Mint01",
    ]
    assert market_store.get_outstanding_treasury_debt_usdc() == "10"

    rerun = market_store.seed_top_markets_by_market_cap(
        amount_atomic=parse_usdc_amount("1"),
        limit=10,
        recorded_at="2026-04-16T09:00:00+00:00",
    )
    assert rerun == []

    next_week = market_store.seed_top_markets_by_market_cap(
        amount_atomic=parse_usdc_amount("1"),
        limit=2,
        recorded_at="2026-04-22T09:00:00+00:00",
    )
    assert [row["mint"] for row in next_week] == ["Mint10", "Mint09"]
    assert market_store.get_outstanding_treasury_debt_usdc() == "12"


def test_record_treasury_funding_reduces_auto_seed_debt(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="MintA",
                name="Alpha",
                symbol="ALPHA",
                image_url=None,
                launched_at="2026-04-15T10:00:00+00:00",
                creator=None,
            )
        ]
    )

    market_store = MarketStore(database_path)
    market_store.initialize()
    market_store.capture_token_metrics(
        FakeDexScreenerClient(
            {
                "MintA": [
                    DexScreenerPair(
                        pair_address="pair-a",
                        dex_id="raydium",
                        price_usd=Decimal("1"),
                        liquidity_usd=Decimal("100"),
                        volume_h24_usd=Decimal("10"),
                        market_cap_usd=Decimal("1000"),
                    )
                ]
            }
        ),
        captured_at="2026-04-15T12:00:00+00:00",
    )

    market_store.seed_top_markets_by_market_cap(
        amount_atomic=parse_usdc_amount("1"),
        limit=1,
        recorded_at="2026-04-15T13:00:00+00:00",
    )

    funding = market_store.record_treasury_funding(
        amount_atomic=parse_usdc_amount("0.4"),
        funded_at="2026-04-15T14:00:00+00:00",
    )
    assert funding["funded_amount_usdc"] == "0.4"
    assert funding["remaining_debt_usdc"] == "0.6"
    assert market_store.get_outstanding_treasury_debt_usdc() == "0.6"

    try:
        market_store.record_treasury_funding(
            amount_atomic=parse_usdc_amount("1"),
            funded_at="2026-04-15T15:00:00+00:00",
        )
    except ValueError as error:
        assert str(error) == "Treasury funding exceeds outstanding seed debt."
    else:
        raise AssertionError("Expected treasury overpayment to fail.")
