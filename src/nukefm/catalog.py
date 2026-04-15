from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .bags import BagsToken


ACTIVE_MARKET_STATES = {"awaiting_liquidity", "open", "halted"}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def market_question(symbol: str) -> str:
    return f"Will {symbol} nuke by 90 days after this market opens?"


class Catalog:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(token_mint, sequence_number)
                );
                """
            )

    def ingest_tokens(self, tokens: list[BagsToken]) -> int:
        ingested_count = 0
        with self._connect() as connection:
            for token in tokens:
                self._upsert_token(connection, token)
                self._ensure_current_market(connection, token)
                ingested_count += 1
        return ingested_count

    def list_token_cards(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    tokens.mint,
                    tokens.symbol,
                    tokens.name,
                    tokens.image_url,
                    tokens.launched_at,
                    markets.id,
                    markets.sequence_number,
                    markets.question,
                    markets.state,
                    markets.market_start,
                    markets.expiry,
                    markets.liquidity_deposit_address,
                    markets.resolved_at,
                    markets.created_at
                FROM tokens
                JOIN markets
                  ON markets.token_mint = tokens.mint
                WHERE markets.id = (
                    SELECT id
                    FROM markets AS current_market
                    WHERE current_market.token_mint = tokens.mint
                      AND current_market.state IN ('awaiting_liquidity', 'open', 'halted')
                    ORDER BY current_market.sequence_number DESC
                    LIMIT 1
                )
                ORDER BY COALESCE(tokens.launched_at, tokens.created_at) DESC, tokens.symbol ASC
                """
            ).fetchall()

        return [self._serialize_token_card(row) for row in rows]

    def get_token_detail(self, mint: str) -> dict | None:
        with self._connect() as connection:
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

        if current_market is None:
            return None

        return {
            "mint": token_row["mint"],
            "symbol": token_row["symbol"],
            "name": token_row["name"],
            "image_url": token_row["image_url"],
            "launched_at": token_row["launched_at"],
            "creator": token_row["creator"],
            "current_market": self._serialize_market(current_market),
            "past_markets": [self._serialize_market(row) for row in past_markets],
            "recent_activity": self._recent_activity(self._serialize_market(current_market), token_row["updated_at"]),
        }

    def resolve_market(self, market_id: int, outcome_state: str, resolved_at: str | None = None) -> None:
        if outcome_state not in {"resolved_yes", "resolved_no", "void"}:
            raise ValueError(f"Invalid resolved market state: {outcome_state}")

        resolved_timestamp = resolved_at or utc_now()
        with self._connect() as connection:
            market = connection.execute(
                "SELECT * FROM markets WHERE id = ?",
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
            token = connection.execute(
                "SELECT mint, symbol FROM tokens WHERE mint = ?",
                [market["token_mint"]],
            ).fetchone()
            self._ensure_current_market(
                connection,
                BagsToken(
                    mint=token["mint"],
                    name="",
                    symbol=token["symbol"],
                    image_url=None,
                    launched_at=None,
                    creator=None,
                ),
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

    def _ensure_current_market(self, connection: sqlite3.Connection, token: BagsToken) -> None:
        active_market = connection.execute(
            """
            SELECT id
            FROM markets
            WHERE token_mint = ?
              AND state IN ('awaiting_liquidity', 'open', 'halted')
            ORDER BY sequence_number DESC
            LIMIT 1
            """,
            [token.mint],
        ).fetchone()
        if active_market is not None:
            return

        next_sequence_number = connection.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence_number FROM markets WHERE token_mint = ?",
            [token.mint],
        ).fetchone()["next_sequence_number"]
        timestamp = utc_now()
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
            VALUES (?, ?, ?, 'awaiting_liquidity', NULL, NULL, NULL, NULL, ?, ?)
            """,
            [token.mint, next_sequence_number, market_question(token.symbol), timestamp, timestamp],
        )

    def _serialize_token_card(self, row: sqlite3.Row) -> dict:
        return {
            "mint": row["mint"],
            "symbol": row["symbol"],
            "name": row["name"],
            "image_url": row["image_url"],
            "launched_at": row["launched_at"],
            "current_market": self._serialize_market(row),
        }

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
            "yes_price_usd": None,
            "no_price_usd": None,
            "reference_price_usd": None,
            "ath_price_usd": None,
            "ath_timestamp": None,
            "drawdown_fraction": None,
            "threshold_price_usd": None,
        }

    @staticmethod
    def _recent_activity(current_market: dict, token_updated_at: str) -> list[dict]:
        activity = [
            {
                "timestamp": current_market["created_at"],
                "summary": f"Series {current_market['sequence_number']} created in {current_market['state']}.",
            }
        ]
        if current_market["state"] == "awaiting_liquidity":
            activity.append(
                {
                    "timestamp": token_updated_at,
                    "summary": "Waiting for the first market liquidity deposit before the 90 day window starts.",
                }
            )
        return activity

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()
