from __future__ import annotations

import sqlite3
from pathlib import Path

from .bags import BagsToken
from .database import connect_database, utc_now


ACTIVE_MARKET_STATES = {"awaiting_liquidity", "open", "halted"}


def seed_market_question(symbol: str) -> str:
    return f"Will {symbol} nuke?"


def display_market_state(state: str) -> str:
    return state.replace("_", " ")


class Catalog:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        with connect_database(self._database_path) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS tokens (
                    mint TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    image_url TEXT,
                    launched_at TEXT,
                    creator TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS markets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_mint TEXT NOT NULL REFERENCES tokens(mint) ON DELETE CASCADE,
                    sequence_number INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    state TEXT NOT NULL,
                    market_start TEXT,
                    expiry TEXT,
                    liquidity_deposit_address TEXT,
                    resolved_at TEXT,
                    starting_price_usd TEXT,
                    threshold_price_usd TEXT,
                    range_floor_price_usd TEXT,
                    range_ceiling_price_usd TEXT,
                    is_frontend_visible INTEGER NOT NULL DEFAULT 1,
                    superseded_by_market_id INTEGER REFERENCES markets(id) ON DELETE SET NULL,
                    superseded_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(token_mint, sequence_number)
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

    def ingest_tokens(self, tokens: list[BagsToken]) -> int:
        with connect_database(self._database_path) as connection:
            for token in tokens:
                self._upsert_token(connection, token)
        return len(tokens)

    def get_token_detail(self, mint: str) -> dict | None:
        with connect_database(self._database_path) as connection:
            token_row = connection.execute(
                """
                SELECT mint, symbol, name, image_url, launched_at, creator, created_at, updated_at
                FROM tokens
                WHERE mint = ?
                """,
                [mint],
            ).fetchone()
            if token_row is None:
                return None

            current_market = self._frontend_visible_market(connection, mint)
            hidden_active_markets = connection.execute(
                """
                SELECT *
                FROM markets
                WHERE token_mint = ?
                  AND is_frontend_visible = 0
                  AND state IN ('awaiting_liquidity', 'open', 'halted')
                ORDER BY sequence_number DESC
                """,
                [mint],
            ).fetchall()
            past_markets = connection.execute(
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
            "current_market": None if current_market is None else self._serialize_market(current_market),
            "hidden_active_markets": [self._serialize_market(row) for row in hidden_active_markets],
            "past_markets": [self._serialize_market(row) for row in past_markets],
            "recent_activity": []
            if current_market is None
            else self._recent_activity(self._serialize_market(current_market), token_row["updated_at"]),
        }

    def resolve_market(self, market_id: int, outcome_state: str, resolved_at: str | None = None) -> None:
        if outcome_state not in {"resolved_yes", "resolved_no", "void"}:
            raise ValueError(f"Invalid resolved market state: {outcome_state}")

        resolved_timestamp = resolved_at or utc_now()
        with connect_database(self._database_path) as connection:
            market = connection.execute(
                "SELECT state FROM markets WHERE id = ?",
                [market_id],
            ).fetchone()
            if market is None:
                raise LookupError(f"Unknown market id: {market_id}")
            if market["state"] not in ACTIVE_MARKET_STATES:
                raise ValueError(f"Cannot resolve market in state {market['state']}")

            connection.execute(
                """
                UPDATE markets
                SET state = ?, resolved_at = ?, updated_at = ?
                WHERE id = ?
                """,
                [outcome_state, resolved_timestamp, resolved_timestamp, market_id],
            )

    def _upsert_token(self, connection: sqlite3.Connection, token: BagsToken) -> None:
        timestamp = utc_now()
        connection.execute(
            """
            INSERT INTO tokens (
                mint,
                symbol,
                name,
                image_url,
                launched_at,
                creator,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mint) DO UPDATE SET
                symbol = excluded.symbol,
                name = excluded.name,
                image_url = excluded.image_url,
                launched_at = excluded.launched_at,
                creator = excluded.creator,
                updated_at = excluded.updated_at
            """,
            [
                token.mint,
                token.symbol,
                token.name,
                token.image_url,
                token.launched_at,
                token.creator,
                timestamp,
                timestamp,
            ],
        )

    @staticmethod
    def _frontend_visible_market(connection: sqlite3.Connection, mint: str) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT *
            FROM markets
            WHERE token_mint = ?
              AND is_frontend_visible = 1
              AND state IN ('awaiting_liquidity', 'open', 'halted')
            ORDER BY sequence_number DESC
            LIMIT 1
            """,
            [mint],
        ).fetchone()

    @staticmethod
    def _serialize_market(row: sqlite3.Row) -> dict:
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
            "starting_price_usd": row["starting_price_usd"],
            "threshold_price_usd": row["threshold_price_usd"],
            "range_floor_price_usd": row["range_floor_price_usd"],
            "range_ceiling_price_usd": row["range_ceiling_price_usd"],
            "is_frontend_visible": bool(row["is_frontend_visible"]),
            "superseded_by_market_id": row["superseded_by_market_id"],
            "superseded_at": row["superseded_at"],
            "yes_price_usd": None,
            "no_price_usd": None,
            "reference_price_usd": None,
            "chance_of_outcome_percent": None,
        }

    @staticmethod
    def _recent_activity(current_market: dict, token_updated_at: str) -> list[dict]:
        activity = [
            {
                "timestamp": current_market["created_at"],
                "summary": f"Series {current_market['sequence_number']} created in {display_market_state(current_market['state'])}.",
            }
        ]
        if current_market["state"] == "awaiting_liquidity":
            activity.append(
                {
                    "timestamp": token_updated_at,
                    "summary": "Waiting for the first market liquidity deposit before trading opens.",
                }
            )
        return activity

    @staticmethod
    def _ensure_market_column(connection: sqlite3.Connection, column_name: str, column_definition: str) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(markets)").fetchall()
        }
        if column_name in columns:
            return
        connection.execute(f"ALTER TABLE markets ADD COLUMN {column_name} {column_definition}")
