from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .amounts import format_usdc_amount
from .database import connect_database, utc_now


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: int
    wallet_address: str
    api_key_id: int


class AccountStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        with connect_database(self._database_path) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_address TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_challenges (
                    id TEXT PRIMARY KEY,
                    wallet_address TEXT NOT NULL,
                    challenge_message TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS user_deposit_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    owner_wallet_address TEXT NOT NULL UNIQUE,
                    token_account_address TEXT NOT NULL UNIQUE,
                    observed_balance_atomic INTEGER NOT NULL DEFAULT 0,
                    ata_initialized_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    entry_type TEXT NOT NULL,
                    amount_atomic INTEGER NOT NULL,
                    reference_type TEXT NOT NULL,
                    reference_id TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_deposits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    deposit_account_id INTEGER NOT NULL REFERENCES user_deposit_accounts(id) ON DELETE CASCADE,
                    amount_atomic INTEGER NOT NULL,
                    observed_balance_after_atomic INTEGER NOT NULL,
                    credited_at TEXT NOT NULL,
                    UNIQUE(user_id, observed_balance_after_atomic)
                );

                CREATE TABLE IF NOT EXISTS user_withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    destination_wallet_address TEXT NOT NULL,
                    destination_token_account_address TEXT,
                    amount_atomic INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    broadcast_signature TEXT,
                    broadcast_at TEXT,
                    completed_at TEXT,
                    failed_at TEXT,
                    failure_reason TEXT,
                    hold_ledger_entry_id INTEGER NOT NULL REFERENCES ledger_entries(id),
                    release_ledger_entry_id INTEGER REFERENCES ledger_entries(id)
                );
                """
            )

    def issue_challenge(self, wallet_address: str, challenge_message: str, expires_at: str) -> dict:
        challenge_id = secrets.token_urlsafe(24)
        created_at = utc_now()
        with connect_database(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO api_challenges (
                    id,
                    wallet_address,
                    challenge_message,
                    expires_at,
                    consumed_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, NULL, ?)
                """,
                [challenge_id, wallet_address, challenge_message, expires_at, created_at],
            )

        return {
            "challenge_id": challenge_id,
            "wallet_address": wallet_address,
            "challenge_message": challenge_message,
            "expires_at": expires_at,
        }

    def get_challenge(self, challenge_id: str) -> sqlite3.Row | None:
        with connect_database(self._database_path) as connection:
            return connection.execute(
                """
                SELECT id, wallet_address, challenge_message, expires_at, consumed_at, created_at
                FROM api_challenges
                WHERE id = ?
                """,
                [challenge_id],
            ).fetchone()

    def consume_challenge(self, challenge_id: str, consumed_at: str) -> None:
        with connect_database(self._database_path) as connection:
            connection.execute(
                "UPDATE api_challenges SET consumed_at = ? WHERE id = ?",
                [consumed_at, challenge_id],
            )

    def ensure_user(self, wallet_address: str) -> sqlite3.Row:
        timestamp = utc_now()
        with connect_database(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO users (wallet_address, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET updated_at = excluded.updated_at
                """,
                [wallet_address, timestamp, timestamp],
            )
            return connection.execute(
                "SELECT id, wallet_address, created_at, updated_at FROM users WHERE wallet_address = ?",
                [wallet_address],
            ).fetchone()

    def issue_api_key(self, user_id: int) -> dict:
        raw_secret = secrets.token_urlsafe(32)
        raw_api_key = f"nfm_{raw_secret}"
        key_hash = hashlib.sha256(raw_api_key.encode("utf-8")).hexdigest()
        created_at = utc_now()

        with connect_database(self._database_path) as connection:
            api_key_id = connection.execute(
                """
                INSERT INTO api_keys (user_id, key_prefix, key_hash, created_at, revoked_at)
                VALUES (?, ?, ?, ?, NULL)
                RETURNING id
                """,
                [user_id, raw_api_key[:12], key_hash, created_at],
            ).fetchone()["id"]

        return {
            "api_key_id": api_key_id,
            "api_key": raw_api_key,
            "created_at": created_at,
        }

    def authenticate_api_key(self, raw_api_key: str) -> AuthenticatedUser | None:
        key_hash = hashlib.sha256(raw_api_key.encode("utf-8")).hexdigest()
        with connect_database(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT
                    api_keys.id AS api_key_id,
                    users.id AS user_id,
                    users.wallet_address
                FROM api_keys
                JOIN users ON users.id = api_keys.user_id
                WHERE api_keys.key_hash = ?
                  AND api_keys.revoked_at IS NULL
                """,
                [key_hash],
            ).fetchone()
            if row is None:
                return None

            return AuthenticatedUser(
                user_id=row["user_id"],
                wallet_address=row["wallet_address"],
                api_key_id=row["api_key_id"],
            )

    def ensure_deposit_account(self, user_id: int, owner_wallet_address: str, token_account_address: str) -> dict:
        timestamp = utc_now()
        with connect_database(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO user_deposit_accounts (
                    user_id,
                    owner_wallet_address,
                    token_account_address,
                    observed_balance_atomic,
                    ata_initialized_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, 0, NULL, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    owner_wallet_address = excluded.owner_wallet_address,
                    token_account_address = excluded.token_account_address,
                    updated_at = excluded.updated_at
                """,
                [user_id, owner_wallet_address, token_account_address, timestamp, timestamp],
            )
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    owner_wallet_address,
                    token_account_address,
                    observed_balance_atomic,
                    ata_initialized_at,
                    created_at,
                    updated_at
                FROM user_deposit_accounts
                WHERE user_id = ?
                """,
                [user_id],
            ).fetchone()

        return self._serialize_deposit_account(row)

    def mark_deposit_account_initialized(self, user_id: int, initialized_at: str | None = None) -> None:
        timestamp = initialized_at or utc_now()
        with connect_database(self._database_path) as connection:
            connection.execute(
                """
                UPDATE user_deposit_accounts
                SET ata_initialized_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                [timestamp, timestamp, user_id],
            )

    def get_deposit_account(self, user_id: int) -> dict | None:
        with connect_database(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    owner_wallet_address,
                    token_account_address,
                    observed_balance_atomic,
                    ata_initialized_at,
                    created_at,
                    updated_at
                FROM user_deposit_accounts
                WHERE user_id = ?
                """,
                [user_id],
            ).fetchone()
        if row is None:
            return None
        return self._serialize_deposit_account(row)

    def list_deposit_accounts(self) -> list[dict]:
        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    owner_wallet_address,
                    token_account_address,
                    observed_balance_atomic,
                    ata_initialized_at,
                    created_at,
                    updated_at
                FROM user_deposit_accounts
                ORDER BY user_id ASC
                """
            ).fetchall()
        return [self._serialize_deposit_account(row) for row in rows]

    def record_deposit_credit(
        self,
        *,
        user_id: int,
        deposit_account_id: int,
        amount_atomic: int,
        observed_balance_after_atomic: int,
        credited_at: str,
    ) -> dict:
        with connect_database(self._database_path) as connection:
            deposit_row = connection.execute(
                """
                INSERT INTO user_deposits (
                    user_id,
                    deposit_account_id,
                    amount_atomic,
                    observed_balance_after_atomic,
                    credited_at
                )
                VALUES (?, ?, ?, ?, ?)
                RETURNING id, amount_atomic, observed_balance_after_atomic, credited_at
                """,
                [user_id, deposit_account_id, amount_atomic, observed_balance_after_atomic, credited_at],
            ).fetchone()
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
                VALUES (?, 'deposit_credit', ?, 'user_deposit', ?, 'Credited user deposit reconciliation.', ?)
                """,
                [user_id, amount_atomic, str(deposit_row["id"]), credited_at],
            )
            connection.execute(
                """
                UPDATE user_deposit_accounts
                SET observed_balance_atomic = ?, updated_at = ?
                WHERE id = ?
                """,
                [observed_balance_after_atomic, credited_at, deposit_account_id],
            )

        return {
            "deposit_id": deposit_row["id"],
            "amount_usdc": format_usdc_amount(deposit_row["amount_atomic"]),
            "credited_at": deposit_row["credited_at"],
            "observed_balance_after_usdc": format_usdc_amount(deposit_row["observed_balance_after_atomic"]),
        }

    def list_deposits(self, user_id: int) -> list[dict]:
        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    user_deposits.id,
                    user_deposits.amount_atomic,
                    user_deposits.observed_balance_after_atomic,
                    user_deposits.credited_at,
                    user_deposit_accounts.token_account_address
                FROM user_deposits
                JOIN user_deposit_accounts ON user_deposit_accounts.id = user_deposits.deposit_account_id
                WHERE user_deposits.user_id = ?
                ORDER BY user_deposits.id DESC
                """,
                [user_id],
            ).fetchall()

        return [
            {
                "deposit_id": row["id"],
                "token_account_address": row["token_account_address"],
                "amount_usdc": format_usdc_amount(row["amount_atomic"]),
                "observed_balance_after_usdc": format_usdc_amount(row["observed_balance_after_atomic"]),
                "credited_at": row["credited_at"],
            }
            for row in rows
        ]

    def create_withdrawal_request(self, user_id: int, destination_wallet_address: str, amount_atomic: int) -> dict:
        available_balance_atomic = self.get_available_balance_atomic(user_id)
        if amount_atomic > available_balance_atomic:
            raise ValueError("Withdrawal amount exceeds available balance.")

        requested_at = utc_now()
        with connect_database(self._database_path) as connection:
            hold_entry = connection.execute(
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
                VALUES (?, 'withdrawal_hold', ?, 'user_withdrawal', 'pending', 'Reserved funds for withdrawal request.', ?)
                RETURNING id
                """,
                [user_id, -amount_atomic, requested_at],
            ).fetchone()

            withdrawal = connection.execute(
                """
                INSERT INTO user_withdrawals (
                    user_id,
                    destination_wallet_address,
                    destination_token_account_address,
                    amount_atomic,
                    state,
                    requested_at,
                    broadcast_signature,
                    broadcast_at,
                    completed_at,
                    failed_at,
                    failure_reason,
                    hold_ledger_entry_id,
                    release_ledger_entry_id
                )
                VALUES (?, ?, NULL, ?, 'requested', ?, NULL, NULL, NULL, NULL, NULL, ?, NULL)
                RETURNING id, destination_wallet_address, amount_atomic, state, requested_at
                """,
                [user_id, destination_wallet_address, amount_atomic, requested_at, hold_entry["id"]],
            ).fetchone()

            connection.execute(
                """
                UPDATE ledger_entries
                SET reference_id = ?
                WHERE id = ?
                """,
                [str(withdrawal["id"]), hold_entry["id"]],
            )

        return self._serialize_withdrawal_summary(withdrawal)

    def list_withdrawals(self, user_id: int) -> list[dict]:
        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    destination_wallet_address,
                    destination_token_account_address,
                    amount_atomic,
                    state,
                    requested_at,
                    broadcast_signature,
                    broadcast_at,
                    completed_at,
                    failed_at,
                    failure_reason
                FROM user_withdrawals
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                [user_id],
            ).fetchall()
        return [self._serialize_withdrawal_row(row) for row in rows]

    def list_withdrawals_by_state(self, states: tuple[str, ...], limit: int) -> list[dict]:
        placeholders = ", ".join(["?"] * len(states))
        with connect_database(self._database_path) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    id,
                    user_id,
                    destination_wallet_address,
                    destination_token_account_address,
                    amount_atomic,
                    state,
                    requested_at,
                    broadcast_signature,
                    broadcast_at,
                    completed_at,
                    failed_at,
                    failure_reason,
                    hold_ledger_entry_id,
                    release_ledger_entry_id
                FROM user_withdrawals
                WHERE state IN ({placeholders})
                ORDER BY id ASC
                LIMIT ?
                """,
                [*states, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_withdrawal_broadcasted(
        self,
        *,
        withdrawal_id: int,
        destination_token_account_address: str,
        broadcast_signature: str,
        broadcast_at: str,
    ) -> None:
        with connect_database(self._database_path) as connection:
            connection.execute(
                """
                UPDATE user_withdrawals
                SET
                    state = 'broadcasted',
                    destination_token_account_address = ?,
                    broadcast_signature = ?,
                    broadcast_at = ?
                WHERE id = ?
                """,
                [destination_token_account_address, broadcast_signature, broadcast_at, withdrawal_id],
            )

    def mark_withdrawal_completed(self, withdrawal_id: int, completed_at: str) -> None:
        with connect_database(self._database_path) as connection:
            connection.execute(
                """
                UPDATE user_withdrawals
                SET state = 'completed', completed_at = ?
                WHERE id = ?
                """,
                [completed_at, withdrawal_id],
            )

    def mark_withdrawal_failed(self, withdrawal_id: int, failure_reason: str, failed_at: str) -> None:
        with connect_database(self._database_path) as connection:
            withdrawal = connection.execute(
                """
                SELECT user_id, amount_atomic, release_ledger_entry_id
                FROM user_withdrawals
                WHERE id = ?
                """,
                [withdrawal_id],
            ).fetchone()
            if withdrawal is None:
                raise LookupError(f"Unknown withdrawal id: {withdrawal_id}")

            release_ledger_entry_id = withdrawal["release_ledger_entry_id"]
            if release_ledger_entry_id is None:
                release_ledger_entry = connection.execute(
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
                    VALUES (?, 'withdrawal_release', ?, 'user_withdrawal', ?, 'Released failed withdrawal hold.', ?)
                    RETURNING id
                    """,
                    [withdrawal["user_id"], withdrawal["amount_atomic"], str(withdrawal_id), failed_at],
                ).fetchone()
                release_ledger_entry_id = release_ledger_entry["id"]

            connection.execute(
                """
                UPDATE user_withdrawals
                SET
                    state = 'failed',
                    failed_at = ?,
                    failure_reason = ?,
                    release_ledger_entry_id = ?
                WHERE id = ?
                """,
                [failed_at, failure_reason, release_ledger_entry_id, withdrawal_id],
            )

    def get_available_balance_atomic(self, user_id: int) -> int:
        with connect_database(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(amount_atomic), 0) AS balance_atomic
                FROM ledger_entries
                WHERE user_id = ?
                """,
                [user_id],
            ).fetchone()
        return row["balance_atomic"]

    def get_pending_withdrawal_atomic(self, user_id: int) -> int:
        with connect_database(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(amount_atomic), 0) AS pending_atomic
                FROM user_withdrawals
                WHERE user_id = ?
                  AND state IN ('requested', 'broadcasted')
                """,
                [user_id],
            ).fetchone()
        return row["pending_atomic"]

    def get_account_overview(self, user_id: int) -> dict:
        with connect_database(self._database_path) as connection:
            user = connection.execute(
                "SELECT id, wallet_address, created_at FROM users WHERE id = ?",
                [user_id],
            ).fetchone()
            if user is None:
                raise LookupError(f"Unknown user id: {user_id}")

        deposit_account = self.get_deposit_account(user_id)
        available_balance_atomic = self.get_available_balance_atomic(user_id)
        pending_withdrawal_atomic = self.get_pending_withdrawal_atomic(user_id)
        return {
            "wallet_address": user["wallet_address"],
            "account_balance_usdc": format_usdc_amount(available_balance_atomic),
            "pending_withdrawal_usdc": format_usdc_amount(pending_withdrawal_atomic),
            "deposit_address": None if deposit_account is None else deposit_account["token_account_address"],
            "deposit_owner_wallet_address": None if deposit_account is None else deposit_account["owner_wallet_address"],
            "open_positions": [],
            "trade_history": [],
        }

    @staticmethod
    def _serialize_deposit_account(row: sqlite3.Row) -> dict:
        return {
            "deposit_account_id": row["id"],
            "user_id": row["user_id"],
            "owner_wallet_address": row["owner_wallet_address"],
            "token_account_address": row["token_account_address"],
            "observed_balance_atomic": row["observed_balance_atomic"],
            "observed_balance_usdc": format_usdc_amount(row["observed_balance_atomic"]),
            "ata_initialized_at": row["ata_initialized_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _serialize_withdrawal_summary(row: sqlite3.Row) -> dict:
        return {
            "withdrawal_id": row["id"],
            "destination_wallet_address": row["destination_wallet_address"],
            "amount_usdc": format_usdc_amount(row["amount_atomic"]),
            "state": row["state"],
            "requested_at": row["requested_at"],
        }

    @staticmethod
    def _serialize_withdrawal_row(row: sqlite3.Row) -> dict:
        return {
            "withdrawal_id": row["id"],
            "destination_wallet_address": row["destination_wallet_address"],
            "destination_token_account_address": row["destination_token_account_address"],
            "amount_usdc": format_usdc_amount(row["amount_atomic"]),
            "state": row["state"],
            "requested_at": row["requested_at"],
            "broadcast_signature": row["broadcast_signature"],
            "broadcast_at": row["broadcast_at"],
            "completed_at": row["completed_at"],
            "failed_at": row["failed_at"],
            "failure_reason": row["failure_reason"],
        }
