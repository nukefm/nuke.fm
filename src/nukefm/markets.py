from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
import json
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
    long_price,
    parse_decimal,
    retuned_weights_for_equal_liquidity,
    short_price,
    weights_for_price,
)


TOKEN_CARD_SORT_OPTIONS = (
    ("state", "State"),
    ("predicted_nuke_percent", "Implied move"),
    ("expiry", "Expiry"),
    ("pm_volume", "Prediction volume"),
    ("market_liquidity", "Prediction liquidity"),
    ("underlying_volume", "Token volume"),
    ("underlying_market_cap", "Token mktcap"),
)
TOKEN_CARD_SORT_FIELDS = {value for value, _ in TOKEN_CARD_SORT_OPTIONS}
MARKET_CHART_INTERVAL_MINUTES = 5


def bags_token_url(token_mint: str) -> str:
    return f"https://bags.fm/{token_mint}"


class MarketStore:
    def __init__(
        self,
        database_path: Path,
        *,
        market_duration_days: int = 90,
        market_price_range_multiple: Decimal = Decimal("10"),
        market_rollover_boundary_rate: Decimal = Decimal("0.85"),
        market_rollover_liquidity_transfer_fraction: Decimal = Decimal("0.80"),
    ) -> None:
        self._database_path = database_path
        self._market_duration = timedelta(days=market_duration_days)
        if market_price_range_multiple <= ONE:
            raise ValueError("market_price_range_multiple must be greater than 1.")
        if market_rollover_boundary_rate <= Decimal("0.5") or market_rollover_boundary_rate >= ONE:
            raise ValueError("market_rollover_boundary_rate must be inside (0.5, 1).")
        if market_rollover_liquidity_transfer_fraction < 0 or market_rollover_liquidity_transfer_fraction > ONE:
            raise ValueError("market_rollover_liquidity_transfer_fraction must be inside [0, 1].")
        self._market_price_range_multiple = market_price_range_multiple
        self._market_rollover_boundary_rate = market_rollover_boundary_rate
        self._market_rollover_liquidity_transfer_fraction = market_rollover_liquidity_transfer_fraction

    def initialize(self) -> None:
        with connect_database(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            if self._has_legacy_binary_market_schema(connection):
                self._reset_legacy_binary_market_state(connection)
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS market_pools (
                    market_id INTEGER PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
                    long_reserve_atomic INTEGER NOT NULL,
                    short_reserve_atomic INTEGER NOT NULL,
                    long_weight TEXT NOT NULL,
                    short_weight TEXT NOT NULL,
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
                    long_shares_atomic INTEGER NOT NULL DEFAULT 0,
                    short_shares_atomic INTEGER NOT NULL DEFAULT 0,
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
                    before_long_price TEXT NOT NULL,
                    before_short_price TEXT NOT NULL,
                    after_long_price TEXT NOT NULL,
                    after_short_price TEXT NOT NULL,
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
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY(market_id, snapshot_hour)
                );

                CREATE TABLE IF NOT EXISTS market_chart_snapshots (
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    captured_at TEXT NOT NULL,
                    underlying_price_usd TEXT NOT NULL,
                    implied_price_usd TEXT NOT NULL,
                    PRIMARY KEY(market_id, captured_at)
                );

                CREATE TABLE IF NOT EXISTS token_rationales (
                    token_mint TEXT NOT NULL REFERENCES tokens(mint) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    submitter_wallet_address TEXT NOT NULL,
                    forecast_price_usd TEXT,
                    confidence TEXT,
                    rationale TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(token_mint, user_id)
                );

                CREATE TABLE IF NOT EXISTS token_metrics_snapshots (
                    token_mint TEXT NOT NULL REFERENCES tokens(mint) ON DELETE CASCADE,
                    captured_at TEXT NOT NULL,
                    pair_count INTEGER NOT NULL,
                    underlying_volume_h24_usd TEXT,
                    underlying_market_cap_usd TEXT,
                    token_supply TEXT,
                    market_cap_kind TEXT,
                    source_pair_address TEXT,
                    source_dex_id TEXT,
                    source_price_usd TEXT,
                    source_liquidity_usd TEXT,
                    PRIMARY KEY(token_mint, captured_at)
                );

                CREATE TABLE IF NOT EXISTS market_payouts (
                    market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    long_shares_atomic INTEGER NOT NULL,
                    short_shares_atomic INTEGER NOT NULL,
                    long_rate TEXT NOT NULL,
                    short_rate TEXT NOT NULL,
                    resolution_price_usd TEXT NOT NULL,
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
            self._ensure_market_column(connection, "min_price_usd", "TEXT")
            self._ensure_market_column(connection, "max_price_usd", "TEXT")
            self._ensure_market_column(connection, "is_frontend_visible", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_market_column(connection, "superseded_by_market_id", "INTEGER REFERENCES markets(id) ON DELETE SET NULL")
            self._ensure_market_column(connection, "superseded_at", "TEXT")
            self._ensure_table_column(connection, "token_metrics_snapshots", "token_supply", "TEXT")
            self._ensure_table_column(connection, "token_metrics_snapshots", "market_cap_kind", "TEXT")

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
        token_mints: Collection[str] | None = None,
        captured_at: str | None = None,
    ) -> list[dict]:
        captured_timestamp = captured_at or utc_now()
        included_token_mints = None if token_mints is None else set(token_mints)

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
                if included_token_mints is not None and row["mint"] not in included_token_mints:
                    continue

                pairs = metrics_client.list_token_pairs(row["mint"])
                underlying_volume = self._sum_pair_volume(pairs)
                price_pair = self._most_liquid_pair_with_price(pairs)
                supply_pair = self._most_liquid_pair_with_supply(pairs)
                source_pair = price_pair or supply_pair
                derived_market_cap = None
                market_cap_kind = None
                if price_pair is not None and price_pair.price_usd is not None and supply_pair is not None:
                    derived_market_cap = price_pair.price_usd * supply_pair.token_supply
                    market_cap_kind = supply_pair.market_cap_kind

                connection.execute(
                    """
                    INSERT INTO token_metrics_snapshots (
                        token_mint,
                        captured_at,
                        pair_count,
                        underlying_volume_h24_usd,
                        underlying_market_cap_usd,
                        token_supply,
                        market_cap_kind,
                        source_pair_address,
                        source_dex_id,
                        source_price_usd,
                        source_liquidity_usd
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(token_mint, captured_at) DO UPDATE SET
                        pair_count = excluded.pair_count,
                        underlying_volume_h24_usd = excluded.underlying_volume_h24_usd,
                        underlying_market_cap_usd = excluded.underlying_market_cap_usd,
                        token_supply = excluded.token_supply,
                        market_cap_kind = excluded.market_cap_kind,
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
                        None if derived_market_cap is None else str(derived_market_cap),
                        None if supply_pair is None else str(supply_pair.token_supply),
                        market_cap_kind,
                        None if source_pair is None else source_pair.pair_address,
                        None if source_pair is None else source_pair.dex_id,
                        None if price_pair is None or price_pair.price_usd is None else str(price_pair.price_usd),
                        None if price_pair is None or price_pair.liquidity_usd is None else str(price_pair.liquidity_usd),
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
                        "underlying_market_cap_usd": None if derived_market_cap is None else format_decimal(derived_market_cap),
                        "market_cap_kind": market_cap_kind,
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

                implied_price = self._implied_price_usd(long_price(pool), row)
                connection.execute(
                    """
                    INSERT INTO market_chart_snapshots (
                        market_id,
                        captured_at,
                        underlying_price_usd,
                        implied_price_usd
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(market_id, captured_at) DO UPDATE SET
                        underlying_price_usd = excluded.underlying_price_usd,
                        implied_price_usd = excluded.implied_price_usd
                    """,
                    [
                        row["id"],
                        snapshot_time,
                        str(price_pair.price_usd),
                        str(implied_price),
                    ],
                )
                captured_rows.append(
                    {
                        "market_id": row["id"],
                        "captured_at": snapshot_time,
                        "underlying_price_usd": format_decimal(price_pair.price_usd),
                        "implied_price_usd": format_decimal(implied_price),
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
                  AND state IN ('resolved', 'void')
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
                "bags_token_url": bags_token_url(token_row["mint"]),
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
                "rationales": self._token_rationales(connection, token_row["mint"]),
                "current_market_chart": self._serialize_current_market_chart(
                    connection,
                    current_market,
                    hidden_active_market_rows,
                ),
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
                    before_long_price,
                    before_short_price,
                    after_long_price,
                    after_short_price,
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
                    quote["before_long_price_usd"],
                    quote["before_short_price_usd"],
                    quote["after_long_price_usd"],
                    quote["after_short_price_usd"],
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

    def upsert_token_rationale(
        self,
        *,
        user_id: int,
        submitter_wallet_address: str,
        token_mint: str,
        rationale: str,
        forecast_price_usd: str | None = None,
        confidence: str | None = None,
        sources: list[str] | None = None,
    ) -> dict:
        normalized_rationale = rationale.strip()
        if not normalized_rationale:
            raise ValueError("Rationale must not be empty.")

        forecast_price_text = None
        if forecast_price_usd is not None:
            forecast_price = parse_decimal(forecast_price_usd)
            if forecast_price <= Decimal("0"):
                raise ValueError("forecast_price_usd must be positive.")
            forecast_price_text = format_decimal(forecast_price)

        confidence_text = None
        if confidence is not None:
            confidence_value = parse_decimal(confidence)
            if confidence_value < Decimal("0") or confidence_value > Decimal("1"):
                raise ValueError("confidence must be inside [0, 1].")
            confidence_text = format_decimal(confidence_value)

        timestamp = utc_now()
        sources_json = json.dumps([str(source) for source in (sources or [])])
        with connect_database(self._database_path) as connection:
            token = connection.execute("SELECT mint FROM tokens WHERE mint = ?", [token_mint]).fetchone()
            if token is None:
                raise LookupError(f"Unknown token mint: {token_mint}")

            row = connection.execute(
                """
                INSERT INTO token_rationales (
                    token_mint,
                    user_id,
                    submitter_wallet_address,
                    forecast_price_usd,
                    confidence,
                    rationale,
                    sources_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_mint, user_id) DO UPDATE SET
                    submitter_wallet_address = excluded.submitter_wallet_address,
                    forecast_price_usd = excluded.forecast_price_usd,
                    confidence = excluded.confidence,
                    rationale = excluded.rationale,
                    sources_json = excluded.sources_json,
                    updated_at = excluded.updated_at
                RETURNING *
                """,
                [
                    token_mint,
                    user_id,
                    submitter_wallet_address,
                    forecast_price_text,
                    confidence_text,
                    normalized_rationale,
                    sources_json,
                    timestamp,
                    timestamp,
                ],
            ).fetchone()
            serialized = self._serialize_token_rationale(connection, row)
            serialized.pop("_position_value_atomic")
            return serialized

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
                    market_positions.long_shares_atomic,
                    market_positions.short_shares_atomic
                FROM market_positions
                JOIN markets ON markets.id = market_positions.market_id
                JOIN tokens ON tokens.mint = markets.token_mint
                WHERE market_positions.user_id = ?
                  AND (market_positions.long_shares_atomic > 0 OR market_positions.short_shares_atomic > 0)
                ORDER BY markets.id DESC
                """,
                [user_id],
            ).fetchall()

            positions: list[dict] = []
            for row in rows:
                pool = self._load_pool(connection, row["market_id"], required=False)
                current_long_price = None if pool is None else long_price(pool)
                current_short_price = None if pool is None else short_price(pool)
                marked_value_atomic = 0
                if current_long_price is not None:
                    marked_value_atomic += int(Decimal(row["long_shares_atomic"]) * current_long_price)
                    marked_value_atomic += int(Decimal(row["short_shares_atomic"]) * current_short_price)
                positions.append(
                    {
                        "market_id": row["market_id"],
                        "mint": row["mint"],
                        "symbol": row["symbol"],
                        "name": row["name"],
                        "question": row["question"],
                        "state": row["state"],
                        "long_shares": format_usdc_amount(row["long_shares_atomic"]),
                        "short_shares": format_usdc_amount(row["short_shares_atomic"]),
                        "long_price_usd": None if current_long_price is None else format_decimal(current_long_price),
                        "short_price_usd": None if current_short_price is None else format_decimal(current_short_price),
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
                    market_trades.before_long_price,
                    market_trades.before_short_price,
                    market_trades.after_long_price,
                    market_trades.after_short_price,
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
                    "before_long_price_usd": row["before_long_price"],
                    "before_short_price_usd": row["before_short_price"],
                    "after_long_price_usd": row["after_long_price"],
                    "after_short_price_usd": row["after_short_price"],
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
            long_price_usd = self._apply_liquidity_credit(
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
                "long_price_usd": format_decimal(long_price_usd),
                "short_price_usd": format_decimal(ONE - long_price_usd),
            }

    def seed_top_markets_by_underlying_volume(
        self,
        *,
        amount_atomic: int,
        limit: int = 4,
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
                        token_metrics_snapshots.underlying_volume_h24_usd
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
                    WHERE token_metrics_snapshots.underlying_volume_h24_usd IS NOT NULL
                    ORDER BY CAST(token_metrics_snapshots.underlying_volume_h24_usd AS REAL) DESC, markets.id ASC
                    LIMIT ?
                )
                SELECT
                    ranked_markets.id,
                    ranked_markets.token_mint,
                    ranked_markets.symbol,
                    ranked_markets.underlying_volume_h24_usd
                FROM ranked_markets
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM market_liquidity_seed_events
                    WHERE market_liquidity_seed_events.market_id = ranked_markets.id
                      AND market_liquidity_seed_events.week_start = ?
                )
                ORDER BY CAST(ranked_markets.underlying_volume_h24_usd AS REAL) DESC, ranked_markets.id ASC
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
                long_price_usd = self._apply_liquidity_credit(
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
                        "long_price_usd": format_decimal(long_price_usd),
                        "short_price_usd": format_decimal(ONE - long_price_usd),
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
                    markets.min_price_usd,
                    markets.max_price_usd,
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

                connection.execute(
                    """
                    INSERT INTO market_snapshots (
                        market_id,
                        snapshot_hour,
                        reference_price_usd,
                        pair_count,
                        captured_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(market_id, snapshot_hour) DO UPDATE SET
                        reference_price_usd = excluded.reference_price_usd,
                        pair_count = excluded.pair_count,
                        captured_at = excluded.captured_at
                    """,
                    [
                        market_row["id"],
                        next_snapshot_hour.isoformat(),
                        format_decimal(reference_price),
                        1,
                        captured_timestamp,
                    ],
                )
                connection.execute("UPDATE markets SET updated_at = ? WHERE id = ?", [captured_timestamp, market_row["id"]])

                if (
                    bool(market_row["is_frontend_visible"])
                    and self._snapshot_touches_rollover_boundary(reference_price, market_row)
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
                        "long_rate": format_decimal(self._long_rate_for_price(reference_price, market_row)),
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
                    markets.min_price_usd,
                    markets.max_price_usd,
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

            markets_to_resolve: list[tuple[int, str, Decimal, bool]] = []
            for market_id, row in market_rows.items():
                if row["expiry"] is None:
                    continue

                expiry_time = self._parse_timestamp(row["expiry"])
                if resolution_time < expiry_time:
                    continue

                latest_snapshot_before_expiry = None
                for snapshot_row in snapshot_rows.get(market_id, []):
                    if self._parse_timestamp(snapshot_row["snapshot_hour"]) <= expiry_time:
                        latest_snapshot_before_expiry = snapshot_row

                if latest_snapshot_before_expiry is None:
                    logger.warning(
                        "Market {} reached expiry {} but has no stored 24h-median snapshot at or before expiry; leaving unresolved.",
                        market_id,
                        row["expiry"],
                    )
                    continue

                markets_to_resolve.append(
                    (
                        market_id,
                        latest_snapshot_before_expiry["snapshot_hour"],
                        parse_decimal(latest_snapshot_before_expiry["reference_price_usd"]),
                        bool(row["is_frontend_visible"]),
                    )
                )

        for market_id, market_resolved_at, resolution_price_usd, should_create_successor in markets_to_resolve:
            self._settle_market(
                catalog,
                market_id,
                market_resolved_at,
                resolution_price_usd=resolution_price_usd,
                successor_price_usd=resolution_price_usd if should_create_successor else None,
            )
            resolved_markets.append(
                {
                    "market_id": market_id,
                    "state": "resolved",
                    "resolved_at": market_resolved_at,
                    "resolution_price_usd": format_decimal(resolution_price_usd),
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
        resolved_at: str,
        *,
        resolution_price_usd: Decimal,
        successor_price_usd: Decimal | None,
    ) -> None:
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
            long_rate = self._long_rate_for_price(resolution_price_usd, market_row)
            short_rate = ONE - long_rate
            positions = connection.execute(
                """
                SELECT user_id, long_shares_atomic, short_shares_atomic
                FROM market_positions
                WHERE market_id = ?
                  AND (long_shares_atomic > 0 OR short_shares_atomic > 0)
                ORDER BY user_id ASC
                """,
                [market_id],
            ).fetchall()
            payouts = [
                (
                    position,
                    int(
                        (
                            Decimal(position["long_shares_atomic"]) * long_rate
                            + Decimal(position["short_shares_atomic"]) * short_rate
                        ).to_integral_value(rounding=ROUND_DOWN)
                    ),
                )
                for position in positions
            ]
            total_payout_atomic = sum(payout_atomic for _, payout_atomic in payouts)
            if total_payout_atomic > pool.cash_backing_atomic:
                raise RuntimeError(f"Market {market_id} is undercollateralized at resolution.")

            for position, payout_atomic in payouts:
                if payout_atomic <= 0:
                    continue
                connection.execute(
                    """
                    INSERT INTO market_payouts (
                        market_id,
                        user_id,
                        long_shares_atomic,
                        short_shares_atomic,
                        long_rate,
                        short_rate,
                        resolution_price_usd,
                        payout_atomic,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        market_id,
                        position["user_id"],
                        position["long_shares_atomic"],
                        position["short_shares_atomic"],
                        format_decimal(long_rate),
                        format_decimal(short_rate),
                        format_decimal(resolution_price_usd),
                        payout_atomic,
                        resolved_at,
                    ],
                )
                self._insert_ledger_entry(
                    connection,
                    user_id=position["user_id"],
                    entry_type="market_payout",
                    amount_atomic=payout_atomic,
                    reference_type="market_payout",
                    reference_id=str(market_id),
                    note=f"Resolved scalar payout for market {market_id}.",
                    created_at=resolved_at,
                )

            connection.execute(
                """
                UPDATE market_positions
                SET long_shares_atomic = 0, short_shares_atomic = 0, updated_at = ?
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

        catalog.resolve_market(market_id, "resolved", resolved_at=resolved_at)
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
        if outcome not in {"long", "short"}:
            raise ValueError("Outcome must be 'long' or 'short'.")
        if side not in {"buy", "sell"}:
            raise ValueError("Side must be 'buy' or 'sell'.")
        if amount_atomic <= 0:
            raise ValueError("Trade amount must be positive.")

        before_long_price = long_price(pool)
        before_short_price = short_price(pool)

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
            "before_long_price_usd": format_decimal(before_long_price),
            "before_short_price_usd": format_decimal(before_short_price),
            "after_long_price_usd": format_decimal(long_price(pool_after)),
            "after_short_price_usd": format_decimal(short_price(pool_after)),
            "pool_after": pool_after,
        }

    @staticmethod
    def _quote_buy(pool: WeightedPoolState, outcome: str, amount_atomic: int) -> tuple[int, WeightedPoolState]:
        # Buying with cash mints complete sets off-pool, then swaps the opposite leg through the pool.
        if outcome == "long":
            swap_out_atomic = amount_out_given_in(
                reserve_in_atomic=pool.short_reserve_atomic,
                reserve_out_atomic=pool.long_reserve_atomic,
                weight_in=pool.short_weight,
                weight_out=pool.long_weight,
                amount_in_atomic=amount_atomic,
            )
            share_amount_atomic = amount_atomic + swap_out_atomic
            return share_amount_atomic, WeightedPoolState(
                long_reserve_atomic=pool.long_reserve_atomic - swap_out_atomic,
                short_reserve_atomic=pool.short_reserve_atomic + amount_atomic,
                long_weight=pool.long_weight,
                short_weight=pool.short_weight,
                cash_backing_atomic=pool.cash_backing_atomic + amount_atomic,
                total_liquidity_atomic=pool.total_liquidity_atomic,
            )

        swap_out_atomic = amount_out_given_in(
            reserve_in_atomic=pool.long_reserve_atomic,
            reserve_out_atomic=pool.short_reserve_atomic,
            weight_in=pool.long_weight,
            weight_out=pool.short_weight,
            amount_in_atomic=amount_atomic,
        )
        share_amount_atomic = amount_atomic + swap_out_atomic
        return share_amount_atomic, WeightedPoolState(
            long_reserve_atomic=pool.long_reserve_atomic + amount_atomic,
            short_reserve_atomic=pool.short_reserve_atomic - swap_out_atomic,
            long_weight=pool.long_weight,
            short_weight=pool.short_weight,
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

        if outcome == "long":
            return cash_amount_atomic, share_amount_atomic, WeightedPoolState(
                long_reserve_atomic=pool.long_reserve_atomic + swap_input_atomic,
                short_reserve_atomic=pool.short_reserve_atomic - cash_amount_atomic,
                long_weight=pool.long_weight,
                short_weight=pool.short_weight,
                cash_backing_atomic=pool.cash_backing_atomic - cash_amount_atomic,
                total_liquidity_atomic=pool.total_liquidity_atomic,
            )

        return cash_amount_atomic, share_amount_atomic, WeightedPoolState(
            long_reserve_atomic=pool.long_reserve_atomic - cash_amount_atomic,
            short_reserve_atomic=pool.short_reserve_atomic + swap_input_atomic,
            long_weight=pool.long_weight,
            short_weight=pool.short_weight,
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
            elif outcome == "long":
                required_share_amount = candidate_cash_out + amount_in_given_out(
                    reserve_in_atomic=pool.long_reserve_atomic,
                    reserve_out_atomic=pool.short_reserve_atomic,
                    weight_in=pool.long_weight,
                    weight_out=pool.short_weight,
                    amount_out_atomic=candidate_cash_out,
                )
            else:
                required_share_amount = candidate_cash_out + amount_in_given_out(
                    reserve_in_atomic=pool.short_reserve_atomic,
                    reserve_out_atomic=pool.long_reserve_atomic,
                    weight_in=pool.short_weight,
                    weight_out=pool.long_weight,
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
            "bags_token_url": bags_token_url(row["mint"]),
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
        long_price_value = None if pool is None else long_price(pool)
        long_price_usd = None if long_price_value is None else format_decimal(long_price_value)
        short_price_usd = None if pool is None else format_decimal(short_price(pool))
        starting_price = parse_decimal(row["starting_price_usd"])
        current_observed_price = None if latest_snapshot is None else parse_decimal(latest_snapshot["reference_price_usd"])
        implied_price = None if long_price_value is None else self._implied_price_usd(long_price_value, row)
        token_supply = (
            None
            if latest_token_metrics is None or latest_token_metrics["token_supply"] is None
            else parse_decimal(latest_token_metrics["token_supply"])
        )
        current_market_cap = (
            None
            if latest_token_metrics is None or latest_token_metrics["underlying_market_cap_usd"] is None
            else parse_decimal(latest_token_metrics["underlying_market_cap_usd"])
        )
        predicted_market_cap = None if token_supply is None or implied_price is None else token_supply * implied_price
        predicted_nuke_fraction = (
            None
            if current_observed_price is None
            or implied_price is None
            or current_observed_price <= 0
            or implied_price <= 0
            else (implied_price / current_observed_price) - ONE
        )

        return {
            "id": row["id"],
            "sequence_number": row["sequence_number"],
            "question": self._market_prompt(symbol=row["symbol"], expiry=row["expiry"], created_at=row["created_at"]),
            "state": row["state"],
            "market_start": row["market_start"],
            "expiry": row["expiry"],
            "liquidity_deposit_address": row["liquidity_deposit_address"],
            "resolved_at": row["resolved_at"],
            "created_at": row["created_at"],
            "starting_price_usd": format_decimal(starting_price),
            "min_price_usd": format_decimal(parse_decimal(row["min_price_usd"])),
            "max_price_usd": format_decimal(parse_decimal(row["max_price_usd"])),
            "is_frontend_visible": bool(row["is_frontend_visible"]),
            "superseded_by_market_id": row["superseded_by_market_id"],
            "superseded_at": row["superseded_at"],
            "long_price_usd": long_price_usd,
            "short_price_usd": short_price_usd,
            "implied_price_usd": None if implied_price is None else format_decimal(implied_price),
            "predicted_market_cap_usd": None if predicted_market_cap is None else format_decimal(predicted_market_cap),
            "predicted_nuke_percent": None if predicted_nuke_fraction is None else self._format_percent(predicted_nuke_fraction),
            "predicted_nuke_fraction": None if predicted_nuke_fraction is None else format_decimal(predicted_nuke_fraction),
            "reference_price_usd": None if current_observed_price is None else format_decimal(current_observed_price),
            "total_liquidity_usdc": None if pool is None else format_usdc_amount(pool.total_liquidity_atomic),
            "pm_volume_24h_usdc": format_usdc_amount(pm_volume_24h_atomic),
            "underlying_volume_24h_usd": None
            if latest_token_metrics is None or latest_token_metrics["underlying_volume_h24_usd"] is None
            else format_decimal(parse_decimal(latest_token_metrics["underlying_volume_h24_usd"])),
            "underlying_market_cap_usd": None if current_market_cap is None else format_decimal(current_market_cap),
            "market_cap_kind": None if latest_token_metrics is None else latest_token_metrics["market_cap_kind"],
        }

    @staticmethod
    def _format_percent(fraction: Decimal) -> str:
        return f"{format_decimal((fraction * Decimal('100')).quantize(Decimal('0.01')))}%"

    def _market_prompt(
        self,
        *,
        symbol: str,
        expiry: str | None,
        created_at: str,
    ) -> str:
        deadline = (
            self._parse_timestamp(expiry).date().isoformat()
            if expiry is not None
            else (self._parse_timestamp(created_at) + self._market_duration).date().isoformat()
        )
        return f"What will {symbol} trade at by {deadline}?"

    def _serialize_current_market_chart(
        self,
        connection: sqlite3.Connection,
        current_market: sqlite3.Row,
        hidden_active_markets: list[sqlite3.Row],
    ) -> dict:
        market_ids = [row["id"] for row in hidden_active_markets]
        market_ids.append(current_market["id"])
        return {
            "market_id": current_market["id"],
            "interval_minutes": MARKET_CHART_INTERVAL_MINUTES,
            "points": [
                {
                    "captured_at": row["captured_at"],
                    "underlying_price_usd": format_decimal(parse_decimal(row["underlying_price_usd"])),
                    "implied_price_usd": format_decimal(parse_decimal(row["implied_price_usd"])),
                }
                for row in self._market_chart_rows(connection, market_ids)
            ],
        }

    def _token_rationales(self, connection: sqlite3.Connection, token_mint: str) -> list[dict]:
        rows = connection.execute(
            """
            SELECT *
            FROM token_rationales
            WHERE token_mint = ?
            """,
            [token_mint],
        ).fetchall()
        rationales = [self._serialize_token_rationale(connection, row) for row in rows]
        rationales.sort(key=lambda item: (-item["_position_value_atomic"], item["submitter_wallet_address"]))
        for rationale in rationales:
            rationale.pop("_position_value_atomic")
        return rationales

    def _serialize_token_rationale(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        position_value_atomic = self._token_position_value_atomic(
            connection,
            user_id=row["user_id"],
            token_mint=row["token_mint"],
        )
        return {
            "token_mint": row["token_mint"],
            "submitter_wallet_address": row["submitter_wallet_address"],
            "rationale": row["rationale"],
            "forecast_price_usd": row["forecast_price_usd"],
            "confidence": row["confidence"],
            "sources": json.loads(row["sources_json"]),
            "position_value_usdc": format_usdc_amount(position_value_atomic),
            "updated_at": row["updated_at"],
            "_position_value_atomic": position_value_atomic,
        }

    def _token_position_value_atomic(self, connection: sqlite3.Connection, *, user_id: int, token_mint: str) -> int:
        rows = connection.execute(
            """
            SELECT
                market_positions.market_id,
                market_positions.long_shares_atomic,
                market_positions.short_shares_atomic
            FROM market_positions
            JOIN markets ON markets.id = market_positions.market_id
            WHERE market_positions.user_id = ?
              AND markets.token_mint = ?
              AND (
                market_positions.long_shares_atomic > 0
                OR market_positions.short_shares_atomic > 0
              )
            """,
            [user_id, token_mint],
        ).fetchall()
        total_value_atomic = 0
        for row in rows:
            pool = self._load_pool(connection, row["market_id"], required=False)
            if pool is None:
                continue
            total_value_atomic += int(Decimal(row["long_shares_atomic"]) * long_price(pool))
            total_value_atomic += int(Decimal(row["short_shares_atomic"]) * short_price(pool))
        return total_value_atomic

    @staticmethod
    def _sort_token_cards(token_cards: list[dict], *, sort_by: str, sort_direction: str) -> None:
        if sort_by not in TOKEN_CARD_SORT_FIELDS:
            raise ValueError(f"Unsupported token card sort field: {sort_by}")
        normalized_direction = sort_direction.lower()
        if normalized_direction not in {"asc", "desc"}:
            raise ValueError(f"Unsupported token card sort direction: {sort_direction}")

        valued_cards = []
        missing_cards = []
        for token_card in token_cards:
            value = MarketStore._token_card_sort_value(token_card, sort_by)
            if value is None:
                missing_cards.append(token_card)
            else:
                valued_cards.append((value, token_card))

        descending = normalized_direction == "desc"
        valued_cards.sort(key=lambda valued_card: valued_card[0], reverse=descending)
        token_cards[:] = [token_card for _, token_card in valued_cards] + missing_cards

    @staticmethod
    def _token_card_sort_value(token_card: dict, sort_by: str) -> Decimal | tuple[int, str, str, str] | None:
        current_market = token_card["current_market"]
        if sort_by == "state":
            state_rank = {"open": 0, "awaiting_liquidity": 1, "halted": 2}
            return (
                state_rank.get(current_market["state"], 3),
                current_market["state"],
                token_card["symbol"].casefold(),
                token_card["name"].casefold(),
            )
        if sort_by == "predicted_nuke_percent":
            value = current_market["predicted_nuke_fraction"]
        elif sort_by == "expiry":
            value = current_market["expiry"]
        elif sort_by == "pm_volume":
            value = current_market["pm_volume_24h_usdc"]
        elif sort_by == "market_liquidity":
            value = current_market["total_liquidity_usdc"]
        elif sort_by == "underlying_volume":
            value = current_market["underlying_volume_24h_usd"]
        elif sort_by == "underlying_market_cap":
            value = current_market["underlying_market_cap_usd"]
        else:
            raise ValueError(f"Unsupported token card sort field: {sort_by}")

        if value is None:
            return None

        return Decimal.from_float(MarketStore._parse_timestamp(value).timestamp()) if sort_by == "expiry" else parse_decimal(value)

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
                    "summary": f"Latest hourly 24h-median token price is {format_usd_display(latest_snapshot['reference_price_usd'])}.",
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
                        f"Latest trade was a {latest_trade['side']} of {latest_trade['outcome'].upper()} exposure for "
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
    def _most_liquid_pair_with_price(pairs: list[DexScreenerPair]) -> DexScreenerPair | None:
        eligible_pairs = [pair for pair in pairs if pair.price_usd is not None]
        if not eligible_pairs:
            return None
        return max(eligible_pairs, key=lambda pair: pair.liquidity_usd or Decimal("0"))

    @staticmethod
    def _most_liquid_pair_with_supply(pairs: list[DexScreenerPair]) -> DexScreenerPair | None:
        eligible_pairs = [pair for pair in pairs if pair.token_supply is not None and pair.token_supply > 0]
        if not eligible_pairs:
            return None
        return max(eligible_pairs, key=lambda pair: pair.liquidity_usd or Decimal("0"))

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
        if starting_price_usd <= 0:
            raise ValueError("Market starting price must be positive.")
        min_price_usd = starting_price_usd / self._market_price_range_multiple
        max_price_usd = starting_price_usd * self._market_price_range_multiple

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
                min_price_usd,
                max_price_usd,
                is_frontend_visible,
                superseded_by_market_id,
                superseded_at,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 'awaiting_liquidity', NULL, ?, NULL, NULL, ?, ?, ?, ?, NULL, NULL, ?, ?)
            RETURNING id
            """,
            [
                token_mint,
                next_sequence_number,
                seed_market_question(symbol),
                expiry,
                format_decimal(starting_price_usd),
                format_decimal(min_price_usd),
                format_decimal(max_price_usd),
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
        self._transfer_rollover_liquidity(connection, old_market_id=market_row["id"], new_market_id=successor_market_id, updated_at=created_at)
        connection.execute(
            """
            UPDATE markets
            SET is_frontend_visible = 0, superseded_by_market_id = ?, superseded_at = ?, updated_at = ?
            WHERE id = ?
            """,
            [successor_market_id, created_at, created_at, market_row["id"]],
        )
        return successor_market_id

    def _snapshot_touches_rollover_boundary(self, reference_price: Decimal, market_row: sqlite3.Row) -> bool:
        long_rate = self._long_rate_for_price(reference_price, market_row)
        return long_rate >= self._market_rollover_boundary_rate or long_rate <= ONE - self._market_rollover_boundary_rate

    @staticmethod
    def _long_rate_for_price(price_usd: Decimal, market_row: sqlite3.Row) -> Decimal:
        min_price = parse_decimal(market_row["min_price_usd"])
        max_price = parse_decimal(market_row["max_price_usd"])
        if min_price <= 0 or max_price <= min_price:
            raise ValueError("Market scalar price bounds are invalid.")
        if price_usd <= min_price:
            return Decimal("0")
        if price_usd >= max_price:
            return ONE
        return (price_usd.ln() - min_price.ln()) / (max_price.ln() - min_price.ln())

    @staticmethod
    def _implied_price_usd(long_price_value: Decimal, market_row: sqlite3.Row) -> Decimal:
        min_price = parse_decimal(market_row["min_price_usd"])
        max_price = parse_decimal(market_row["max_price_usd"])
        if min_price <= 0 or max_price <= min_price:
            raise ValueError("Market scalar price bounds are invalid.")
        bounded_long_price = min(ONE, max(Decimal("0"), long_price_value))
        return (min_price.ln() + bounded_long_price * (max_price.ln() - min_price.ln())).exp()

    def _transfer_rollover_liquidity(
        self,
        connection: sqlite3.Connection,
        *,
        old_market_id: int,
        new_market_id: int,
        updated_at: str,
    ) -> None:
        old_pool = self._load_pool(connection, old_market_id, required=False)
        if old_pool is None or self._market_rollover_liquidity_transfer_fraction == 0:
            return

        # Only matched LONG/SHORT units are complete neutral AMM-owned sets; one-sided excess stays with the old market.
        transfer_atomic = int(
            (
                Decimal(min(old_pool.long_reserve_atomic, old_pool.short_reserve_atomic))
                * self._market_rollover_liquidity_transfer_fraction
            ).to_integral_value(rounding=ROUND_DOWN)
        )
        if transfer_atomic <= 0:
            return
        if transfer_atomic > old_pool.cash_backing_atomic or transfer_atomic > old_pool.total_liquidity_atomic:
            raise RuntimeError(f"Market {old_market_id} cannot transfer more liquidity than it owns.")

        preserved_old_long_price = long_price(old_pool)
        old_long_reserve = old_pool.long_reserve_atomic - transfer_atomic
        old_short_reserve = old_pool.short_reserve_atomic - transfer_atomic
        old_long_weight, old_short_weight = weights_for_price(
            long_reserve_atomic=old_long_reserve,
            short_reserve_atomic=old_short_reserve,
            long_price=preserved_old_long_price,
        )
        self._update_pool(
            connection,
            old_market_id,
            WeightedPoolState(
                long_reserve_atomic=old_long_reserve,
                short_reserve_atomic=old_short_reserve,
                long_weight=old_long_weight,
                short_weight=old_short_weight,
                cash_backing_atomic=old_pool.cash_backing_atomic - transfer_atomic,
                total_liquidity_atomic=old_pool.total_liquidity_atomic - transfer_atomic,
            ),
            updated_at,
        )

        connection.execute(
            """
            INSERT INTO market_pools (
                market_id,
                long_reserve_atomic,
                short_reserve_atomic,
                long_weight,
                short_weight,
                cash_backing_atomic,
                total_liquidity_atomic,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, '0.5', '0.5', ?, ?, ?, ?)
            """,
            [new_market_id, transfer_atomic, transfer_atomic, transfer_atomic, transfer_atomic, updated_at, updated_at],
        )
        connection.execute(
            """
            UPDATE markets
            SET state = 'open', market_start = ?, updated_at = ?
            WHERE id = ?
            """,
            [updated_at, updated_at, new_market_id],
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
    def _market_chart_rows(connection: sqlite3.Connection, market_ids: list[int]) -> list[sqlite3.Row]:
        placeholders = ", ".join("?" for _ in market_ids)
        return connection.execute(
            f"""
            SELECT captured_at, underlying_price_usd, implied_price_usd
            FROM market_chart_snapshots
            WHERE market_id IN ({placeholders})
            ORDER BY captured_at ASC, market_id ASC
            """,
            market_ids,
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
            long_reserve_atomic=row["long_reserve_atomic"],
            short_reserve_atomic=row["short_reserve_atomic"],
            long_weight=parse_decimal(row["long_weight"]),
            short_weight=parse_decimal(row["short_weight"]),
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
                    long_reserve_atomic,
                    short_reserve_atomic,
                    long_weight,
                    short_weight,
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

        preserved_long_price = long_price(pool)
        updated_long_weight, updated_short_weight = retuned_weights_for_equal_liquidity(
            long_reserve_atomic=pool.long_reserve_atomic,
            short_reserve_atomic=pool.short_reserve_atomic,
            equal_liquidity_atomic=amount_atomic,
            preserved_long_price=preserved_long_price,
        )
        connection.execute(
            """
            UPDATE market_pools
            SET
                long_reserve_atomic = long_reserve_atomic + ?,
                short_reserve_atomic = short_reserve_atomic + ?,
                long_weight = ?,
                short_weight = ?,
                cash_backing_atomic = cash_backing_atomic + ?,
                total_liquidity_atomic = total_liquidity_atomic + ?,
                updated_at = ?
            WHERE market_id = ?
            """,
            [
                amount_atomic,
                amount_atomic,
                str(updated_long_weight),
                str(updated_short_weight),
                amount_atomic,
                amount_atomic,
                credited_at,
                market_id,
            ],
        )
        return preserved_long_price

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
                long_reserve_atomic = ?,
                short_reserve_atomic = ?,
                long_weight = ?,
                short_weight = ?,
                cash_backing_atomic = ?,
                total_liquidity_atomic = ?,
                updated_at = ?
            WHERE market_id = ?
            """,
            [
                pool.long_reserve_atomic,
                pool.short_reserve_atomic,
                str(pool.long_weight),
                str(pool.short_weight),
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
                long_shares_atomic,
                short_shares_atomic,
                created_at,
                updated_at
            )
            VALUES (?, ?, 0, 0, ?, ?)
            ON CONFLICT(user_id, market_id) DO NOTHING
            """,
            [user_id, market_id, timestamp, timestamp],
        )
        column = "long_shares_atomic" if outcome == "long" else "short_shares_atomic"
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
        column = "long_shares_atomic" if outcome == "long" else "short_shares_atomic"
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
    def _ensure_table_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_definition: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
        if column_name in columns:
            return
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    @staticmethod
    def _has_legacy_binary_market_schema(connection: sqlite3.Connection) -> bool:
        table_columns = {
            table_name: {row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
            for table_name in ("market_pools", "market_positions", "market_trades", "market_chart_snapshots", "markets")
        }
        return (
            "yes_reserve_atomic" in table_columns["market_pools"]
            or "yes_shares_atomic" in table_columns["market_positions"]
            or "before_yes_price" in table_columns["market_trades"]
            or "chance_of_outcome_percent" in table_columns["market_chart_snapshots"]
            or (
                ("threshold_price_usd" in table_columns["markets"] or "min_price_usd" not in table_columns["markets"])
                and MarketStore._table_exists(connection, "markets")
                and connection.execute("SELECT 1 FROM markets LIMIT 1").fetchone() is not None
                and MarketStore._metadata_value(connection, "scalar_long_short_reset") is None
            )
        )

    @staticmethod
    def _reset_legacy_binary_market_state(connection: sqlite3.Connection) -> None:
        logger.warning("Resetting legacy binary market state before scalar LONG/SHORT schema initialization.")
        if MarketStore._table_exists(connection, "ledger_entries"):
            connection.execute(
                """
                DELETE FROM ledger_entries
                WHERE entry_type = 'market_payout'
                   OR entry_type LIKE 'market_buy_%'
                   OR entry_type LIKE 'market_sell_%'
                """
            )
        for table_name in (
            "market_revenue_sweeps",
            "market_payouts",
            "market_chart_snapshots",
            "market_snapshots",
            "market_pair_snapshots",
            "market_trades",
            "market_positions",
            "market_liquidity_seed_events",
            "market_liquidity_deposits",
            "market_liquidity_accounts",
            "market_pools",
            "token_metrics_snapshots",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table_name}")
        if MarketStore._table_exists(connection, "treasury_debt_entries"):
            connection.execute("DELETE FROM treasury_debt_entries WHERE market_id IS NOT NULL OR entry_type = 'weekly_market_seed'")
        connection.execute("DELETE FROM markets")
        connection.execute(
            """
            INSERT INTO app_metadata (key, value)
            VALUES ('scalar_long_short_reset', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [utc_now()],
        )

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                [table_name],
            ).fetchone()
            is not None
        )

    @staticmethod
    def _metadata_value(connection: sqlite3.Connection, key: str) -> str | None:
        if not MarketStore._table_exists(connection, "app_metadata"):
            return None
        row = connection.execute("SELECT value FROM app_metadata WHERE key = ?", [key]).fetchone()
        return None if row is None else row["value"]

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
