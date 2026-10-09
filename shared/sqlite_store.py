"""Small SQLite foundation used by each service-owned data store."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


class SQLiteStore:
    """Manage short-lived SQLite connections with consistent safety settings."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, statements: Sequence[str]) -> None:
        with self.connect() as connection:
            for statement in statements:
                connection.execute(statement)

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> None:
        with self.connect() as connection:
            connection.execute(sql, parameters)

    def fetch_one(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def is_healthy(self) -> bool:
        try:
            with self.connect() as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()
            return result is not None and result[0] == "ok"
        except sqlite3.Error:
            return False
