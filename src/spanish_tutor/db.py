"""SQLite connection and schema setup for the word bank."""

import sqlite3
from pathlib import Path

from spanish_tutor.config import DB_PATH, SQL_DIR


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the database with foreign keys enforced (SQLite leaves them off by default)."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables, indexes, and views. Safe to run repeatedly."""
    conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))


def rebuild_word_bank(conn: sqlite3.Connection) -> None:
    """Recompute word_bank from the full event log (e.g. after a formula change)."""
    conn.executescript((SQL_DIR / "rebuild_word_bank.sql").read_text(encoding="utf-8"))
