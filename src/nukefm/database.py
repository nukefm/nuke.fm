from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

SQLITE_BUSY_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MILLISECONDS = SQLITE_BUSY_TIMEOUT_SECONDS * 1000


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@contextmanager
def connect_database(database_path: Path) -> Iterator[sqlite3.Connection]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MILLISECONDS}")
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()
