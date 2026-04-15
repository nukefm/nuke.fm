from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3

from loguru import logger

from .amounts import format_usdc_amount
from .catalog import ACTIVE_MARKET_STATES
from .database import connect_database, utc_now
from .display import format_usd_display
from .settlement import SettlementPriceClient
from .dexscreener import DexScreenerPair, DexScreenerPairClient
from .weighted_pool import (
    ONE,
    WeightedPoolState,
    amount_in_given_out,
    amount_out_given_in,
    format_decimal,
    no_price,
    parse_decimal,
    retuned_weights_for_equal_liquidity,
    yes_price,
)


TOKEN_CARD_SORT_OPTIONS = (
    ("market_liquidity", "Market liquidity"),
    ("dump_percentage", "Dump %"),
    ("underlying_volume", "Underlying volume"),
    ("underlying_market_cap", "Underlying market cap"),
)
TOKEN_CARD_SORT_FIELDS = {value for value, _ in TOKEN_CARD_SORT_OPTIONS}


class MarketStore:
    def __init__(
        self,
        database_path: Path,
        *,
        market_duration_days: int = 90,
        threshold_fraction: Decimal = Decimal("0.05"),
    ) -> None:
        self._database_path = database_path
        self._market_duration = timedelta(days=market_duration_days)
        self._threshold_fraction = threshold_fraction

    def initialize(self) -> None:
        with connect_database(self._database_path) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS market_pools (
                    market_id INTEGER PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
                    yes_reserve_atomic INTEGER NOT NULL,
                    no_reserve_atomic INTEGER NOT NULL,
                    yes_weight TEXT NOT NULL,
                    no_weight TEXT NOT NULL,
                    cash_backing_atomic INTEGER NOT NULL,
                    total_liquidity_atomic INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_liquidity_accounts (
                    market_id INTEGER PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
                    owner_wallet_address TEXT NOT NULL UNIQUE,
                    token_account_address TEXT NOT NULL UNIQUE,
                    observed_balance_atomic INTEGER NOT NULL DEFAULT 0,
                    ata_initialized_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_liquidity_deposits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    amount_atomic INTEGER NOT NULL,
                    observed_balance_after_atomic INTEGER NOT NULL,
                    credited_at TEXT NOT NULL,
                    UNIQUE(market_id, observed_balance_after_atomic)
                );

                CREATE TABLE IF NOT EXISTS treasury_debt_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER REFERENCES markets(id) ON DELETE SET NULL,
                    amount_atomic INTEGER NOT NULL,
                    entry_type TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_liquidity_seed_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    week_start TEXT NOT NULL,
                    amount_atomic INTEGER NOT NULL,
                    treasury_debt_entry_id INTEGER NOT NULL REFERENCES treasury_debt_entries(id) ON DELETE RESTRICT,
                    credited_at TEXT NOT NULL,
                    UNIQUE(market_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS market_positions (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    yes_shares_atomic INTEGER NOT NULL DEFAULT 0,
                    no_shares_atomic INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, market_id)
                );

                CREATE TABLE IF NOT EXISTS market_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    outcome TEXT NOT NULL,
                    side TEXT NOT NULL,
                    cash_amount_atomic INTEGER NOT NULL,
                    share_amount_atomic INTEGER NOT NULL,
                    before_yes_price TEXT NOT NULL,
                    before_no_price TEXT NOT NULL,
                    after_yes_price TEXT NOT NULL,
                    after_no_price TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_pair_snapshots (
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    snapshot_hour TEXT NOT NULL,
                    pair_address TEXT NOT NULL,
                    dex_id TEXT,
                    price_usd TEXT NOT NULL,
                    liquidity_usd TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY(market_id, snapshot_hour, pair_address)
                );

                CREATE TABLE IF NOT EXISTS market_snapshots (
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    snapshot_hour TEXT NOT NULL,
                    reference_price_usd TEXT NOT NULL,
                    pair_count INTEGER NOT NULL,
                    ath_price_usd TEXT NOT NULL,
                    ath_timestamp TEXT NOT NULL,
                    drawdown_fraction TEXT NOT NULL,
                    threshold_price_usd TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY(market_id, snapshot_hour)
                );

                CREATE TABLE IF NOT EXISTS token_metrics_snapshots (
                    token_mint TEXT NOT NULL REFERENCES tokens(mint) ON DELETE CASCADE,
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

                CREATE TABLE IF NOT EXISTS market_payouts (
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    winning_outcome TEXT NOT NULL,
                    winning_shares_atomic INTEGER NOT NULL,
                    payout_atomic INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(market_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS market_revenue_sweeps (
                    market_id INTEGER PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
                    amount_atomic INTEGER NOT NULL,
                    source_token_account_address TEXT,
                    destination_token_account_address TEXT,
                    onchain_amount_atomic INTEGER,
                    state TEXT NOT NULL,
                    broadcast_signature TEXT,
                    recorded_at TEXT NOT NULL,
                    completed_at TEXT,
                    failed_at TEXT,
                    failure_reason TEXT
                );
                """
            )

    def list_token_cards(self, *, sort_by: str | None = None, sort_direction: str = "desc") -> list[dict]:
        with connect_database(self._database_path) as connection:
            rows = self._list_current_market_rows(connection)
            pm_volume_24h_by_market_id = self._market_volume_24h_by_market_id(
                connection,
                [row["id"] for row in rows],
            )
            token_cards = [
                self._serialize_token_card(
                    connection,
                    row,
                    pm_volume_24h_atomic=pm_volume_24h_by_market_id.get(row["id"], 0),
                )
                for row in rows
            ]

            normalized_sort_by = None if sort_by in (None, "") else sort_by
            if normalized_sort_by is None:
                return token_cards

            self._sort_token_cards(token_cards, sort_by=normalized_sort_by, sort_direction=sort_direction)
            return token_cards

    def capture_token_metrics(
        self,
        metrics_client: DexScreenerPairClient,
        *,
        captured_at: str | None = None,
    ) -> list[dict]:
        captured_timestamp = captured_at or utc_now()

        with connect_database(self._database_path) as connection:
            rows = self._list_current_market_rows(connection)
            captured_rows: list[dict] = []

            for row in rows:
                pairs = metrics_client.list_token_pairs(row["mint"])
                underlying_volume = self._sum_pair_volume(pairs)
                market_cap_pair = self._most_liquid_pair_with_market_cap(pairs)

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
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(token_mint, captured_at) DO UPDATE SET
                        pair_count = excluded.pair_count,
                        underlying_volume_h24_usd = excluded.underlying_volume_h24_usd,
                        underlying_market_cap_usd = excluded.underlying_market_cap_usd,
                        source_pair_address = excluded.source_pair_address,
                        source_dex_id = excluded.source_dex_id,
                        source_price_usd = excluded.source_price_usd,
                        source_liquidity_usd = excluded.source_liquidity_usd
                    """,
                    [
                        row["mint"],
                        captured_timestamp,
                        len(pairs),
                        None if underlying_volume is None else str(underlying_volume),
                        None if market_cap_pair is None or market_cap_pair.market_cap_usd is None else str(market_cap_pair.market_cap_usd),
                        None if market_cap_pair is None else market_cap_pair.pair_address,
                        None if market_cap_pair is None else market_cap_pair.dex_id,
                        None if market_cap_pair is None or market_cap_pair.price_usd is None else str(market_cap_pair.price_usd),
                        None if market_cap_pair is None else str(market_cap_pair.liquidity_usd),
                    ],
                )
                captured_rows.append(
                    {
                        "mint": row["mint"],
                        "captured_at": captured_timestamp,
                        "pair_count": len(pairs),
                        "underlying_volume_usd": None if underlying_volume is None else format_decimal(underlying_volume),
                        "underlying_market_cap_usd": None
                        if market_cap_pair is None or market_cap_pair.market_cap_usd is None
                        else format_decimal(market_cap_pair.market_cap_usd),
                    }
                )

            return captured_rows

    def get_token_detail(self, mint: str) -> dict | None:
        with connect_database(self._database_path) as connection:
            token_row = connection.execute(
                """
                SELECT mint, symbol, name, image_url, launched_at, creator, updated_at
                FROM tokens
                WHERE mint = ?
                """,
                [mint],
            ).fetchone()
            if token_row is None:
                return None

            current_market = connection.execute(
                """
                SELECT *
                FROM markets
                WHERE token_mint = ?
                  AND state IN ('awaiting_liquidity', 'open', 'halted')
                ORDER BY sequence_number DESC
                LIMIT 1
                """,
                [mint],
            ).fetchone()
            if current_market is None:
                return None

            past_market_rows = connection.execute(
                """
                SELECT *
                FROM markets
                WHERE token_mint = ?
                  AND state IN ('resolved_yes', 'resolved_no', 'void')
                ORDER BY sequence_number DESC
                """,
                [mint],
            ).fetchall()
            market_ids = [current_market["id"], *[row["id"] for row in past_market_rows]]
            pm_volume_24h_by_market_id = self._market_volume_24h_by_market_id(connection, market_ids)

            return {
                "mint": token_row["mint"],
                "symbol": token_row["symbol"],
                "name": token_row["name"],
                "image_url": token_row["image_url"],
                "launched_at": token_row["launched_at"],
                "creator": token_row["creator"],
                "current_market": self._serialize_market(
                    connection,
                    current_market,
                    pm_volume_24h_atomic=pm_volume_24h_by_market_id.get(current_market["id"], 0),
                ),
                "past_markets": [
                    self._serialize_market(
                        connection,
                        row,
                        pm_volume_24h_atomic=pm_volume_24h_by_market_id.get(row["id"], 0),
                    )
                    for row in past_market_rows
                ],
                "recent_activity": self._recent_activity(connection, current_market, token_row["updated_at"]),
            }

    def quote_trade(self, *, market_id: int, outcome: str, side: str, amount_atomic: int) -> dict:
        with connect_database(self._database_path) as connection:
            market_row = self._load_tradeable_market(connection, market_id)
            pool = self._load_pool(connection, market_id)
            quote = self._quote_trade(connection, market_row, pool, outcome, side, amount_atomic)
            return self._public_quote(quote)

    def execute_trade(
        self,
        *,
        user_id: int,
        market_id: int,
        outcome: str,
        side: str,
        amount_atomic: int,
    ) -> dict:
        executed_at = utc_now()
        with connect_database(self._database_path) as connection:
            market_row = self._load_tradeable_market(connection, market_id)
            pool = self._load_pool(connection, market_id)
            quote = self._quote_trade(connection, market_row, pool, outcome, side, amount_atomic)

            if side == "buy":
                available_balance = self._available_balance_atomic(connection, user_id)
                if amount_atomic > available_balance:
                    raise ValueError("Trade amount exceeds available balance.")
                ledger_entry_id = self._insert_ledger_entry(
                    connection,
                    user_id=user_id,
                    entry_type=f"market_buy_{outcome}",
                    amount_atomic=-amount_atomic,
                    reference_type="market_trade",
                    reference_id="pending",
                    note=f"Bought {outcome.upper()} shares in market {market_id}.",
                    created_at=executed_at,
                )
                self._adjust_position(connection, user_id, market_id, outcome, quote["share_amount_atomic"])
            else:
                self._require_position(connection, user_id, market_id, outcome, amount_atomic)
                ledger_entry_id = self._insert_ledger_entry(
                    connection,
                    user_id=user_id,
                    entry_type=f"market_sell_{outcome}",
                    amount_atomic=quote["cash_amount_atomic"],
                    reference_type="market_trade",
                    reference_id="pending",
                    note=f"Sold {outcome.upper()} exposure in market {market_id}.",
                    created_at=executed_at,
                )
                self._adjust_position(connection, user_id, market_id, outcome, -quote["share_amount_atomic"])

            updated_pool = quote["pool_after"]
            self._update_pool(connection, market_id, updated_pool, executed_at)

            trade_row = connection.execute(
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
                RETURNING id
                """,
                [
                    user_id,
                    market_id,
                    outcome,
                    side,
                    quote["cash_amount_atomic"],
                    quote["share_amount_atomic"],
                    quote["before_yes_price_usd"],
                    quote["before_no_price_usd"],
                    quote["after_yes_price_usd"],
                    quote["after_no_price_usd"],
                    executed_at,
                ],
            ).fetchone()

            connection.execute(
                """
                UPDATE ledger_entries
                SET reference_id = ?
                WHERE id = ?
                """,
                [str(trade_row["id"]), ledger_entry_id],
            )

            quote["trade_id"] = trade_row["id"]
            quote["created_at"] = executed_at
            quote["market_id"] = market_id
            quote["token_mint"] = market_row["token_mint"]
            quote["symbol"] = market_row["symbol"]
            return self._public_quote(quote)

    def list_positions(self, user_id: int) -> list[dict]:
        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    tokens.mint,
                    tokens.symbol,
                    tokens.name,
                    markets.id AS market_id,
                    markets.question,
                    markets.state,
                    market_positions.yes_shares_atomic,
                    market_positions.no_shares_atomic
                FROM market_positions
                JOIN markets ON markets.id = market_positions.market_id
                JOIN tokens ON tokens.mint = markets.token_mint
                WHERE market_positions.user_id = ?
                  AND (market_positions.yes_shares_atomic > 0 OR market_positions.no_shares_atomic > 0)
                ORDER BY markets.id DESC
                """,
                [user_id],
            ).fetchall()

            positions: list[dict] = []
            for row in rows:
                pool = self._load_pool(connection, row["market_id"], required=False)
                current_yes_price = None if pool is None else yes_price(pool)
                current_no_price = None if pool is None else no_price(pool)
                marked_value_atomic = 0
                if current_yes_price is not None:
                    marked_value_atomic += int(Decimal(row["yes_shares_atomic"]) * current_yes_price)
                    marked_value_atomic += int(Decimal(row["no_shares_atomic"]) * current_no_price)
                positions.append(
                    {
                        "market_id": row["market_id"],
                        "mint": row["mint"],
                        "symbol": row["symbol"],
                        "name": row["name"],
                        "question": row["question"],
                        "state": row["state"],
                        "yes_shares": format_usdc_amount(row["yes_shares_atomic"]),
                        "no_shares": format_usdc_amount(row["no_shares_atomic"]),
                        "yes_price_usd": None if current_yes_price is None else format_decimal(current_yes_price),
                        "no_price_usd": None if current_no_price is None else format_decimal(current_no_price),
                        "marked_value_usdc": format_usdc_amount(marked_value_atomic),
                    }
                )
            return positions

    def list_trade_history(self, user_id: int) -> list[dict]:
        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    market_trades.id,
                    market_trades.market_id,
                    market_trades.outcome,
                    market_trades.side,
                    market_trades.cash_amount_atomic,
                    market_trades.share_amount_atomic,
                    market_trades.before_yes_price,
                    market_trades.before_no_price,
                    market_trades.after_yes_price,
                    market_trades.after_no_price,
                    market_trades.created_at,
                    tokens.mint,
                    tokens.symbol,
                    markets.question
                FROM market_trades
                JOIN markets ON markets.id = market_trades.market_id
                JOIN tokens ON tokens.mint = markets.token_mint
                WHERE market_trades.user_id = ?
                ORDER BY market_trades.id DESC
                """,
                [user_id],
            ).fetchall()
            return [
                {
                    "trade_id": row["id"],
                    "market_id": row["market_id"],
                    "mint": row["mint"],
                    "symbol": row["symbol"],
                    "question": row["question"],
                    "outcome": row["outcome"],
                    "side": row["side"],
                    "amount_usdc": format_usdc_amount(row["cash_amount_atomic"]),
                    "share_amount": format_usdc_amount(row["share_amount_atomic"]),
                    "before_yes_price_usd": row["before_yes_price"],
                    "before_no_price_usd": row["before_no_price"],
                    "after_yes_price_usd": row["after_yes_price"],
                    "after_no_price_usd": row["after_no_price"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def ensure_market_liquidity_account(
        self,
        market_id: int,
        *,
        owner_wallet_address: str,
        token_account_address: str,
    ) -> dict:
        timestamp = utc_now()
        with connect_database(self._database_path) as connection:
            market = connection.execute("SELECT id FROM markets WHERE id = ?", [market_id]).fetchone()
            if market is None:
                raise LookupError(f"Unknown market id: {market_id}")

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
                ON CONFLICT(market_id) DO UPDATE SET
                    owner_wallet_address = excluded.owner_wallet_address,
                    token_account_address = excluded.token_account_address,
                    ata_initialized_at = excluded.ata_initialized_at,
                    updated_at = excluded.updated_at
                """,
                [market_id, owner_wallet_address, token_account_address, timestamp, timestamp, timestamp],
            )
            connection.execute(
                """
                UPDATE markets
                SET liquidity_deposit_address = ?, updated_at = ?
                WHERE id = ?
                """,
                [token_account_address, timestamp, market_id],
            )
            row = connection.execute(
                """
                SELECT *
                FROM market_liquidity_accounts
                WHERE market_id = ?
                """,
                [market_id],
            ).fetchone()
            return self._serialize_market_liquidity_account(row)

    def ensure_missing_market_liquidity_accounts(self, treasury) -> list[dict]:
        created_accounts: list[dict] = []
        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM markets
                WHERE state IN ('awaiting_liquidity', 'open', 'halted')
                  AND liquidity_deposit_address IS NULL
                ORDER BY
                    CASE state
                        WHEN 'open' THEN 0
                        WHEN 'halted' THEN 1
                        ELSE 2
                    END,
                    id ASC
                """
            ).fetchall()
        for row in rows:
            addresses = treasury.ensure_market_liquidity_account(row["id"])
            created_accounts.append(
                self.ensure_market_liquidity_account(
                    row["id"],
                    owner_wallet_address=addresses.owner_wallet_address,
                    token_account_address=addresses.token_account_address,
                )
            )
        return created_accounts

    def list_market_liquidity_accounts(self) -> list[dict]:
        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT market_liquidity_accounts.*
                FROM market_liquidity_accounts
                JOIN markets ON markets.id = market_liquidity_accounts.market_id
                WHERE markets.state IN ('awaiting_liquidity', 'open', 'halted')
                ORDER BY market_liquidity_accounts.market_id ASC
                """
            ).fetchall()
            return [self._serialize_market_liquidity_account(row) for row in rows]

    def record_market_liquidity_credit(
        self,
        *,
        market_id: int,
        amount_atomic: int,
        observed_balance_after_atomic: int,
        credited_at: str,
    ) -> dict:
        with connect_database(self._database_path) as connection:
            market_row = connection.execute("SELECT * FROM markets WHERE id = ?", [market_id]).fetchone()
            if market_row is None:
                raise LookupError(f"Unknown market id: {market_id}")
            if market_row["state"] not in ACTIVE_MARKET_STATES:
                raise ValueError("Cannot add liquidity to a resolved market.")

            connection.execute(
                """
                INSERT INTO market_liquidity_deposits (
                    market_id,
                    amount_atomic,
                    observed_balance_after_atomic,
                    credited_at
                )
                VALUES (?, ?, ?, ?)
                """,
                [market_id, amount_atomic, observed_balance_after_atomic, credited_at],
            )
            connection.execute(
                """
                UPDATE market_liquidity_accounts
                SET observed_balance_atomic = ?, updated_at = ?
                WHERE market_id = ?
                """,
                [observed_balance_after_atomic, credited_at, market_id],
            )
            yes_price_usd = self._apply_liquidity_credit(
                connection,
                market_id=market_id,
                amount_atomic=amount_atomic,
                credited_at=credited_at,
            )

            return {
                "market_id": market_id,
                "amount_usdc": format_usdc_amount(amount_atomic),
                "credited_at": credited_at,
                "observed_balance_after_usdc": format_usdc_amount(observed_balance_after_atomic),
                "yes_price_usd": format_decimal(yes_price_usd),
                "no_price_usd": format_decimal(ONE - yes_price_usd),
            }

    def seed_top_markets_by_market_cap(
        self,
        *,
        amount_atomic: int,
        limit: int = 10,
        recorded_at: str | None = None,
    ) -> list[dict]:
        credited_at = recorded_at or utc_now()
        week_start = self._week_start(credited_at)

        with connect_database(self._database_path) as connection:
            candidate_rows = connection.execute(
                """
                WITH ranked_markets AS (
                    SELECT
                        markets.id,
                        markets.token_mint,
                        tokens.symbol,
                        token_metrics_snapshots.underlying_market_cap_usd
                    FROM tokens
                    JOIN markets
                      ON markets.id = (
                        SELECT current_market.id
                        FROM markets AS current_market
                        WHERE current_market.token_mint = tokens.mint
                          AND current_market.state IN ('awaiting_liquidity', 'open', 'halted')
                        ORDER BY current_market.sequence_number DESC
                        LIMIT 1
                      )
                    JOIN token_metrics_snapshots
                      ON token_metrics_snapshots.token_mint = tokens.mint
                     AND token_metrics_snapshots.captured_at = (
                        SELECT latest_metrics.captured_at
                        FROM token_metrics_snapshots AS latest_metrics
                        WHERE latest_metrics.token_mint = tokens.mint
                        ORDER BY latest_metrics.captured_at DESC
                        LIMIT 1
                     )
                    WHERE token_metrics_snapshots.underlying_market_cap_usd IS NOT NULL
                    ORDER BY CAST(token_metrics_snapshots.underlying_market_cap_usd AS REAL) DESC, markets.id ASC
                    LIMIT ?
                )
                SELECT
                    ranked_markets.id,
                    ranked_markets.token_mint,
                    ranked_markets.symbol,
                    ranked_markets.underlying_market_cap_usd
                FROM ranked_markets
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM market_liquidity_seed_events
                    WHERE market_liquidity_seed_events.market_id = ranked_markets.id
                      AND market_liquidity_seed_events.week_start = ?
                )
                ORDER BY CAST(ranked_markets.underlying_market_cap_usd AS REAL) DESC, ranked_markets.id ASC
                """,
                [limit, week_start],
            ).fetchall()

            seeded_markets: list[dict] = []
            for row in candidate_rows:
                debt_entry = connection.execute(
                    """
                    INSERT INTO treasury_debt_entries (
                        market_id,
                        amount_atomic,
                        entry_type,
                        note,
                        created_at
                    )
                    VALUES (?, ?, 'weekly_market_seed', ?, ?)
                    RETURNING id
                    """,
                    [
                        row["id"],
                        amount_atomic,
                        f"Debt-funded weekly auto-seed for {row['symbol']} current market.",
                        credited_at,
                    ],
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO market_liquidity_seed_events (
                        market_id,
                        week_start,
                        amount_atomic,
                        treasury_debt_entry_id,
                        credited_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [row["id"], week_start, amount_atomic, debt_entry["id"], credited_at],
                )
                yes_price_usd = self._apply_liquidity_credit(
                    connection,
                    market_id=row["id"],
                    amount_atomic=amount_atomic,
                    credited_at=credited_at,
                )
                seeded_markets.append(
                    {
                        "market_id": row["id"],
                        "mint": row["token_mint"],
                        "symbol": row["symbol"],
                        "week_start": week_start,
                        "amount_usdc": format_usdc_amount(amount_atomic),
                        "treasury_debt_usdc": format_usdc_amount(amount_atomic),
                        "yes_price_usd": format_decimal(yes_price_usd),
                        "no_price_usd": format_decimal(ONE - yes_price_usd),
                    }
                )

            return seeded_markets

    def record_treasury_funding(
        self,
        *,
        amount_atomic: int,
        funded_at: str | None = None,
        note: str | None = None,
    ) -> dict:
        recorded_at = funded_at or utc_now()

        with connect_database(self._database_path) as connection:
            outstanding_debt_atomic = self._outstanding_treasury_debt_atomic(connection)
            if amount_atomic > outstanding_debt_atomic:
                raise ValueError("Treasury funding exceeds outstanding seed debt.")

            connection.execute(
                """
                INSERT INTO treasury_debt_entries (
                    market_id,
                    amount_atomic,
                    entry_type,
                    note,
                    created_at
                )
                VALUES (NULL, ?, 'treasury_funding', ?, ?)
                """,
                [
                    -amount_atomic,
                    note or "Recorded operator treasury funding against auto-seed debt.",
                    recorded_at,
                ],
            )
            remaining_debt_atomic = outstanding_debt_atomic - amount_atomic

        return {
            "funded_amount_usdc": format_usdc_amount(amount_atomic),
            "remaining_debt_usdc": format_usdc_amount(remaining_debt_atomic),
            "recorded_at": recorded_at,
        }

    def get_outstanding_treasury_debt_usdc(self) -> str:
        with connect_database(self._database_path) as connection:
            return format_usdc_amount(self._outstanding_treasury_debt_atomic(connection))

    def capture_hourly_snapshots(
        self,
        price_client: SettlementPriceClient,
        *,
        captured_at: str | None = None,
    ) -> list[dict]:
        captured_timestamp = captured_at or utc_now()
        captured_time = self._parse_timestamp(captured_timestamp)
        finalized_hour = self._snapshot_hour(captured_time)
        captured_rows: list[dict] = []

        with connect_database(self._database_path) as connection:
            market_rows = connection.execute(
                """
                SELECT markets.id, markets.token_mint, markets.market_start, markets.state
                FROM markets
                WHERE markets.state IN ('open', 'halted')
                ORDER BY markets.id ASC
                """
            ).fetchall()

            for market_row in market_rows:
                market_start = market_row["market_start"]
                if market_start is None:
                    raise ValueError(f"Market {market_row['id']} is missing market_start and cannot be snapshotted.")

                market_start_time = self._parse_timestamp(market_start)
                if market_start_time > captured_time:
                    continue
                next_snapshot_hour = self._next_snapshot_hour(
                    market_start_time,
                    latest_snapshot=self._latest_snapshot_row(connection, market_row["id"]),
                )
                if next_snapshot_hour > finalized_hour:
                    continue

                # The settlement series intentionally uses the full trailing 24h token window,
                # including pre-market prints for newly opened markets.
                window_start_time = next_snapshot_hour - timedelta(hours=24)
                try:
                    reference_price = price_client.get_rolling_median_price(
                        market_row["token_mint"],
                        start_at=window_start_time.isoformat(),
                        end_at=next_snapshot_hour.isoformat(),
                    )
                except ValueError as error:
                    logger.warning(
                        "Skipping snapshot for market {} ({}) at {} because settlement prices are unavailable: {}",
                        market_row["id"],
                        market_row["token_mint"],
                        next_snapshot_hour.isoformat(),
                        error,
                    )
                    continue

                latest_snapshot = self._latest_snapshot_row(connection, market_row["id"])
                ath_price = reference_price
                ath_timestamp = next_snapshot_hour.isoformat()
                if latest_snapshot is not None:
                    previous_ath = parse_decimal(latest_snapshot["ath_price_usd"])
                    if previous_ath >= reference_price:
                        ath_price = previous_ath
                        ath_timestamp = latest_snapshot["ath_timestamp"]
                threshold_price = ath_price * self._threshold_fraction
                drawdown_fraction = Decimal("0") if ath_price == 0 else ONE - (reference_price / ath_price)

                connection.execute(
                    """
                    INSERT INTO market_snapshots (
                        market_id,
                        snapshot_hour,
                        reference_price_usd,
                        pair_count,
                        ath_price_usd,
                        ath_timestamp,
                        drawdown_fraction,
                        threshold_price_usd,
                        captured_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(market_id, snapshot_hour) DO UPDATE SET
                        reference_price_usd = excluded.reference_price_usd,
                        pair_count = excluded.pair_count,
                        ath_price_usd = excluded.ath_price_usd,
                        ath_timestamp = excluded.ath_timestamp,
                        drawdown_fraction = excluded.drawdown_fraction,
                        threshold_price_usd = excluded.threshold_price_usd,
                        captured_at = excluded.captured_at
                    """,
                    [
                        market_row["id"],
                        next_snapshot_hour.isoformat(),
                        str(reference_price),
                        1,
                        str(ath_price),
                        ath_timestamp,
                        str(drawdown_fraction),
                        str(threshold_price),
                        captured_timestamp,
                    ],
                )
                connection.execute(
                    "UPDATE markets SET state = 'open', updated_at = ? WHERE id = ?",
                    [captured_timestamp, market_row["id"]],
                )
                captured_rows.append(
                    {
                        "market_id": market_row["id"],
                        "state": "open",
                        "reference_price_usd": format_decimal(reference_price),
                        "ath_price_usd": format_decimal(ath_price),
                        "threshold_price_usd": format_decimal(threshold_price),
                    }
                )
        return captured_rows

    def resolve_markets(self, *, catalog, treasury=None, resolved_at: str | None = None) -> list[dict]:
        resolution_timestamp = resolved_at or utc_now()
        resolution_time = self._parse_timestamp(resolution_timestamp)
        resolved_markets: list[dict] = []

        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    markets.id,
                    markets.token_mint,
                    markets.expiry,
                    markets.state,
                    market_snapshots.snapshot_hour,
                    market_snapshots.reference_price_usd,
                    market_snapshots.ath_timestamp,
                    market_snapshots.threshold_price_usd
                FROM markets
                LEFT JOIN market_snapshots
                  ON market_snapshots.market_id = markets.id
                WHERE markets.state IN ('open', 'halted')
                ORDER BY markets.id ASC, market_snapshots.snapshot_hour ASC
                """
            ).fetchall()

            market_rows: dict[int, sqlite3.Row] = {}
            snapshot_rows: dict[int, list[sqlite3.Row]] = {}
            for row in rows:
                market_rows.setdefault(row["id"], row)
                if row["snapshot_hour"] is not None:
                    snapshot_rows.setdefault(row["id"], []).append(row)

            markets_to_resolve: list[tuple[int, str, str]] = []
            for market_id, row in market_rows.items():
                if row["expiry"] is None:
                    continue

                expiry_time = self._parse_timestamp(row["expiry"])
                for snapshot_row in snapshot_rows.get(market_id, []):
                    snapshot_time = self._parse_timestamp(snapshot_row["snapshot_hour"])
                    if snapshot_time > self._parse_timestamp(snapshot_row["ath_timestamp"]) and snapshot_time <= expiry_time:
                        threshold_price = parse_decimal(snapshot_row["threshold_price_usd"])
                        reference_price = parse_decimal(snapshot_row["reference_price_usd"])
                        if reference_price <= threshold_price:
                            markets_to_resolve.append((market_id, "resolved_yes", snapshot_row["snapshot_hour"]))
                            break
                else:
                    if resolution_time >= expiry_time:
                        markets_to_resolve.append((market_id, "resolved_no", resolution_timestamp))

        for market_id, outcome_state, market_resolved_at in markets_to_resolve:
            self._settle_market(catalog, market_id, outcome_state, market_resolved_at)
            resolved_markets.append(
                {
                    "market_id": market_id,
                    "state": outcome_state,
                    "resolved_at": market_resolved_at,
                }
            )

        if treasury is not None:
            self.ensure_missing_market_liquidity_accounts(treasury)
            treasury.sweep_market_revenue(self, limit=max(len(resolved_markets), 1))

        return resolved_markets

    def list_pending_revenue_sweeps(self, *, limit: int) -> list[dict]:
        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM market_revenue_sweeps
                WHERE state = 'pending'
                ORDER BY market_id ASC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_revenue_sweep_completed(
        self,
        *,
        market_id: int,
        destination_token_account_address: str,
        onchain_amount_atomic: int,
        broadcast_signature: str,
        completed_at: str,
    ) -> None:
        with connect_database(self._database_path) as connection:
            connection.execute(
                """
                UPDATE market_revenue_sweeps
                SET
                    state = 'completed',
                    destination_token_account_address = ?,
                    onchain_amount_atomic = ?,
                    broadcast_signature = ?,
                    completed_at = ?
                WHERE market_id = ?
                """,
                [destination_token_account_address, onchain_amount_atomic, broadcast_signature, completed_at, market_id],
            )

    def mark_revenue_sweep_failed(self, *, market_id: int, failure_reason: str, failed_at: str) -> None:
        with connect_database(self._database_path) as connection:
            connection.execute(
                """
                UPDATE market_revenue_sweeps
                SET state = 'failed', failure_reason = ?, failed_at = ?
                WHERE market_id = ?
                """,
                [failure_reason, failed_at, market_id],
            )

    def _settle_market(self, catalog, market_id: int, outcome_state: str, resolved_at: str) -> None:
        winning_outcome = "yes" if outcome_state == "resolved_yes" else "no"
        winning_column = "yes_shares_atomic" if winning_outcome == "yes" else "no_shares_atomic"

        with connect_database(self._database_path) as connection:
            pool = self._load_pool(connection, market_id)
            positions = connection.execute(
                f"""
                SELECT user_id, {winning_column} AS winning_shares_atomic
                FROM market_positions
                WHERE market_id = ?
                  AND {winning_column} > 0
                ORDER BY user_id ASC
                """,
                [market_id],
            ).fetchall()
            total_payout_atomic = sum(row["winning_shares_atomic"] for row in positions)
            if total_payout_atomic > pool.cash_backing_atomic:
                raise RuntimeError(f"Market {market_id} is undercollateralized at resolution.")

            for position in positions:
                connection.execute(
                    """
                    INSERT INTO market_payouts (
                        market_id,
                        user_id,
                        winning_outcome,
                        winning_shares_atomic,
                        payout_atomic,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        market_id,
                        position["user_id"],
                        winning_outcome,
                        position["winning_shares_atomic"],
                        position["winning_shares_atomic"],
                        resolved_at,
                    ],
                )
                self._insert_ledger_entry(
                    connection,
                    user_id=position["user_id"],
                    entry_type="market_payout",
                    amount_atomic=position["winning_shares_atomic"],
                    reference_type="market_payout",
                    reference_id=str(market_id),
                    note=f"Resolved {winning_outcome.upper()} payout for market {market_id}.",
                    created_at=resolved_at,
                )

            connection.execute(
                """
                UPDATE market_positions
                SET yes_shares_atomic = 0, no_shares_atomic = 0, updated_at = ?
                WHERE market_id = ?
                """,
                [resolved_at, market_id],
            )

            revenue_atomic = pool.cash_backing_atomic - total_payout_atomic
            liquidity_account = connection.execute(
                """
                SELECT token_account_address
                FROM market_liquidity_accounts
                WHERE market_id = ?
                """,
                [market_id],
            ).fetchone()
            sweep_state = "pending" if liquidity_account is not None else "recorded"
            connection.execute(
                """
                INSERT INTO market_revenue_sweeps (
                    market_id,
                    amount_atomic,
                    source_token_account_address,
                    destination_token_account_address,
                    onchain_amount_atomic,
                    state,
                    broadcast_signature,
                    recorded_at,
                    completed_at,
                    failed_at,
                    failure_reason
                )
                VALUES (?, ?, ?, NULL, NULL, ?, NULL, ?, NULL, NULL, NULL)
                """,
                [
                    market_id,
                    revenue_atomic,
                    None if liquidity_account is None else liquidity_account["token_account_address"],
                    sweep_state,
                    resolved_at,
                ],
            )
            connection.execute(
                """
                UPDATE market_pools
                SET cash_backing_atomic = 0, updated_at = ?
                WHERE market_id = ?
                """,
                [resolved_at, market_id],
            )

        catalog.resolve_market(market_id, outcome_state, resolved_at=resolved_at)

    def _quote_trade(
        self,
        connection: sqlite3.Connection,
        market_row: sqlite3.Row,
        pool: WeightedPoolState,
        outcome: str,
        side: str,
        amount_atomic: int,
    ) -> dict:
        if outcome not in {"yes", "no"}:
            raise ValueError("Outcome must be 'yes' or 'no'.")
        if side not in {"buy", "sell"}:
            raise ValueError("Side must be 'buy' or 'sell'.")
        if amount_atomic <= 0:
            raise ValueError("Trade amount must be positive.")

        before_yes_price = yes_price(pool)
        before_no_price = no_price(pool)

        if side == "buy":
            cash_amount_atomic = amount_atomic
            share_amount_atomic, pool_after = self._quote_buy(pool, outcome, cash_amount_atomic)
        else:
            requested_share_amount_atomic = amount_atomic
            cash_amount_atomic, share_amount_atomic, pool_after = self._quote_sell(
                pool,
                outcome,
                requested_share_amount_atomic,
            )
            if share_amount_atomic == 0:
                raise ValueError("Sell amount is too small to produce any USDC output.")

        return {
            "market_id": market_row["id"],
            "token_mint": market_row["token_mint"],
            "symbol": market_row["symbol"],
            "outcome": outcome,
            "side": side,
            "amount_usdc": format_usdc_amount(cash_amount_atomic),
            "share_amount": format_usdc_amount(share_amount_atomic),
            "cash_amount_atomic": cash_amount_atomic,
            "share_amount_atomic": share_amount_atomic,
            "average_price_usdc": format_decimal(Decimal(cash_amount_atomic) / Decimal(share_amount_atomic)),
            "requested_share_amount": None if side == "buy" else format_usdc_amount(requested_share_amount_atomic),
            "unfilled_share_amount": None if side == "buy" else format_usdc_amount(requested_share_amount_atomic - share_amount_atomic),
            "before_yes_price_usd": format_decimal(before_yes_price),
            "before_no_price_usd": format_decimal(before_no_price),
            "after_yes_price_usd": format_decimal(yes_price(pool_after)),
            "after_no_price_usd": format_decimal(no_price(pool_after)),
            "pool_after": pool_after,
        }

    @staticmethod
    def _quote_buy(pool: WeightedPoolState, outcome: str, amount_atomic: int) -> tuple[int, WeightedPoolState]:
        # Buying with cash mints complete sets off-pool, then swaps the opposite leg through the pool.
        if outcome == "yes":
            swap_out_atomic = amount_out_given_in(
                reserve_in_atomic=pool.no_reserve_atomic,
                reserve_out_atomic=pool.yes_reserve_atomic,
                weight_in=pool.no_weight,
                weight_out=pool.yes_weight,
                amount_in_atomic=amount_atomic,
            )
            share_amount_atomic = amount_atomic + swap_out_atomic
            return share_amount_atomic, WeightedPoolState(
                yes_reserve_atomic=pool.yes_reserve_atomic - swap_out_atomic,
                no_reserve_atomic=pool.no_reserve_atomic + amount_atomic,
                yes_weight=pool.yes_weight,
                no_weight=pool.no_weight,
                cash_backing_atomic=pool.cash_backing_atomic + amount_atomic,
                total_liquidity_atomic=pool.total_liquidity_atomic,
            )

        swap_out_atomic = amount_out_given_in(
            reserve_in_atomic=pool.yes_reserve_atomic,
            reserve_out_atomic=pool.no_reserve_atomic,
            weight_in=pool.yes_weight,
            weight_out=pool.no_weight,
            amount_in_atomic=amount_atomic,
        )
        share_amount_atomic = amount_atomic + swap_out_atomic
        return share_amount_atomic, WeightedPoolState(
            yes_reserve_atomic=pool.yes_reserve_atomic + amount_atomic,
            no_reserve_atomic=pool.no_reserve_atomic - swap_out_atomic,
            yes_weight=pool.yes_weight,
            no_weight=pool.no_weight,
            cash_backing_atomic=pool.cash_backing_atomic + amount_atomic,
            total_liquidity_atomic=pool.total_liquidity_atomic,
        )

    @staticmethod
    def _quote_sell(
        pool: WeightedPoolState,
        outcome: str,
        requested_share_amount_atomic: int,
    ) -> tuple[int, int, WeightedPoolState]:
        cash_amount_atomic, share_amount_atomic = MarketStore._max_cash_out_for_share_sell(
            pool,
            outcome,
            requested_share_amount_atomic,
        )
        swap_input_atomic = share_amount_atomic - cash_amount_atomic

        if outcome == "yes":
            return cash_amount_atomic, share_amount_atomic, WeightedPoolState(
                yes_reserve_atomic=pool.yes_reserve_atomic + swap_input_atomic,
                no_reserve_atomic=pool.no_reserve_atomic - cash_amount_atomic,
                yes_weight=pool.yes_weight,
                no_weight=pool.no_weight,
                cash_backing_atomic=pool.cash_backing_atomic - cash_amount_atomic,
                total_liquidity_atomic=pool.total_liquidity_atomic,
            )

        return cash_amount_atomic, share_amount_atomic, WeightedPoolState(
            yes_reserve_atomic=pool.yes_reserve_atomic - cash_amount_atomic,
            no_reserve_atomic=pool.no_reserve_atomic + swap_input_atomic,
            yes_weight=pool.yes_weight,
            no_weight=pool.no_weight,
            cash_backing_atomic=pool.cash_backing_atomic - cash_amount_atomic,
            total_liquidity_atomic=pool.total_liquidity_atomic,
        )

    @staticmethod
    def _max_cash_out_for_share_sell(
        pool: WeightedPoolState,
        outcome: str,
        requested_share_amount_atomic: int,
    ) -> tuple[int, int]:
        if requested_share_amount_atomic > pool.cash_backing_atomic:
            upper_bound = pool.cash_backing_atomic
        else:
            upper_bound = requested_share_amount_atomic

        low_cash_out = 0
        high_cash_out = upper_bound
        best_cash_out = 0
        best_share_amount = 0

        while low_cash_out <= high_cash_out:
            candidate_cash_out = (low_cash_out + high_cash_out) // 2
            if candidate_cash_out == 0:
                required_share_amount = 0
            elif outcome == "yes":
                required_share_amount = candidate_cash_out + amount_in_given_out(
                    reserve_in_atomic=pool.yes_reserve_atomic,
                    reserve_out_atomic=pool.no_reserve_atomic,
                    weight_in=pool.yes_weight,
                    weight_out=pool.no_weight,
                    amount_out_atomic=candidate_cash_out,
                )
            else:
                required_share_amount = candidate_cash_out + amount_in_given_out(
                    reserve_in_atomic=pool.no_reserve_atomic,
                    reserve_out_atomic=pool.yes_reserve_atomic,
                    weight_in=pool.no_weight,
                    weight_out=pool.yes_weight,
                    amount_out_atomic=candidate_cash_out,
                )

            if required_share_amount <= requested_share_amount_atomic:
                best_cash_out = candidate_cash_out
                best_share_amount = required_share_amount
                low_cash_out = candidate_cash_out + 1
            else:
                high_cash_out = candidate_cash_out - 1

        return best_cash_out, best_share_amount

    def _serialize_token_card(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        pm_volume_24h_atomic: int,
    ) -> dict:
        return {
            "mint": row["mint"],
            "symbol": row["symbol"],
            "name": row["name"],
            "image_url": row["image_url"],
            "launched_at": row["launched_at"],
            "current_market": self._serialize_market(
                connection,
                row,
                pm_volume_24h_atomic=pm_volume_24h_atomic,
            ),
        }

    def _serialize_market(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        pm_volume_24h_atomic: int,
    ) -> dict:
        pool = self._load_pool(connection, row["id"], required=False)
        latest_snapshot = self._latest_snapshot_row(connection, row["id"])
        latest_token_metrics = self._latest_token_metrics_row(connection, row["token_mint"])
        yes_price_value = None if pool is None else yes_price(pool)
        yes_price_usd = None if yes_price_value is None else format_decimal(yes_price_value)
        no_price_usd = None if pool is None else format_decimal(no_price(pool))

        return {
            "id": row["id"],
            "sequence_number": row["sequence_number"],
            "question": row["question"],
            "state": row["state"],
            "market_start": row["market_start"],
            "expiry": row["expiry"],
            "liquidity_deposit_address": row["liquidity_deposit_address"],
            "resolved_at": row["resolved_at"],
            "created_at": row["created_at"],
            "yes_price_usd": yes_price_usd,
            "no_price_usd": no_price_usd,
            "chance_of_outcome_percent": None if yes_price_value is None else self._chance_of_outcome_percent(yes_price_value),
            "reference_price_usd": None if latest_snapshot is None else format_decimal(parse_decimal(latest_snapshot["reference_price_usd"])),
            "ath_price_usd": None if latest_snapshot is None else format_decimal(parse_decimal(latest_snapshot["ath_price_usd"])),
            "ath_timestamp": None if latest_snapshot is None else latest_snapshot["ath_timestamp"],
            "drawdown_fraction": None if latest_snapshot is None else format_decimal(parse_decimal(latest_snapshot["drawdown_fraction"])),
            "threshold_price_usd": None if latest_snapshot is None else format_decimal(parse_decimal(latest_snapshot["threshold_price_usd"])),
            "total_liquidity_usdc": None if pool is None else format_usdc_amount(pool.total_liquidity_atomic),
            "pm_volume_24h_usdc": format_usdc_amount(pm_volume_24h_atomic),
            "underlying_volume_24h_usd": None
            if latest_token_metrics is None or latest_token_metrics["underlying_volume_h24_usd"] is None
            else format_decimal(parse_decimal(latest_token_metrics["underlying_volume_h24_usd"])),
            "underlying_market_cap_usd": None
            if latest_token_metrics is None or latest_token_metrics["underlying_market_cap_usd"] is None
            else format_decimal(parse_decimal(latest_token_metrics["underlying_market_cap_usd"])),
        }

    @staticmethod
    def _chance_of_outcome_percent(yes_price_value: Decimal) -> str:
        return f"{format_decimal(yes_price_value * Decimal('100'))}%"

    @staticmethod
    def _sort_token_cards(token_cards: list[dict], *, sort_by: str, sort_direction: str) -> None:
        if sort_by not in TOKEN_CARD_SORT_FIELDS:
            raise ValueError(f"Unsupported token card sort field: {sort_by}")
        normalized_direction = sort_direction.lower()
        if normalized_direction not in {"asc", "desc"}:
            raise ValueError(f"Unsupported token card sort direction: {sort_direction}")

        descending = normalized_direction == "desc"
        token_cards.sort(key=lambda token_card: MarketStore._token_card_sort_key(token_card, sort_by, descending=descending))

    @staticmethod
    def _token_card_sort_key(token_card: dict, sort_by: str, *, descending: bool) -> tuple[int, Decimal]:
        current_market = token_card["current_market"]
        if sort_by == "market_liquidity":
            value = current_market["total_liquidity_usdc"]
        elif sort_by == "dump_percentage":
            value = current_market["drawdown_fraction"]
        elif sort_by == "underlying_volume":
            value = current_market["underlying_volume_24h_usd"]
        elif sort_by == "underlying_market_cap":
            value = current_market["underlying_market_cap_usd"]
        else:
            raise ValueError(f"Unsupported token card sort field: {sort_by}")

        if value is None:
            return (1, Decimal("0"))

        decimal_value = parse_decimal(value)
        return (0, -decimal_value if descending else decimal_value)

    def _recent_activity(
        self,
        connection: sqlite3.Connection,
        current_market: sqlite3.Row,
        token_updated_at: str,
    ) -> list[dict]:
        activity = [
            {
                "timestamp": current_market["created_at"],
                "summary": f"Series {current_market['sequence_number']} created in {current_market['state'].replace('_', ' ')}.",
            }
        ]
        latest_liquidity = connection.execute(
            """
            SELECT amount_atomic, credited_at, source
            FROM (
                SELECT amount_atomic, credited_at, 'deposit' AS source
                FROM market_liquidity_deposits
                WHERE market_id = ?
                UNION ALL
                SELECT amount_atomic, credited_at, 'auto_seed' AS source
                FROM market_liquidity_seed_events
                WHERE market_id = ?
            )
            ORDER BY credited_at DESC
            LIMIT 1
            """,
            [current_market["id"], current_market["id"]],
        ).fetchone()
        if latest_liquidity is not None:
            if latest_liquidity["source"] == "auto_seed":
                summary = (
                    f"Debt-funded weekly auto-seed of {format_usd_display(format_usdc_amount(latest_liquidity['amount_atomic']))} "
                    "opened or deepened the pool."
                )
            else:
                summary = (
                    f"Liquidity credit of {format_usd_display(format_usdc_amount(latest_liquidity['amount_atomic']))} "
                    "deepened the pool."
                )
            activity.append(
                {
                    "timestamp": latest_liquidity["credited_at"],
                    "summary": summary,
                }
            )
        latest_snapshot = self._latest_snapshot_row(connection, current_market["id"])
        if latest_snapshot is not None:
            activity.append(
                {
                    "timestamp": latest_snapshot["snapshot_hour"],
                    "summary": f"Latest hourly reference price is {format_usd_display(latest_snapshot['reference_price_usd'])}.",
                }
            )
        elif current_market["state"] == "awaiting_liquidity":
            activity.append(
                {
                    "timestamp": token_updated_at,
                    "summary": "Waiting for the first market liquidity deposit before the 90 day window starts.",
                }
            )
        latest_trade = connection.execute(
            """
            SELECT side, outcome, cash_amount_atomic, created_at
            FROM market_trades
            WHERE market_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            [current_market["id"]],
        ).fetchone()
        if latest_trade is not None:
            activity.append(
                {
                    "timestamp": latest_trade["created_at"],
                    "summary": (
                        f"Latest trade was a {latest_trade['side']} of nuke exposure for "
                        f"{format_usd_display(format_usdc_amount(latest_trade['cash_amount_atomic']))}."
                    ),
                }
            )
        return sorted(activity, key=lambda item: item["timestamp"], reverse=True)

    @staticmethod
    def _sum_pair_volume(pairs: list[DexScreenerPair]) -> Decimal | None:
        total_volume = Decimal("0")
        has_volume = False
        for pair in pairs:
            if pair.volume_h24_usd is None:
                continue
            total_volume += pair.volume_h24_usd
            has_volume = True
        return total_volume if has_volume else None

    @staticmethod
    def _most_liquid_pair_with_market_cap(pairs: list[DexScreenerPair]) -> DexScreenerPair | None:
        eligible_pairs = [pair for pair in pairs if pair.market_cap_usd is not None]
        if not eligible_pairs:
            return None
        return max(eligible_pairs, key=lambda pair: pair.liquidity_usd)

    @staticmethod
    def _list_current_market_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT
                tokens.mint,
                tokens.symbol,
                tokens.name,
                tokens.image_url,
                tokens.launched_at,
                markets.*
            FROM tokens
            JOIN markets
              ON markets.id = (
                SELECT current_market.id
                FROM markets AS current_market
                WHERE current_market.token_mint = tokens.mint
                  AND current_market.state IN ('awaiting_liquidity', 'open', 'halted')
                ORDER BY current_market.sequence_number DESC
                LIMIT 1
              )
            ORDER BY COALESCE(tokens.launched_at, tokens.created_at) DESC, tokens.symbol ASC
            """
        ).fetchall()

    @staticmethod
    def _market_volume_24h_by_market_id(
        connection: sqlite3.Connection,
        market_ids: list[int],
    ) -> dict[int, int]:
        unique_market_ids = list(dict.fromkeys(market_ids))
        if not unique_market_ids:
            return {}

        reference_time = datetime.fromisoformat(utc_now())
        window_start = (reference_time - timedelta(hours=24)).isoformat()
        placeholders = ", ".join("?" for _ in unique_market_ids)
        rows = connection.execute(
            f"""
            SELECT
                market_id,
                COALESCE(SUM(cash_amount_atomic), 0) AS pm_volume_24h_atomic
            FROM market_trades
            WHERE market_id IN ({placeholders})
              AND created_at >= ?
              AND created_at <= ?
            GROUP BY market_id
            """,
            [*unique_market_ids, window_start, reference_time.isoformat()],
        ).fetchall()
        return {row["market_id"]: row["pm_volume_24h_atomic"] for row in rows}

    def _latest_token_metrics_row(self, connection: sqlite3.Connection, token_mint: str) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT *
            FROM token_metrics_snapshots
            WHERE token_mint = ?
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            [token_mint],
        ).fetchone()

    def _load_tradeable_market(self, connection: sqlite3.Connection, market_id: int) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT markets.*, tokens.symbol
            FROM markets
            JOIN tokens ON tokens.mint = markets.token_mint
            WHERE markets.id = ?
            """,
            [market_id],
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown market id: {market_id}")
        if row["state"] != "open":
            raise ValueError("Market is not open for trading.")
        return row

    def _load_pool(
        self,
        connection: sqlite3.Connection,
        market_id: int,
        *,
        required: bool = True,
    ) -> WeightedPoolState | None:
        row = connection.execute(
            """
            SELECT *
            FROM market_pools
            WHERE market_id = ?
            """,
            [market_id],
        ).fetchone()
        if row is None:
            if required:
                raise ValueError("Market has no active pool.")
            return None
        return WeightedPoolState(
            yes_reserve_atomic=row["yes_reserve_atomic"],
            no_reserve_atomic=row["no_reserve_atomic"],
            yes_weight=parse_decimal(row["yes_weight"]),
            no_weight=parse_decimal(row["no_weight"]),
            cash_backing_atomic=row["cash_backing_atomic"],
            total_liquidity_atomic=row["total_liquidity_atomic"],
        )

    def _apply_liquidity_credit(
        self,
        connection: sqlite3.Connection,
        *,
        market_id: int,
        amount_atomic: int,
        credited_at: str,
    ) -> Decimal:
        market_row = connection.execute("SELECT * FROM markets WHERE id = ?", [market_id]).fetchone()
        if market_row is None:
            raise LookupError(f"Unknown market id: {market_id}")
        if market_row["state"] not in ACTIVE_MARKET_STATES:
            raise ValueError("Cannot add liquidity to a resolved market.")

        pool = self._load_pool(connection, market_id, required=False)
        if pool is None:
            opened_at = self._parse_timestamp(credited_at)
            expiry = (opened_at + self._market_duration).isoformat()
            connection.execute(
                """
                INSERT INTO market_pools (
                    market_id,
                    yes_reserve_atomic,
                    no_reserve_atomic,
                    yes_weight,
                    no_weight,
                    cash_backing_atomic,
                    total_liquidity_atomic,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, '0.5', '0.5', ?, ?, ?, ?)
                """,
                [market_id, amount_atomic, amount_atomic, amount_atomic, amount_atomic, credited_at, credited_at],
            )
            connection.execute(
                """
                UPDATE markets
                SET state = 'open', market_start = ?, expiry = ?, updated_at = ?
                WHERE id = ?
                """,
                [credited_at, expiry, credited_at, market_id],
            )
            return Decimal("0.5")

        preserved_yes_price = yes_price(pool)
        updated_yes_weight, updated_no_weight = retuned_weights_for_equal_liquidity(
            yes_reserve_atomic=pool.yes_reserve_atomic,
            no_reserve_atomic=pool.no_reserve_atomic,
            equal_liquidity_atomic=amount_atomic,
            preserved_yes_price=preserved_yes_price,
        )
        connection.execute(
            """
            UPDATE market_pools
            SET
                yes_reserve_atomic = yes_reserve_atomic + ?,
                no_reserve_atomic = no_reserve_atomic + ?,
                yes_weight = ?,
                no_weight = ?,
                cash_backing_atomic = cash_backing_atomic + ?,
                total_liquidity_atomic = total_liquidity_atomic + ?,
                updated_at = ?
            WHERE market_id = ?
            """,
            [
                amount_atomic,
                amount_atomic,
                str(updated_yes_weight),
                str(updated_no_weight),
                amount_atomic,
                amount_atomic,
                credited_at,
                market_id,
            ],
        )
        return preserved_yes_price

    @staticmethod
    def _update_pool(
        connection: sqlite3.Connection,
        market_id: int,
        pool: WeightedPoolState,
        updated_at: str,
    ) -> None:
        connection.execute(
            """
            UPDATE market_pools
            SET
                yes_reserve_atomic = ?,
                no_reserve_atomic = ?,
                yes_weight = ?,
                no_weight = ?,
                cash_backing_atomic = ?,
                total_liquidity_atomic = ?,
                updated_at = ?
            WHERE market_id = ?
            """,
            [
                pool.yes_reserve_atomic,
                pool.no_reserve_atomic,
                str(pool.yes_weight),
                str(pool.no_weight),
                pool.cash_backing_atomic,
                pool.total_liquidity_atomic,
                updated_at,
                market_id,
            ],
        )

    @staticmethod
    def _insert_ledger_entry(
        connection: sqlite3.Connection,
        *,
        user_id: int,
        entry_type: str,
        amount_atomic: int,
        reference_type: str,
        reference_id: str,
        note: str,
        created_at: str,
    ) -> int:
        row = connection.execute(
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
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            [user_id, entry_type, amount_atomic, reference_type, reference_id, note, created_at],
        ).fetchone()
        return row["id"]

    @staticmethod
    def _public_quote(quote: dict) -> dict:
        response = dict(quote)
        response.pop("share_amount_atomic", None)
        response.pop("pool_after", None)
        return response

    @staticmethod
    def _available_balance_atomic(connection: sqlite3.Connection, user_id: int) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(amount_atomic), 0) AS balance_atomic
            FROM ledger_entries
            WHERE user_id = ?
            """,
            [user_id],
        ).fetchone()
        return row["balance_atomic"]

    def _adjust_position(
        self,
        connection: sqlite3.Connection,
        user_id: int,
        market_id: int,
        outcome: str,
        delta_atomic: int,
    ) -> None:
        timestamp = utc_now()
        connection.execute(
            """
            INSERT INTO market_positions (
                user_id,
                market_id,
                yes_shares_atomic,
                no_shares_atomic,
                created_at,
                updated_at
            )
            VALUES (?, ?, 0, 0, ?, ?)
            ON CONFLICT(user_id, market_id) DO NOTHING
            """,
            [user_id, market_id, timestamp, timestamp],
        )
        column = "yes_shares_atomic" if outcome == "yes" else "no_shares_atomic"
        connection.execute(
            f"""
            UPDATE market_positions
            SET {column} = {column} + ?, updated_at = ?
            WHERE user_id = ? AND market_id = ?
            """,
            [delta_atomic, timestamp, user_id, market_id],
        )

    @staticmethod
    def _require_position(
        connection: sqlite3.Connection,
        user_id: int,
        market_id: int,
        outcome: str,
        required_shares_atomic: int,
    ) -> None:
        column = "yes_shares_atomic" if outcome == "yes" else "no_shares_atomic"
        row = connection.execute(
            f"""
            SELECT {column} AS shares_atomic
            FROM market_positions
            WHERE user_id = ? AND market_id = ?
            """,
            [user_id, market_id],
        ).fetchone()
        current_shares = 0 if row is None else row["shares_atomic"]
        if required_shares_atomic > current_shares:
            raise ValueError(f"Sell amount exceeds available {outcome.upper()} shares.")

    @staticmethod
    def _latest_snapshot_row(connection: sqlite3.Connection, market_id: int) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT *
            FROM market_snapshots
            WHERE market_id = ?
            ORDER BY snapshot_hour DESC
            LIMIT 1
            """,
            [market_id],
        ).fetchone()

    @staticmethod
    def _next_snapshot_hour(market_start_time: datetime, *, latest_snapshot: sqlite3.Row | None) -> datetime:
        if latest_snapshot is None:
            return MarketStore._snapshot_hour(market_start_time)
        return MarketStore._parse_timestamp(latest_snapshot["snapshot_hour"]) + timedelta(hours=1)

    @staticmethod
    def _snapshot_hour(timestamp: datetime | str) -> datetime:
        dt = timestamp if isinstance(timestamp, datetime) else MarketStore._parse_timestamp(timestamp)
        return dt.replace(minute=0, second=0, microsecond=0)

    @staticmethod
    def _parse_timestamp(timestamp: str) -> datetime:
        dt = datetime.fromisoformat(timestamp)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    @staticmethod
    def _serialize_market_liquidity_account(row: sqlite3.Row) -> dict:
        return {
            "market_id": row["market_id"],
            "owner_wallet_address": row["owner_wallet_address"],
            "token_account_address": row["token_account_address"],
            "observed_balance_atomic": row["observed_balance_atomic"],
            "observed_balance_usdc": format_usdc_amount(row["observed_balance_atomic"]),
            "ata_initialized_at": row["ata_initialized_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _outstanding_treasury_debt_atomic(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(amount_atomic), 0) AS balance_atomic
            FROM treasury_debt_entries
            """
        ).fetchone()
        return row["balance_atomic"]

    def _week_start(self, timestamp: str) -> str:
        current_time = self._parse_timestamp(timestamp).astimezone(UTC)
        return (current_time - timedelta(days=current_time.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ).isoformat()
