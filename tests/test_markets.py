from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from nukefm.accounts import AccountStore
from nukefm.amounts import parse_usdc_amount
from nukefm.bags import BagsToken
from nukefm.catalog import Catalog
from nukefm.dexscreener import DexScreenerPair
from nukefm.database import connect_database, utc_now
from nukefm.markets import MarketStore
from nukefm.treasury import DepositAccountAddresses
from nukefm.weighted_pool import format_decimal, yes_price


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


def create_markets_from_prices(
    market_store: MarketStore,
    prices_by_mint: dict[str, Decimal],
    *,
    captured_at: str = "2026-04-15T12:00:00+00:00",
) -> None:
    market_store.capture_token_metrics(
        FakeDexScreenerClient(
            {
                mint: [
                    DexScreenerPair(
                        pair_address=f"{mint}-pair",
                        dex_id="raydium",
                        price_usd=price,
                        liquidity_usd=Decimal("100"),
                        volume_h24_usd=Decimal("10"),
                        market_cap_usd=Decimal("1000"),
                    )
                ]
                for mint, price in prices_by_mint.items()
            }
        ),
        captured_at=captured_at,
    )


class SelectiveSettlementPriceClient:
    def __init__(self, prices_by_mint: dict[str, Decimal], missing_mints: set[str]) -> None:
        self._prices_by_mint = prices_by_mint
        self._missing_mints = missing_mints
        self.calls: list[str] = []

    def get_rolling_median_price(self, token_mint: str, *, start_at: str, end_at: str) -> Decimal:
        self.calls.append(token_mint)
        if token_mint in self._missing_mints:
            raise ValueError(f"No settlement prices returned for {token_mint}.")
        return self._prices_by_mint[token_mint]


def test_initialize_migrates_legacy_market_in_place_and_preserves_deposit_address(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    with connect_database(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE tokens (
                mint TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                image_url TEXT,
                launched_at TEXT,
                creator TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_mint TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                question TEXT NOT NULL,
                state TEXT NOT NULL,
                market_start TEXT,
                expiry TEXT,
                liquidity_deposit_address TEXT,
                resolved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(token_mint, sequence_number)
            );

            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE token_metrics_snapshots (
                token_mint TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                pair_count INTEGER NOT NULL,
                underlying_volume_h24_usd TEXT,
                underlying_market_cap_usd TEXT,
                source_pair_address TEXT,
                source_dex_id TEXT,
                source_price_usd TEXT,
                source_liquidity_usd TEXT,
                PRIMARY KEY(token_mint, captured_at)
            );

            CREATE TABLE market_liquidity_accounts (
                market_id INTEGER PRIMARY KEY,
                owner_wallet_address TEXT NOT NULL UNIQUE,
                token_account_address TEXT NOT NULL UNIQUE,
                observed_balance_atomic INTEGER NOT NULL DEFAULT 0,
                ata_initialized_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO tokens (mint, symbol, name, image_url, launched_at, creator, created_at, updated_at)
            VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            ["MintLegacy", "LEG", "Legacy", "2026-04-15T11:46:49+00:00", "2026-04-15T11:46:49+00:00"],
        )
        connection.execute(
            """
            INSERT INTO markets (
                id,
                token_mint,
                sequence_number,
                question,
                state,
                market_start,
                expiry,
                liquidity_deposit_address,
                resolved_at,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            [
                7,
                "MintLegacy",
                1,
                "Will LEG nuke by 90 days after this market opens?",
                "open",
                "2026-04-15T16:34:04+00:00",
                "2026-07-14T16:34:04+00:00",
                "legacy-deposit-address",
                "2026-04-15T11:46:49+00:00",
                "2026-04-15T16:34:04+00:00",
            ],
        )
        connection.execute(
            """
            INSERT INTO market_liquidity_accounts (
                market_id,
                owner_wallet_address,
                token_account_address,
                observed_balance_atomic,
                ata_initialized_at,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 0, ?, ?, ?)
            """,
            [
                7,
                "legacy-owner-wallet",
                "legacy-deposit-address",
                "2026-04-15T16:30:00+00:00",
                "2026-04-15T16:30:00+00:00",
                "2026-04-15T16:30:00+00:00",
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
            VALUES (?, ?, 1, NULL, NULL, 'pair-1', 'raydium', ?, '100')
            """,
            ["MintLegacy", "2026-04-15T16:30:26+00:00", "2.5"],
        )

    market_store = MarketStore(database_path)
    market_store.initialize()

    with connect_database(database_path) as connection:
        migrated_market = connection.execute(
            """
            SELECT id, question, expiry, liquidity_deposit_address, starting_price_usd, threshold_price_usd, range_floor_price_usd, range_ceiling_price_usd
            FROM markets
            WHERE id = 7
            """
        ).fetchone()
        migrated_account = connection.execute(
            """
            SELECT market_id, owner_wallet_address, token_account_address
            FROM market_liquidity_accounts
            WHERE market_id = 7
            """
        ).fetchone()

    assert migrated_market is not None
    assert migrated_market["id"] == 7
    assert migrated_market["question"] == "Will LEG nuke?"
    assert migrated_market["liquidity_deposit_address"] == "legacy-deposit-address"
    assert migrated_market["starting_price_usd"] == "2.5"
    assert migrated_market["threshold_price_usd"] == "0.25"
    assert migrated_market["range_floor_price_usd"] == "0.625"
    assert migrated_market["range_ceiling_price_usd"] == "10"
    assert migrated_market["expiry"] == "2026-07-14T16:34:04+00:00"
    assert migrated_account is not None
    assert migrated_account["market_id"] == 7
    assert migrated_account["token_account_address"] == "legacy-deposit-address"


def test_initialize_prunes_dead_legacy_markets_without_observed_price(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    with connect_database(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE tokens (
                mint TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                image_url TEXT,
                launched_at TEXT,
                creator TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_mint TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                question TEXT NOT NULL,
                state TEXT NOT NULL,
                market_start TEXT,
                expiry TEXT,
                liquidity_deposit_address TEXT,
                resolved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(token_mint, sequence_number)
            );

            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO tokens (mint, symbol, name, image_url, launched_at, creator, created_at, updated_at)
            VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            ["MintDead", "DEAD", "Dead", "2026-04-15T11:46:49+00:00", "2026-04-15T11:46:49+00:00"],
        )
        connection.execute(
            """
            INSERT INTO markets (
                token_mint,
                sequence_number,
                question,
                state,
                market_start,
                expiry,
                liquidity_deposit_address,
                resolved_at,
                created_at,
                updated_at
            )
            VALUES (?, 1, ?, 'awaiting_liquidity', NULL, NULL, NULL, NULL, ?, ?)
            """,
            [
                "MintDead",
                "Will DEAD nuke by 90 days after this market opens?",
                "2026-04-15T11:46:49+00:00",
                "2026-04-15T11:46:49+00:00",
            ],
        )

    market_store = MarketStore(database_path)
    market_store.initialize()

    with connect_database(database_path) as connection:
        remaining_markets = connection.execute("SELECT COUNT(*) AS count FROM markets").fetchone()["count"]

    assert remaining_markets == 0


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
    create_markets_from_prices(market_store, {"Mint555": Decimal("10")}, captured_at="2026-04-15T12:00:00+00:00")
    treasury = FakeTreasury()
    market_store.ensure_missing_market_liquidity_accounts(treasury)

    market_id = market_store.list_token_cards()[0]["current_market"]["id"]
    market_store.record_market_liquidity_credit(
        market_id=market_id,
        amount_atomic=20_000_000,
        observed_balance_after_atomic=20_000_000,
        credited_at="2026-04-15T13:30:00+00:00",
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

    market_store.capture_hourly_snapshots(price_client, captured_at="2026-04-15T14:00:00+00:00")
    market_store.capture_hourly_snapshots(price_client, captured_at="2026-04-15T15:00:00+00:00")
    market_store.capture_hourly_snapshots(price_client, captured_at="2026-04-15T16:00:00+00:00")

    assert price_client.calls == [
        {
            "token_mint": "Mint555",
            "start_at": "2026-04-14T13:00:00+00:00",
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
            SELECT snapshot_hour, captured_at, reference_price_usd, ath_price_usd, drawdown_fraction, threshold_price_usd
            FROM market_snapshots
            WHERE market_id = ?
            ORDER BY snapshot_hour ASC
            """,
            [market_id],
        ).fetchall()

    assert [
        (
            row["snapshot_hour"],
            row["captured_at"],
            row["reference_price_usd"],
            row["ath_price_usd"],
            row["drawdown_fraction"],
            row["threshold_price_usd"],
        )
        for row in snapshot_rows
    ] == [
        ("2026-04-15T13:00:00+00:00", "2026-04-15T14:00:00+00:00", "10", "10", "0", "1"),
        ("2026-04-15T14:00:00+00:00", "2026-04-15T15:00:00+00:00", "0.4", "10", "0.96", "1"),
        ("2026-04-15T15:00:00+00:00", "2026-04-15T16:00:00+00:00", "0.8", "10", "0.92", "1"),
    ]

    resolved = market_store.resolve_markets(
        catalog=catalog,
        treasury=treasury,
        resolved_at="2026-04-15T16:05:00+00:00",
    )

    assert resolved == [{"market_id": market_id, "state": "resolved_yes", "resolved_at": "2026-04-15T14:00:00+00:00"}]

    token = market_store.get_token_detail("Mint555")
    assert token is not None
    assert token["past_markets"][0]["state"] == "resolved_yes"
    assert token["current_market"]["sequence_number"] == 2
    assert token["current_market"]["liquidity_deposit_address"] == "market-deposit-2"
    assert market_store.list_pending_revenue_sweeps(limit=10) == []
    assert account_store.get_available_balance_atomic(user["id"]) > 30_000_000


def test_range_exit_creates_hidden_active_predecessor(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="MintRoll",
                name="Rollover",
                symbol="ROLL",
                image_url=None,
                launched_at=None,
                creator=None,
            )
        ]
    )

    market_store = MarketStore(database_path)
    market_store.initialize()
    create_markets_from_prices(market_store, {"MintRoll": Decimal("10")}, captured_at="2026-04-15T12:00:00+00:00")
    treasury = FakeTreasury()
    market_store.ensure_missing_market_liquidity_accounts(treasury)

    first_market_id = market_store.get_token_detail("MintRoll")["current_market"]["id"]
    market_store.record_market_liquidity_credit(
        market_id=first_market_id,
        amount_atomic=10_000_000,
        observed_balance_after_atomic=10_000_000,
        credited_at="2026-04-15T12:30:00+00:00",
    )

    market_store.capture_hourly_snapshots(
        FakeSettlementPriceClient([Decimal("45")]),
        captured_at="2026-04-15T13:00:00+00:00",
    )

    token = market_store.get_token_detail("MintRoll")
    assert token is not None
    assert token["current_market"]["sequence_number"] == 2
    assert token["current_market"]["state"] == "awaiting_liquidity"
    assert token["current_market"]["starting_price_usd"] == "45"
    assert token["hidden_active_markets"][0]["id"] == first_market_id
    assert token["hidden_active_markets"][0]["state"] == "open"
    assert token["hidden_active_markets"][0]["is_frontend_visible"] is False

    quoted = market_store.quote_trade(
        market_id=first_market_id,
        outcome="yes",
        side="buy",
        amount_atomic=1_000_000,
    )
    assert quoted["market_id"] == first_market_id


def test_snapshot_uses_last_closed_wall_clock_hour_before_market_start(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="MintFresh",
                name="Fresh",
                symbol="FRESH",
                image_url=None,
                launched_at="2026-04-15T16:30:00+00:00",
                creator=None,
            )
        ]
    )

    market_store = MarketStore(database_path)
    market_store.initialize()
    create_markets_from_prices(market_store, {"MintFresh": Decimal("1")}, captured_at="2026-04-15T16:20:00+00:00")
    treasury = FakeTreasury()
    market_store.ensure_missing_market_liquidity_accounts(treasury)
    market_id = market_store.list_token_cards()[0]["current_market"]["id"]
    market_store.record_market_liquidity_credit(
        market_id=market_id,
        amount_atomic=1_000_000,
        observed_balance_after_atomic=1_000_000,
        credited_at="2026-04-15T16:34:04+00:00",
    )

    price_client = FakeSettlementPriceClient([Decimal("1"), Decimal("1.5")])
    captured = market_store.capture_hourly_snapshots(price_client, captured_at="2026-04-15T16:34:30+00:00")
    updated = market_store.capture_hourly_snapshots(price_client, captured_at="2026-04-15T17:05:00+00:00")

    assert captured == [
        {
            "market_id": market_id,
            "state": "open",
            "reference_price_usd": "1",
            "threshold_price_usd": "0.1",
        }
    ]
    assert updated == [
        {
            "market_id": market_id,
            "state": "open",
            "reference_price_usd": "1.5",
            "threshold_price_usd": "0.1",
        }
    ]
    assert price_client.calls == [
        {
            "token_mint": "MintFresh",
            "start_at": "2026-04-14T16:00:00+00:00",
            "end_at": "2026-04-15T16:00:00+00:00",
        },
        {
            "token_mint": "MintFresh",
            "start_at": "2026-04-14T17:00:00+00:00",
            "end_at": "2026-04-15T17:00:00+00:00",
        },
    ]

    with connect_database(database_path) as connection:
        snapshot_rows = connection.execute(
            """
            SELECT snapshot_hour, captured_at, reference_price_usd, ath_price_usd, threshold_price_usd
            FROM market_snapshots
            WHERE market_id = ?
            """,
            [market_id],
        ).fetchall()

    assert [tuple(row) for row in snapshot_rows] == [
        (
            "2026-04-15T16:00:00+00:00",
            "2026-04-15T16:34:30+00:00",
            "1",
            "1",
            "0.1",
        ),
        (
            "2026-04-15T17:00:00+00:00",
            "2026-04-15T17:05:00+00:00",
            "1.5",
            "1",
            "0.1",
        )
    ]


def test_snapshot_skips_only_markets_missing_price_data(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(mint="MintGood", name="Good", symbol="GOOD", image_url=None, launched_at=None, creator=None),
            BagsToken(mint="MintMissing", name="Missing", symbol="MISS", image_url=None, launched_at=None, creator=None),
        ]
    )

    market_store = MarketStore(database_path)
    market_store.initialize()
    create_markets_from_prices(
        market_store,
        {
            "MintGood": Decimal("2"),
            "MintMissing": Decimal("3"),
        },
        captured_at="2026-04-15T09:50:00+00:00",
    )
    treasury = FakeTreasury()
    market_store.ensure_missing_market_liquidity_accounts(treasury)

    good_market_id = market_store.get_token_detail("MintGood")["current_market"]["id"]
    missing_market_id = market_store.get_token_detail("MintMissing")["current_market"]["id"]
    market_store.record_market_liquidity_credit(
        market_id=good_market_id,
        amount_atomic=1_000_000,
        observed_balance_after_atomic=1_000_000,
        credited_at="2026-04-15T10:00:00+00:00",
    )
    market_store.record_market_liquidity_credit(
        market_id=missing_market_id,
        amount_atomic=1_000_000,
        observed_balance_after_atomic=1_000_000,
        credited_at="2026-04-15T10:05:00+00:00",
    )

    captured = market_store.capture_hourly_snapshots(
        SelectiveSettlementPriceClient(
            prices_by_mint={"MintGood": Decimal("2")},
            missing_mints={"MintMissing"},
        ),
        captured_at="2026-04-15T11:00:00+00:00",
    )

    assert captured == [
        {
            "market_id": good_market_id,
            "state": "open",
            "reference_price_usd": "2",
            "threshold_price_usd": "0.2",
        }
    ]

    with connect_database(database_path) as connection:
        snapshot_rows = connection.execute(
            """
            SELECT market_id, reference_price_usd
            FROM market_snapshots
            ORDER BY market_id ASC
            """
        ).fetchall()

    assert [tuple(row) for row in snapshot_rows] == [(good_market_id, "2")]


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
    create_markets_from_prices(
        market_store,
        {
            "MintA": Decimal("2"),
            "MintB": Decimal("4"),
            "MintC": Decimal("1"),
        },
    )
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
                Decimal("4"),
                Decimal("2"),
            ]
        ),
        captured_at="2026-04-15T12:00:00+00:00",
    )
    market_store.capture_hourly_snapshots(
        FakeSettlementPriceClient(
            [
                Decimal("3"),
                Decimal("1"),
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
    assert beta_card["current_market"]["remaining_drop_percent"] == "86.67%"
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
        ("dump_percentage", "asc"): ["MintA", "MintB", "MintC"],
        ("dump_percentage", "desc"): ["MintC", "MintB", "MintA"],
        ("underlying_volume", "asc"): ["MintA", "MintB", "MintC"],
        ("underlying_volume", "desc"): ["MintB", "MintA", "MintC"],
        ("underlying_market_cap", "asc"): ["MintA", "MintB", "MintC"],
        ("underlying_market_cap", "desc"): ["MintB", "MintA", "MintC"],
    }
    for (sort_by, sort_direction), expected_mints in expected_orders.items():
        sorted_cards = market_store.list_token_cards(sort_by=sort_by, sort_direction=sort_direction)
        assert [card["mint"] for card in sorted_cards] == expected_mints


def test_current_market_serialization_uses_trailing_24h_pm_volume(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="MintVolume",
                name="Volume",
                symbol="VOL",
                image_url=None,
                launched_at="2026-04-15T10:00:00+00:00",
                creator=None,
            )
        ]
    )

    account_store = AccountStore(database_path)
    account_store.initialize()
    user = account_store.ensure_user("11111111111111111111111111111112")

    market_store = MarketStore(database_path)
    market_store.initialize()
    create_markets_from_prices(market_store, {"MintVolume": Decimal("1")})

    market_id = market_store.get_token_detail("MintVolume")["current_market"]["id"]
    reference_time = datetime.fromisoformat(utc_now())
    recent_trade_time = reference_time.isoformat()
    old_trade_time = (reference_time - timedelta(hours=25)).isoformat()

    with connect_database(database_path) as connection:
        for cash_amount_atomic, created_at in ((1_500_000, old_trade_time), (2_500_000, recent_trade_time)):
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
                    user["id"],
                    market_id,
                    "yes",
                    "buy",
                    cash_amount_atomic,
                    cash_amount_atomic,
                    "0.5",
                    "0.5",
                    "0.5",
                    "0.5",
                    created_at,
                ],
            )

    token = market_store.get_token_detail("MintVolume")
    assert token is not None
    assert token["current_market"]["pm_volume_24h_usdc"] == "2.5"

    token_cards = market_store.list_token_cards()
    assert token_cards[0]["current_market"]["pm_volume_24h_usdc"] == "2.5"


def test_market_chart_snapshots_bucket_and_serialize_current_series(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(database_path)
    catalog.initialize()
    catalog.ingest_tokens(
        [
            BagsToken(
                mint="MintChart",
                name="Chart",
                symbol="CHART",
                image_url=None,
                launched_at="2026-04-15T10:00:00+00:00",
                creator=None,
            )
        ]
    )

    account_store = AccountStore(database_path)
    account_store.initialize()
    user = account_store.ensure_user("11111111111111111111111111111113")

    market_store = MarketStore(database_path)
    market_store.initialize()
    create_markets_from_prices(market_store, {"MintChart": Decimal("1.25")})
    market_id = market_store.get_token_detail("MintChart")["current_market"]["id"]
    market_store.record_market_liquidity_credit(
        market_id=market_id,
        amount_atomic=10_000_000,
        observed_balance_after_atomic=10_000_000,
        credited_at="2026-04-15T12:00:00+00:00",
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
            [user["id"], 20_000_000, "2026-04-15T12:01:00+00:00"],
        )

    first_capture = market_store.capture_market_chart_snapshots(
        FakeDexScreenerClient(
            {
                "MintChart": [
                    DexScreenerPair(
                        pair_address="pair-1",
                        dex_id="raydium",
                        price_usd=Decimal("1.25"),
                        liquidity_usd=Decimal("100"),
                        volume_h24_usd=Decimal("10"),
                        market_cap_usd=Decimal("500"),
                    )
                ]
            }
        ),
        captured_at="2026-04-15T12:34:20+00:00",
    )
    market_store.execute_trade(
        user_id=user["id"],
        market_id=market_id,
        outcome="yes",
        side="buy",
        amount_atomic=2_000_000,
    )
    second_capture = market_store.capture_market_chart_snapshots(
        FakeDexScreenerClient(
            {
                "MintChart": [
                    DexScreenerPair(
                        pair_address="pair-2",
                        dex_id="raydium",
                        price_usd=Decimal("1.50"),
                        liquidity_usd=Decimal("120"),
                        volume_h24_usd=Decimal("11"),
                        market_cap_usd=Decimal("550"),
                    )
                ]
            }
        ),
        captured_at="2026-04-15T12:39:54+00:00",
    )
    with connect_database(database_path) as connection:
        expected_chance_percent = format_decimal(yes_price(market_store._load_pool(connection, market_id)) * Decimal("100"))
    updated_same_bucket = market_store.capture_market_chart_snapshots(
        FakeDexScreenerClient(
            {
                "MintChart": [
                    DexScreenerPair(
                        pair_address="pair-3",
                        dex_id="raydium",
                        price_usd=Decimal("1.55"),
                        liquidity_usd=Decimal("130"),
                        volume_h24_usd=Decimal("12"),
                        market_cap_usd=Decimal("575"),
                    )
                ]
            }
        ),
        captured_at="2026-04-15T12:39:59+00:00",
    )

    assert first_capture == [
        {
            "market_id": market_id,
            "captured_at": "2026-04-15T12:30:00+00:00",
            "underlying_price_usd": "1.25",
            "chance_of_outcome_percent": "50",
        }
    ]
    assert second_capture == [
        {
            "market_id": market_id,
            "captured_at": "2026-04-15T12:35:00+00:00",
            "underlying_price_usd": "1.5",
            "chance_of_outcome_percent": expected_chance_percent,
        }
    ]
    assert updated_same_bucket == [
        {
            "market_id": market_id,
            "captured_at": "2026-04-15T12:35:00+00:00",
            "underlying_price_usd": "1.55",
            "chance_of_outcome_percent": expected_chance_percent,
        }
    ]

    token = market_store.get_token_detail("MintChart")
    assert token is not None
    assert token["current_market_chart"] == {
        "market_id": market_id,
        "interval_minutes": 5,
        "points": [
            {
                "captured_at": "2026-04-15T12:30:00+00:00",
                "underlying_price_usd": "1.25",
                "chance_of_outcome_percent": "50",
            },
            {
                "captured_at": "2026-04-15T12:35:00+00:00",
                "underlying_price_usd": "1.55",
                "chance_of_outcome_percent": expected_chance_percent,
            },
        ],
    }


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
