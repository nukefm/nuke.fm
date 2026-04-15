from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3

from .amounts import format_usdc_amount
from .catalog import ACTIVE_MARKET_STATES, market_question
from .database import connect_database, utc_now
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

    def list_token_cards(self) -> list[dict]:
        with connect_database(self._database_path) as connection:
            rows = self._list_current_market_rows(connection)
            return [self._serialize_token_card(connection, row) for row in rows]

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

            return {
                "mint": token_row["mint"],
                "symbol": token_row["symbol"],
                "name": token_row["name"],
                "image_url": token_row["image_url"],
                "launched_at": token_row["launched_at"],
                "creator": token_row["creator"],
                "current_market": self._serialize_market(connection, current_market),
                "past_markets": [self._serialize_market(connection, row) for row in past_market_rows],
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
                self._require_position(connection, user_id, market_id, outcome, quote["share_amount_atomic"])
                ledger_entry_id = self._insert_ledger_entry(
                    connection,
                    user_id=user_id,
                    entry_type=f"market_sell_{outcome}",
                    amount_atomic=amount_atomic,
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
                    amount_atomic,
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
                ORDER BY id ASC
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
                yes_price_usd = Decimal("0.5")
            else:
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
                yes_price_usd = preserved_yes_price

            return {
                "market_id": market_id,
                "amount_usdc": format_usdc_amount(amount_atomic),
                "credited_at": credited_at,
                "observed_balance_after_usdc": format_usdc_amount(observed_balance_after_atomic),
                "yes_price_usd": format_decimal(yes_price_usd),
                "no_price_usd": format_decimal(ONE - yes_price_usd),
            }

    def capture_hourly_snapshots(self, price_client, *, captured_at: str | None = None) -> list[dict]:
        captured_timestamp = captured_at or utc_now()
        snapshot_hour = self._snapshot_hour(captured_timestamp)
        captured_rows: list[dict] = []

        with connect_database(self._database_path) as connection:
            market_rows = connection.execute(
                """
                SELECT markets.id, markets.token_mint, markets.state
                FROM markets
                WHERE markets.state IN ('open', 'halted')
                ORDER BY markets.id ASC
                """
            ).fetchall()

            for market_row in market_rows:
                pairs = price_client.list_token_pairs(market_row["token_mint"])
                if not pairs:
                    connection.execute(
                        "UPDATE markets SET state = 'halted', updated_at = ? WHERE id = ?",
                        [captured_timestamp, market_row["id"]],
                    )
                    captured_rows.append({"market_id": market_row["id"], "state": "halted"})
                    continue

                total_liquidity = sum(pair.liquidity_usd for pair in pairs)
                reference_price = sum(pair.price_usd * pair.liquidity_usd for pair in pairs) / total_liquidity
                latest_snapshot = self._latest_snapshot_row(connection, market_row["id"])
                ath_price = reference_price
                ath_timestamp = snapshot_hour
                if latest_snapshot is not None:
                    previous_ath = parse_decimal(latest_snapshot["ath_price_usd"])
                    if previous_ath >= reference_price:
                        ath_price = previous_ath
                        ath_timestamp = latest_snapshot["ath_timestamp"]
                threshold_price = ath_price * self._threshold_fraction
                drawdown_fraction = Decimal("0") if ath_price == 0 else ONE - (reference_price / ath_price)

                for pair in pairs:
                    connection.execute(
                        """
                        INSERT INTO market_pair_snapshots (
                            market_id,
                            snapshot_hour,
                            pair_address,
                            dex_id,
                            price_usd,
                            liquidity_usd,
                            captured_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(market_id, snapshot_hour, pair_address) DO UPDATE SET
                            dex_id = excluded.dex_id,
                            price_usd = excluded.price_usd,
                            liquidity_usd = excluded.liquidity_usd,
                            captured_at = excluded.captured_at
                        """,
                        [
                            market_row["id"],
                            snapshot_hour,
                            pair.pair_address,
                            pair.dex_id,
                            str(pair.price_usd),
                            str(pair.liquidity_usd),
                            captured_timestamp,
                        ],
                    )

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
                        snapshot_hour,
                        str(reference_price),
                        len(pairs),
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
                 AND market_snapshots.snapshot_hour = (
                    SELECT snapshot_hour
                    FROM market_snapshots AS latest_snapshot
                    WHERE latest_snapshot.market_id = markets.id
                    ORDER BY latest_snapshot.snapshot_hour DESC
                    LIMIT 1
                 )
                WHERE markets.state IN ('open', 'halted')
                ORDER BY markets.id ASC
                """
            ).fetchall()

            markets_to_resolve: list[tuple[int, str, str]] = []
            for row in rows:
                if row["snapshot_hour"] is not None and row["expiry"] is not None:
                    snapshot_time = self._parse_timestamp(row["snapshot_hour"])
                    expiry_time = self._parse_timestamp(row["expiry"])
                    threshold_price = parse_decimal(row["threshold_price_usd"])
                    reference_price = parse_decimal(row["reference_price_usd"])
                    if (
                        snapshot_time > self._parse_timestamp(row["ath_timestamp"])
                        and snapshot_time <= expiry_time
                        and reference_price <= threshold_price
                    ):
                        markets_to_resolve.append((row["id"], "resolved_yes", row["snapshot_hour"]))
                        continue
                if row["expiry"] is not None and resolution_time >= self._parse_timestamp(row["expiry"]):
                    markets_to_resolve.append((row["id"], "resolved_no", resolution_timestamp))

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
            share_amount_atomic, pool_after = self._quote_buy(pool, outcome, amount_atomic)
        else:
            share_amount_atomic, pool_after = self._quote_sell(pool, outcome, amount_atomic)

        return {
            "market_id": market_row["id"],
            "token_mint": market_row["token_mint"],
            "symbol": market_row["symbol"],
            "outcome": outcome,
            "side": side,
            "amount_usdc": format_usdc_amount(amount_atomic),
            "share_amount": format_usdc_amount(share_amount_atomic),
            "share_amount_atomic": share_amount_atomic,
            "average_price_usdc": format_decimal(Decimal(amount_atomic) / Decimal(share_amount_atomic)),
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
    def _quote_sell(pool: WeightedPoolState, outcome: str, amount_atomic: int) -> tuple[int, WeightedPoolState]:
        if amount_atomic > pool.cash_backing_atomic:
            raise ValueError("Sell amount exceeds available market cash backing.")

        # Selling for cash redeems complete sets. The user spends cash_amount of the outcome
        # directly on redemption and spends the rest to buy the opposite leg from the pool.
        if outcome == "yes":
            swap_input_atomic = amount_in_given_out(
                reserve_in_atomic=pool.yes_reserve_atomic,
                reserve_out_atomic=pool.no_reserve_atomic,
                weight_in=pool.yes_weight,
                weight_out=pool.no_weight,
                amount_out_atomic=amount_atomic,
            )
            share_amount_atomic = amount_atomic + swap_input_atomic
            return share_amount_atomic, WeightedPoolState(
                yes_reserve_atomic=pool.yes_reserve_atomic + swap_input_atomic,
                no_reserve_atomic=pool.no_reserve_atomic - amount_atomic,
                yes_weight=pool.yes_weight,
                no_weight=pool.no_weight,
                cash_backing_atomic=pool.cash_backing_atomic - amount_atomic,
                total_liquidity_atomic=pool.total_liquidity_atomic,
            )

        swap_input_atomic = amount_in_given_out(
            reserve_in_atomic=pool.no_reserve_atomic,
            reserve_out_atomic=pool.yes_reserve_atomic,
            weight_in=pool.no_weight,
            weight_out=pool.yes_weight,
            amount_out_atomic=amount_atomic,
        )
        share_amount_atomic = amount_atomic + swap_input_atomic
        return share_amount_atomic, WeightedPoolState(
            yes_reserve_atomic=pool.yes_reserve_atomic - amount_atomic,
            no_reserve_atomic=pool.no_reserve_atomic + swap_input_atomic,
            yes_weight=pool.yes_weight,
            no_weight=pool.no_weight,
            cash_backing_atomic=pool.cash_backing_atomic - amount_atomic,
            total_liquidity_atomic=pool.total_liquidity_atomic,
        )

    def _serialize_token_card(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        return {
            "mint": row["mint"],
            "symbol": row["symbol"],
            "name": row["name"],
            "image_url": row["image_url"],
            "launched_at": row["launched_at"],
            "current_market": self._serialize_market(connection, row),
        }

    def _serialize_market(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        pool = self._load_pool(connection, row["id"], required=False)
        latest_snapshot = self._latest_snapshot_row(connection, row["id"])
        yes_price_usd = None if pool is None else format_decimal(yes_price(pool))
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
            "reference_price_usd": None if latest_snapshot is None else format_decimal(parse_decimal(latest_snapshot["reference_price_usd"])),
            "ath_price_usd": None if latest_snapshot is None else format_decimal(parse_decimal(latest_snapshot["ath_price_usd"])),
            "ath_timestamp": None if latest_snapshot is None else latest_snapshot["ath_timestamp"],
            "drawdown_fraction": None if latest_snapshot is None else format_decimal(parse_decimal(latest_snapshot["drawdown_fraction"])),
            "threshold_price_usd": None if latest_snapshot is None else format_decimal(parse_decimal(latest_snapshot["threshold_price_usd"])),
            "total_liquidity_usdc": None if pool is None else format_usdc_amount(pool.total_liquidity_atomic),
        }

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
            SELECT amount_atomic, credited_at
            FROM market_liquidity_deposits
            WHERE market_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            [current_market["id"]],
        ).fetchone()
        if latest_liquidity is not None:
            activity.append(
                {
                    "timestamp": latest_liquidity["credited_at"],
                    "summary": f"Liquidity credit of {format_usdc_amount(latest_liquidity['amount_atomic'])} USDC deepened the pool.",
                }
            )
        latest_snapshot = self._latest_snapshot_row(connection, current_market["id"])
        if latest_snapshot is not None:
            activity.append(
                {
                    "timestamp": latest_snapshot["snapshot_hour"],
                    "summary": f"Latest hourly reference price is {format_decimal(parse_decimal(latest_snapshot['reference_price_usd']))} USDC.",
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
                        f"Latest trade was a {latest_trade['side']} {latest_trade['outcome'].upper()} "
                        f"execution for {format_usdc_amount(latest_trade['cash_amount_atomic'])} USDC."
                    ),
                }
            )
        return sorted(activity, key=lambda item: item["timestamp"], reverse=True)

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
    def _snapshot_hour(timestamp: str) -> str:
        dt = MarketStore._parse_timestamp(timestamp)
        return dt.replace(minute=0, second=0, microsecond=0).isoformat()

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
