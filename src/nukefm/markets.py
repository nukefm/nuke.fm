from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3

from loguru import logger

from .amounts import format_usdc_amount
from .catalog import ACTIVE_MARKET_STATES, seed_market_question
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
    ("dump_percentage", "Nuke distance"),
    ("underlying_volume", "Underlying volume"),
    ("underlying_market_cap", "Underlying market cap"),
)
TOKEN_CARD_SORT_FIELDS = {value for value, _ in TOKEN_CARD_SORT_OPTIONS}
MARKET_CHART_INTERVAL_MINUTES = 5


class MarketStore:
    def __init__(
        self,
        database_path: Path,
        *,
        market_duration_days: int = 90,
        resolution_threshold_fraction: Decimal = Decimal("0.10"),
        rollover_lower_bound_fraction: Decimal = Decimal("0.25"),
        rollover_upper_bound_fraction: Decimal = Decimal("4.0"),
    ) -> None:
        self._database_path = database_path
        self._market_duration = timedelta(days=market_duration_days)
        self._resolution_threshold_fraction = resolution_threshold_fraction
        self._rollover_lower_bound_fraction = rollover_lower_bound_fraction
        self._rollover_upper_bound_fraction = rollover_upper_bound_fraction

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

                CREATE TABLE IF NOT EXISTS market_chart_snapshots (
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    captured_at TEXT NOT NULL,
                    underlying_price_usd TEXT NOT NULL,
                    chance_of_outcome_percent TEXT NOT NULL,
                    PRIMARY KEY(market_id, captured_at)
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
            self._ensure_market_column(connection, "starting_price_usd", "TEXT")
            self._ensure_market_column(connection, "threshold_price_usd", "TEXT")
            self._ensure_market_column(connection, "range_floor_price_usd", "TEXT")
            self._ensure_market_column(connection, "range_ceiling_price_usd", "TEXT")
            self._ensure_market_column(connection, "is_frontend_visible", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_market_column(connection, "superseded_by_market_id", "INTEGER REFERENCES markets(id) ON DELETE SET NULL")
            self._ensure_market_column(connection, "superseded_at", "TEXT")

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
            token_rows = connection.execute(
                """
                SELECT mint, symbol
                FROM tokens
                ORDER BY COALESCE(launched_at, created_at) DESC, symbol ASC
                """
            ).fetchall()
            captured_rows: list[dict] = []

            for row in token_rows:
                pairs = metrics_client.list_token_pairs(row["mint"])
                underlying_volume = self._sum_pair_volume(pairs)
                market_cap_pair = self._most_liquid_pair_with_market_cap(pairs)
                price_pair = self._most_liquid_pair_with_price(pairs)

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
                        None if price_pair is None or price_pair.price_usd is None else str(price_pair.price_usd),
                        None if price_pair is None else str(price_pair.liquidity_usd),
                    ],
                )
                if self._frontend_visible_market_row(connection, row["mint"]) is None:
                    if price_pair is None or price_pair.price_usd is None:
                        logger.warning(
                            "Skipping market creation for token {} ({}) at {} because the creation price is unavailable.",
                            row["symbol"],
                            row["mint"],
                            captured_timestamp,
                        )
                    else:
                        self._create_market(
                            connection,
                            token_mint=row["mint"],
                            symbol=row["symbol"],
                            created_at=captured_timestamp,
                            starting_price_usd=price_pair.price_usd,
                            is_frontend_visible=True,
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

    def capture_market_chart_snapshots(
        self,
        metrics_client: DexScreenerPairClient,
        *,
        captured_at: str | None = None,
    ) -> list[dict]:
        snapshot_time = self._chart_snapshot_time(captured_at or utc_now()).isoformat()

        with connect_database(self._database_path) as connection:
            captured_rows: list[dict] = []
            for row in self._list_current_market_rows(connection):
                if row["state"] not in {"open", "halted"}:
                    continue

                pool = self._load_pool(connection, row["id"], required=False)
                if pool is None:
                    raise ValueError(f"Market {row['id']} is {row['state']} but has no active pool.")

                price_pair = self._most_liquid_pair_with_price(metrics_client.list_token_pairs(row["mint"]))
                if price_pair is None or price_pair.price_usd is None:
                    logger.warning(
                        "Skipping chart snapshot for market {} ({}) at {} because the current token price is unavailable.",
                        row["id"],
                        row["mint"],
                        snapshot_time,
                    )
                    continue

                chance_of_outcome_percent = yes_price(pool) * Decimal("100")
                connection.execute(
                    """
                    INSERT INTO market_chart_snapshots (
                        market_id,
                        captured_at,
                        underlying_price_usd,
                        chance_of_outcome_percent
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(market_id, captured_at) DO UPDATE SET
                        underlying_price_usd = excluded.underlying_price_usd,
                        chance_of_outcome_percent = excluded.chance_of_outcome_percent
                    """,
                    [
                        row["id"],
                        snapshot_time,
                        str(price_pair.price_usd),
                        str(chance_of_outcome_percent),
                    ],
                )
                captured_rows.append(
                    {
                        "market_id": row["id"],
                        "captured_at": snapshot_time,
                        "underlying_price_usd": format_decimal(price_pair.price_usd),
                        "chance_of_outcome_percent": format_decimal(chance_of_outcome_percent),
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

            current_market = self._frontend_visible_market_row(connection, mint)
            if current_market is None:
                return None

            hidden_active_market_rows = connection.execute(
                """
                SELECT markets.*, tokens.symbol
                FROM markets
                JOIN tokens ON tokens.mint = markets.token_mint
                WHERE token_mint = ?
                  AND is_frontend_visible = 0
                  AND state IN ('awaiting_liquidity', 'open', 'halted')
                ORDER BY sequence_number DESC
                """,
                [mint],
            ).fetchall()
            past_market_rows = connection.execute(
                """
                SELECT markets.*, tokens.symbol
                FROM markets
                JOIN tokens ON tokens.mint = markets.token_mint
                WHERE token_mint = ?
                  AND state IN ('resolved_yes', 'resolved_no', 'void')
                ORDER BY sequence_number DESC
                """,
                [mint],
            ).fetchall()
            market_ids = [current_market["id"], *[row["id"] for row in hidden_active_market_rows], *[row["id"] for row in past_market_rows]]
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
                "hidden_active_markets": [
                    self._serialize_market(
                        connection,
                        row,
                        pm_volume_24h_atomic=pm_volume_24h_by_market_id.get(row["id"], 0),
                    )
                    for row in hidden_active_market_rows
                ],
                "past_markets": [
                    self._serialize_market(
                        connection,
                        row,
                        pm_volume_24h_atomic=pm_volume_24h_by_market_id.get(row["id"], 0),
                    )
                    for row in past_market_rows
                ],
                "current_market_chart": self._serialize_current_market_chart(connection, current_market),
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
                          AND current_market.is_frontend_visible = 1
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
                SELECT
                    markets.id,
                    markets.token_mint,
                    tokens.symbol,
                    markets.market_start,
                    markets.state,
                    markets.created_at,
                    markets.starting_price_usd,
                    markets.threshold_price_usd,
                    markets.range_floor_price_usd,
                    markets.range_ceiling_price_usd,
                    markets.is_frontend_visible
                FROM markets
                JOIN tokens ON tokens.mint = markets.token_mint
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
                starting_price = parse_decimal(market_row["starting_price_usd"])
                threshold_price = parse_decimal(market_row["threshold_price_usd"])
                price_change_fraction = Decimal("0") if starting_price == 0 else ONE - (reference_price / starting_price)

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
                        format_decimal(reference_price),
                        1,
                        format_decimal(starting_price),
                        market_row["created_at"],
                        format_decimal(price_change_fraction),
                        format_decimal(threshold_price),
                        captured_timestamp,
                    ],
                )
                connection.execute("UPDATE markets SET updated_at = ? WHERE id = ?", [captured_timestamp, market_row["id"]])

                if (
                    bool(market_row["is_frontend_visible"])
                    and self._price_outside_rollover_range(reference_price, market_row)
                    and self._frontend_visible_market_row(connection, market_row["token_mint"])["id"] == market_row["id"]
                ):
                    self._create_successor_market(
                        connection,
                        market_row=market_row,
                        starting_price_usd=reference_price,
                        created_at=next_snapshot_hour.isoformat(),
                    )
                captured_rows.append(
                    {
                        "market_id": market_row["id"],
                        "state": market_row["state"],
                        "reference_price_usd": format_decimal(reference_price),
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
                    markets.is_frontend_visible,
                    markets.starting_price_usd,
                    markets.threshold_price_usd,
                    market_snapshots.snapshot_hour,
                    market_snapshots.reference_price_usd
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
                threshold_price = parse_decimal(row["threshold_price_usd"])
                latest_price_before_expiry = parse_decimal(row["starting_price_usd"])
                for snapshot_row in snapshot_rows.get(market_id, []):
                    snapshot_time = self._parse_timestamp(snapshot_row["snapshot_hour"])
                    if snapshot_time > expiry_time:
                        continue
                    latest_price_before_expiry = parse_decimal(snapshot_row["reference_price_usd"])
                    if latest_price_before_expiry <= threshold_price:
                        markets_to_resolve.append(
                            (
                                market_id,
                                "resolved_yes",
                                snapshot_row["snapshot_hour"],
                                bool(row["is_frontend_visible"]),
                                latest_price_before_expiry,
                            )
                        )
                        break
                else:
                    if resolution_time >= expiry_time:
                        markets_to_resolve.append(
                            (
                                market_id,
                                "resolved_no",
                                resolution_timestamp,
                                bool(row["is_frontend_visible"]),
                                latest_price_before_expiry,
                            )
                        )

        for market_id, outcome_state, market_resolved_at, should_create_successor, successor_price_usd in markets_to_resolve:
            self._settle_market(
                catalog,
                market_id,
                outcome_state,
                market_resolved_at,
                successor_price_usd=successor_price_usd if should_create_successor else None,
            )
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

    def _settle_market(
        self,
        catalog,
        market_id: int,
        outcome_state: str,
        resolved_at: str,
        *,
        successor_price_usd: Decimal | None,
    ) -> None:
        winning_outcome = "yes" if outcome_state == "resolved_yes" else "no"
        winning_column = "yes_shares_atomic" if winning_outcome == "yes" else "no_shares_atomic"
        create_successor = False
        successor_token_mint = ""
        successor_symbol = ""
        next_starting_price: Decimal | None = None

        with connect_database(self._database_path) as connection:
            market_row = connection.execute(
                """
                SELECT markets.*, tokens.symbol
                FROM markets
                JOIN tokens ON tokens.mint = markets.token_mint
                WHERE markets.id = ?
                """,
                [market_id],
            ).fetchone()
            if market_row is None:
                raise LookupError(f"Unknown market id: {market_id}")
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

            create_successor = bool(market_row["is_frontend_visible"])
            successor_token_mint = market_row["token_mint"]
            successor_symbol = market_row["symbol"]
            next_starting_price = successor_price_usd or parse_decimal(market_row["starting_price_usd"])

        catalog.resolve_market(market_id, outcome_state, resolved_at=resolved_at)
        if not create_successor or next_starting_price is None:
            return

        with connect_database(self._database_path) as connection:
            successor_market_id = self._create_market(
                connection,
                token_mint=successor_token_mint,
                symbol=successor_symbol,
                created_at=resolved_at,
                starting_price_usd=next_starting_price,
                is_frontend_visible=True,
            )
            connection.execute(
                """
                UPDATE markets
                SET is_frontend_visible = 0, superseded_by_market_id = ?, superseded_at = ?, updated_at = ?
                WHERE id = ?
                """,
                [successor_market_id, resolved_at, resolved_at, market_id],
            )

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
        starting_price = parse_decimal(row["starting_price_usd"])
        threshold_price = parse_decimal(row["threshold_price_usd"])
        current_observed_price = starting_price if latest_snapshot is None else parse_decimal(latest_snapshot["reference_price_usd"])

        return {
            "id": row["id"],
            "sequence_number": row["sequence_number"],
            "question": self._market_prompt(
                symbol=row["symbol"],
                current_price_usd=current_observed_price,
                threshold_price_usd=threshold_price,
                expiry=row["expiry"],
                created_at=row["created_at"],
            ),
            "state": row["state"],
            "market_start": row["market_start"],
            "expiry": row["expiry"],
            "liquidity_deposit_address": row["liquidity_deposit_address"],
            "resolved_at": row["resolved_at"],
            "created_at": row["created_at"],
            "starting_price_usd": format_decimal(starting_price),
            "threshold_price_usd": format_decimal(threshold_price),
            "range_floor_price_usd": format_decimal(parse_decimal(row["range_floor_price_usd"])),
            "range_ceiling_price_usd": format_decimal(parse_decimal(row["range_ceiling_price_usd"])),
            "is_frontend_visible": bool(row["is_frontend_visible"]),
            "superseded_by_market_id": row["superseded_by_market_id"],
            "superseded_at": row["superseded_at"],
            "yes_price_usd": yes_price_usd,
            "no_price_usd": no_price_usd,
            "chance_of_outcome_percent": None if yes_price_value is None else self._chance_of_outcome_percent(yes_price_value),
            "reference_price_usd": format_decimal(current_observed_price),
            "remaining_drop_percent": self._remaining_drop_percent(current_observed_price, threshold_price),
            "drop_to_nuke_fraction": format_decimal(self._remaining_drop_fraction(current_observed_price, threshold_price)),
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

    def _market_prompt(
        self,
        *,
        symbol: str,
        current_price_usd: Decimal,
        threshold_price_usd: Decimal,
        expiry: str | None,
        created_at: str,
    ) -> str:
        deadline = (
            self._parse_timestamp(expiry).date().isoformat()
            if expiry is not None
            else (self._parse_timestamp(created_at) + self._market_duration).date().isoformat()
        )
        return f"Will {symbol} nuke by {self._remaining_drop_percent(current_price_usd, threshold_price_usd)} by {deadline}?"

    @staticmethod
    def _remaining_drop_fraction(current_price_usd: Decimal, threshold_price_usd: Decimal) -> Decimal:
        if current_price_usd <= 0:
            return Decimal("0")
        remaining_drop_fraction = ONE - (threshold_price_usd / current_price_usd)
        return max(Decimal("0"), remaining_drop_fraction)

    @classmethod
    def _remaining_drop_percent(cls, current_price_usd: Decimal, threshold_price_usd: Decimal) -> str:
        percent_value = cls._remaining_drop_fraction(current_price_usd, threshold_price_usd) * Decimal("100")
        return f"{format_decimal(percent_value.quantize(Decimal('0.01')))}%"

    def _serialize_current_market_chart(self, connection: sqlite3.Connection, current_market: sqlite3.Row) -> dict:
        return {
            "market_id": current_market["id"],
            "interval_minutes": MARKET_CHART_INTERVAL_MINUTES,
            "points": [
                {
                    "captured_at": row["captured_at"],
                    "underlying_price_usd": format_decimal(parse_decimal(row["underlying_price_usd"])),
                    "chance_of_outcome_percent": format_decimal(parse_decimal(row["chance_of_outcome_percent"])),
                }
                for row in self._market_chart_rows(connection, current_market["id"])
            ],
        }

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
            value = current_market["drop_to_nuke_fraction"]
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
                    "summary": f"Latest hourly token price is {format_usd_display(latest_snapshot['reference_price_usd'])}.",
                }
            )
        elif current_market["state"] == "awaiting_liquidity":
            activity.append(
                {
                    "timestamp": token_updated_at,
                    "summary": "Waiting for the first market liquidity deposit before trading opens.",
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
    def _most_liquid_pair_with_price(pairs: list[DexScreenerPair]) -> DexScreenerPair | None:
        eligible_pairs = [pair for pair in pairs if pair.price_usd is not None]
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
                  AND current_market.is_frontend_visible = 1
                  AND current_market.state IN ('awaiting_liquidity', 'open', 'halted')
                ORDER BY current_market.sequence_number DESC
                LIMIT 1
              )
            ORDER BY COALESCE(tokens.launched_at, tokens.created_at) DESC, tokens.symbol ASC
            """
        ).fetchall()

    @staticmethod
    def _frontend_visible_market_row(connection: sqlite3.Connection, token_mint: str) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT markets.*, tokens.symbol
            FROM markets
            JOIN tokens ON tokens.mint = markets.token_mint
            WHERE markets.token_mint = ?
              AND markets.is_frontend_visible = 1
              AND markets.state IN ('awaiting_liquidity', 'open', 'halted')
            ORDER BY markets.sequence_number DESC
            LIMIT 1
            """,
            [token_mint],
        ).fetchone()

    def _create_market(
        self,
        connection: sqlite3.Connection,
        *,
        token_mint: str,
        symbol: str,
        created_at: str,
        starting_price_usd: Decimal,
        is_frontend_visible: bool,
    ) -> int:
        next_sequence_number = connection.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence_number FROM markets WHERE token_mint = ?",
            [token_mint],
        ).fetchone()["next_sequence_number"]
        expiry = (self._parse_timestamp(created_at) + self._market_duration).isoformat()
        threshold_price_usd = starting_price_usd * self._resolution_threshold_fraction
        range_floor_price_usd = starting_price_usd * self._rollover_lower_bound_fraction
        range_ceiling_price_usd = starting_price_usd * self._rollover_upper_bound_fraction

        if is_frontend_visible:
            connection.execute(
                """
                UPDATE markets
                SET is_frontend_visible = 0, updated_at = ?
                WHERE token_mint = ?
                  AND is_frontend_visible = 1
                  AND state IN ('awaiting_liquidity', 'open', 'halted')
                """,
                [created_at, token_mint],
            )

        return connection.execute(
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
                starting_price_usd,
                threshold_price_usd,
                range_floor_price_usd,
                range_ceiling_price_usd,
                is_frontend_visible,
                superseded_by_market_id,
                superseded_at,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 'awaiting_liquidity', NULL, ?, NULL, NULL, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            RETURNING id
            """,
            [
                token_mint,
                next_sequence_number,
                seed_market_question(symbol),
                expiry,
                format_decimal(starting_price_usd),
                format_decimal(threshold_price_usd),
                format_decimal(range_floor_price_usd),
                format_decimal(range_ceiling_price_usd),
                1 if is_frontend_visible else 0,
                created_at,
                created_at,
            ],
        ).fetchone()["id"]

    def _create_successor_market(
        self,
        connection: sqlite3.Connection,
        *,
        market_row: sqlite3.Row,
        starting_price_usd: Decimal,
        created_at: str,
    ) -> int:
        successor_market_id = self._create_market(
            connection,
            token_mint=market_row["token_mint"],
            symbol=market_row["symbol"],
            created_at=created_at,
            starting_price_usd=starting_price_usd,
            is_frontend_visible=True,
        )
        connection.execute(
            """
            UPDATE markets
            SET is_frontend_visible = 0, superseded_by_market_id = ?, superseded_at = ?, updated_at = ?
            WHERE id = ?
            """,
            [successor_market_id, created_at, created_at, market_row["id"]],
        )
        return successor_market_id

    @staticmethod
    def _price_outside_rollover_range(reference_price: Decimal, market_row: sqlite3.Row) -> bool:
        return reference_price < parse_decimal(market_row["range_floor_price_usd"]) or reference_price > parse_decimal(
            market_row["range_ceiling_price_usd"]
        )

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

    @staticmethod
    def _market_chart_rows(connection: sqlite3.Connection, market_id: int) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT captured_at, underlying_price_usd, chance_of_outcome_percent
            FROM market_chart_snapshots
            WHERE market_id = ?
            ORDER BY captured_at ASC
            """,
            [market_id],
        ).fetchall()

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
                SET state = 'open', market_start = ?, updated_at = ?
                WHERE id = ?
                """,
                [credited_at, credited_at, market_id],
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
    def _chart_snapshot_time(timestamp: datetime | str) -> datetime:
        dt = timestamp if isinstance(timestamp, datetime) else MarketStore._parse_timestamp(timestamp)
        minute_bucket = (dt.minute // MARKET_CHART_INTERVAL_MINUTES) * MARKET_CHART_INTERVAL_MINUTES
        return dt.replace(minute=minute_bucket, second=0, microsecond=0)

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
    def _ensure_market_column(connection: sqlite3.Connection, column_name: str, column_definition: str) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(markets)").fetchall()
        }
        if column_name in columns:
            return
        connection.execute(f"ALTER TABLE markets ADD COLUMN {column_name} {column_definition}")

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
